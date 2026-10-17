"""Keep the context inside the model's window: at 85 % of it, summarize the older turns.

Big contexts are what make paid models expensive, so before a conversation is sent on
(to a high-cost coding or planning model), everything except the newest ~20k tokens is
squeezed into a short summary by the *Memory* model (the cheap, long-context slot in
Models; it follows Chat when unset). ``ContextBudget`` decides when, ``compact_messages``
does the work.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from backend.config import COMPACT_KEEP, COMPACT_KEEP_TOKENS
from backend.harness.tool_result import tool_failed
from backend.providers.base import BaseProvider, Message

_SUMMARY_SYSTEM = (
    "Summarize this coding-agent transcript for a later turn. "
    "Use these headings, in this order, as plain text (no code dumps):\n"
    "## Goal\n"
    "## Constraints\n"
    "## Files touched\n"
    "## Decisions\n"
    "## Errors / dead ends\n"
    "## Status\n"
    "## Next steps\n"
    "Keep it under 400 words. Quote file paths exactly. The original assignment is kept "
    "separately, so do not restate it: focus on what is done, what is left, decisions, "
    "file paths and errors. If a 'Summary so far' block is included, update it instead of "
    "repeating it."
)

# The first user message of a run is the assignment (task, acceptance wording, task id). It is
# pinned verbatim at the top of every compacted context; folding it into a summary is how a cat
# ends up working from a paraphrase of its own job.
# Marks a cat's assignment in a shared (unified) history, where the first user message is not
# necessarily the one the active cat is working on (platform/shared_context.py).
TASK_PREFIX = "[assignment for "
ASSIGNMENT_HEAD = "[your assignment — unchanged; this is still the task you must finish]\n"
COMPACTED_MARK = "[compacted earlier conversation — treat as remembered context]\n"
_ASSIGNMENT_CAP = 8_000

_PATH_KEY = re.compile(r'["\']path["\']\s*:\s*["\']([^"\']+)["\']')
_FILEISH = re.compile(
    r"(?:[A-Za-z]:)?[^\s\"'<>|*?]+\.(?:py|pyw|js|ts|tsx|jsx|md|json|yml|yaml|toml|cs|cpp|h|rs|go|sql|html|css|txt)"
)


CHARS_PER_TOKEN = 4  # a coarse, model-independent estimate: good enough to decide "too big"


def estimate_chars(messages: list[Message]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(str(content))
        for call in message.get("tool_calls") or []:
            total += len(str(call))
    return total


def _tool_name(call: Any) -> str:
    if not isinstance(call, dict):
        return ""
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    return str(call.get("name") or fn.get("name") or "")


def _tool_args(call: Any) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {}
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    raw = call.get("arguments", fn.get("arguments"))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def files_touched(messages: list[Message]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        text = (path or "").strip().replace("\\", "/")
        if not text or text in seen or len(text) > 240:
            return
        seen.add(text)
        found.append(text)

    for message in messages:
        for call in message.get("tool_calls") or []:
            args = _tool_args(call)
            if args.get("path"):
                add(str(args["path"]))
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if message.get("role") == "tool" or "Error" in content[:80]:
            for match in _PATH_KEY.findall(content):
                add(match)
            for match in _FILEISH.findall(content[:2000]):
                if "/" in match or "\\" in match or match.endswith(".py"):
                    add(match)
    return found[:40]


def dead_ends(messages: list[Message]) -> list[str]:
    notes: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if message.get("role") == "tool" and tool_failed(str(message.get("name") or ""), content):
            notes.append(content.splitlines()[0][:200])
        elif message.get("role") == "assistant" and "error:" in content.lower()[:400]:
            notes.append(content.splitlines()[0][:200])
    return notes[:12]


def _format_for_summary(messages: list[Message]) -> str:
    files = files_touched(messages)
    errors = dead_ends(messages)
    header = []
    if files:
        header.append("Known files touched:\n" + "\n".join(f"- {path}" for path in files))
    if errors:
        header.append("Known errors / dead ends:\n" + "\n".join(f"- {item}" for item in errors))
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "unknown")
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        tools = message.get("tool_calls") or []
        if tools:
            names = [_tool_name(call) or "tool" for call in tools]
            text = (text + "\n[tools: " + ", ".join(names) + "]").strip()
        if role == "tool":
            text = (text or "")[:400]
        if not text:
            continue
        parts.append(f"{role}: {text[:1200]}")
    blob = "\n\n".join(header + parts)
    return blob[:24_000]


def estimate_tokens(messages: list[Message]) -> int:
    return -(-estimate_chars(messages) // CHARS_PER_TOKEN)


def _split_point(body: list[Message], keep: int) -> int:
    """Index where the "recent" part starts: ``keep`` messages from the end, moved earlier
    so it never begins with a tool result (which must stay next to the assistant turn
    that called the tool). ``0`` means there is nothing older worth summarizing."""
    split = len(body) - keep
    while split > 0 and body[split].get("role") == "tool":
        split -= 1
    return max(split, 0)


_MIN_KEEP = 4


def _split_point_tokens(body: list[Message], keep_tokens: int) -> int:
    """Like ``_split_point``, but the recent part is the newest messages that fit in about
    ``keep_tokens`` estimated tokens (never fewer than ``_MIN_KEEP`` messages): one huge tool
    result does not push ten turns out of view, and ten tiny ones do not leave the window idle."""
    budget = max(0, int(keep_tokens))
    used = 0
    split = len(body)
    while split > 0:
        cost = estimate_tokens([body[split - 1]])
        if len(body) - split >= _MIN_KEEP and used + cost > budget:
            break
        used += cost
        split -= 1
    while split > 0 and body[split].get("role") == "tool":
        split -= 1
    return max(split, 0)


def _split_first(content: str) -> tuple[str, str]:
    """(assignment text, earlier summary) of a run's first user message, whether or not it has
    been through a compaction already."""
    if COMPACTED_MARK in content:
        head, _, tail = content.partition(COMPACTED_MARK)
        return head.replace(ASSIGNMENT_HEAD, "").strip(), tail.strip()
    return content.strip(), ""


def compact_messages(
    provider: BaseProvider,
    messages: list[Message],
    *,
    keep: int | None = None,
    keep_tokens: int | None = None,
) -> list[Message] | None:
    """A shorter copy with the older turns replaced by ``provider``'s summary, or ``None``
    when there is nothing to summarize or the summary failed.

    ``keep_tokens`` keeps the newest messages that fit in that many estimated tokens (the
    ``ContextBudget`` path); otherwise ``keep`` (default ``COMPACT_KEEP``) messages are kept."""
    system = messages[0] if messages and messages[0].get("role") == "system" else None
    body = messages[1:] if system is not None else messages
    if keep_tokens and keep is None:
        if len(body) <= _MIN_KEEP:
            return None
        split = _split_point_tokens(body, keep_tokens)
    else:
        keep_n = max(_MIN_KEEP, keep if keep is not None else COMPACT_KEEP)
        if len(body) <= keep_n:
            return None
        split = _split_point(body, keep_n)
    if split <= 0:
        return None
    older, recent = body[:split], body[split:]
    assignment, previous = "", ""
    first = older[0]
    marked = [
        i
        for i, m in enumerate(body)
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].startswith(TASK_PREFIX)
    ]
    first_is_user = first.get("role") == "user" and isinstance(first.get("content"), str)
    offset = 0
    if first_is_user and (COMPACTED_MARK in first["content"] or not marked):
        assignment, previous = _split_first(first["content"])
        older = older[1:]
        offset = 1
    if marked and marked[-1] < split:
        # Shared history (platform/shared_context.py): pin the active cat's own assignment (the
        # last marked message), never the first cat's. Still in the recent part: nothing to pin.
        at = marked[-1] - offset
        if 0 <= at < len(older):
            assignment = body[marked[-1]]["content"].strip()
            older = [m for i, m in enumerate(older) if i != at]
    if not older and not previous:
        return None
    to_summarize = older
    if previous:
        to_summarize = [{"role": "user", "content": "Summary so far:\n" + previous}, *older]
    files = files_touched(older)
    try:
        reply = provider.chat(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": _format_for_summary(to_summarize)},
            ]
        )
    except Exception:  # noqa: BLE001
        return None
    summary = (reply.content or "").strip()
    if not summary:
        return None
    if files and "Files touched" not in summary:
        summary += "\n\n## Files touched\n" + "\n".join(f"- {path}" for path in files)
    compacted: list[Message] = []
    if system is not None:
        compacted.append(system)
    compacted.append(
        {
            "role": "user",
            "content": (
                (ASSIGNMENT_HEAD + assignment[:_ASSIGNMENT_CAP] + "\n\n" if assignment else "")
                + COMPACTED_MARK
                + summary
            ),
        }
    )
    compacted.append(
        {
            "role": "assistant",
            "content": "Understood. Continuing from the summary.",
        }
    )
    compacted.extend(recent)
    return compacted


def summarizer_provider() -> tuple[BaseProvider, str] | None:
    """The Memory-role provider and its display name; ``None`` when it cannot be built
    (a missing key, say), in which case the context is left alone."""
    try:
        from backend.core import provider_factory
        from backend.services.lm_runtime import host_label
        from backend.platform.runtime import current_project_id
        from backend.services.model_resolver import get_effective_model

        from backend.services import aux_routing

        eff = get_effective_model(current_project_id(), "memory")
        host, model = eff.host, eff.model
        # Summarizing older turns is exactly the kind of lightweight, low-stakes background call
        # auxiliary free-tier routing targets (Settings -> Models -> OpenRouter savings). A no-op
        # unless that's turned on and the Memory role is on OpenRouter.
        model = aux_routing.route(host, model)
        return provider_factory.provider_from_brain(host, model), f"{host_label(host)} · {model or 'default'}"
    except Exception:  # noqa: BLE001
        return None


class ContextBudget:
    """One per conversation loop: compresses ``messages`` in place when they reach the model's threshold.

    The threshold is not a constant: it comes from the model that will receive the conversation
    (``services/provider_limits``: DeepSeek 54,000 of 64,000, Gemini 850,000 of 1,000,000, Claude 170,000
    of 200,000, OpenAI 110,000 of 128,000, about 85 % of the window).
    The whole conversation counts, system prompt included, because that is what fills the window.

    After each attempt it waits for the context to grow by a quarter before trying again, so a
    summary that could not shrink the context (or a failing Memory model) is never retried on
    every round.
    """

    REGROW = 1.25

    def __init__(
        self,
        *,
        limit: int | None = None,
        on_compressed: "Callable[[int, int, str], None] | None" = None,
        summarizer: "Callable[[], tuple[BaseProvider, str] | None] | None" = None,
    ) -> None:
        #: an explicit threshold (tests, callers that know better); ``None`` = ask the model's provider each time
        self._forced = int(limit) if limit else None
        self.limit = self._forced or 0  # the compression threshold last used
        self.max_tokens = 0  # the model's context window last used
        self._on_compressed = on_compressed
        self._summarizer = summarizer or summarizer_provider
        self._retry_above = 0

    @staticmethod
    def keep_tokens(threshold: int) -> int:
        """Recent tokens a compaction keeps verbatim: ``COMPACT_KEEP_TOKENS`` (about 20k), at most
        half the threshold. ``0`` (the setting is off) falls back to keeping ``COMPACT_KEEP`` messages."""
        if COMPACT_KEEP_TOKENS <= 0:
            return 0
        return max(1, min(COMPACT_KEEP_TOKENS, int(threshold) // 2))

    def resolve(self, provider: Any = None) -> tuple[int, int]:
        """``(max_tokens, threshold)`` for ``provider`` (the model about to be called)."""
        if self._forced:
            self.limit = self._forced
            self.max_tokens = round(self._forced / 0.85)
            return self.max_tokens, self.limit
        from backend.services.provider_limits import limits_for_provider

        found = limits_for_provider(provider) if provider is not None else None
        if not found:
            from backend.services.provider_limits import get_model_context_limit

            found = get_model_context_limit("", "")
        self.max_tokens = int(found["max_tokens"])
        self.limit = int(found["compression_threshold"])
        return self.max_tokens, self.limit

    def apply(self, messages: list[Message], provider: Any = None) -> bool:
        """Compress in place at the threshold. Returns True when the messages were replaced."""
        try:
            _, threshold = self.resolve(provider)
            before = estimate_tokens(messages)
            if before < threshold or before <= self._retry_above:
                return False
            chosen = self._summarizer()
            compacted = (
                compact_messages(chosen[0], messages, keep_tokens=self.keep_tokens(threshold))
                if chosen
                else None
            )
            after = estimate_tokens(compacted) if compacted is not None else before
            self._retry_above = int(after * self.REGROW)
            if compacted is None or after >= before:
                return False
            messages[:] = compacted
            if self._on_compressed is not None and chosen is not None:
                self._on_compressed(before, after, chosen[1])
            return True
        except Exception:  # noqa: BLE001 - trimming context is an optimisation; it must never fail a turn
            return False
