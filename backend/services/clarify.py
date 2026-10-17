"""Rewrite messy Developer prompts into a short task spec (same chat model)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from backend.services.here import looks_like_affirmation, looks_like_weather
from backend.providers.base import BaseProvider, Message

AbortFn = Callable[[], bool]

_CYRILLIC = re.compile(r"[А-Яа-яЁёІіЇїЄє]")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁёІіЇїЄє0-9_]+")
_SKIP_FOLLOWUP = re.compile(
    r"^\s*("
    r"fix that|fix it|this one|that one|same|again|continue|retry|undo|"
    r"оправи го|оправи|ето това|това|същото|пак|продължи"
    r")\s*[.!]?\s*$",
    re.I,
)
_FENCE = re.compile(r"^```(?:\w+)?\s*|\s*```$", re.I | re.M)
_LABEL = re.compile(
    r"^(task spec|clarified task|goal|task)\s*:\s*",
    re.I,
)

CLARIFY_SYSTEM = (
    "Rewrite the user's latest developer request into a short task spec "
    "for a coding agent on Windows.\n"
    "The user may type Bulgarian, English, or a mix, with typos or incomplete logic.\n"
    "Output English only.\n"
    "Rules:\n"
    "- Preserve intent. Do not add features they did not ask for.\n"
    "- One sentence goal, then up to four bullets if the work has parts.\n"
    "- No code, no greetings, no questions, no tool-call JSON.\n"
    "- If the request is already clear, tighten it; do not pad.\n"
)


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def should_clarify(text: str, *, mode: str = "developer") -> bool:
    """Skip non-coding chats, weather, yes/ok, and short steers."""
    if (mode or "") != "developer":
        return False
    body = (text or "").strip()
    if not body:
        return False
    if looks_like_weather(body) or looks_like_affirmation(body):
        return False
    if _SKIP_FOLLOWUP.match(body):
        return False
    words = _words(body)
    if _CYRILLIC.search(body):
        return len(words) >= 3 or len(body) >= 16
    if len(words) <= 4 and len(body) < 80:
        return False
    return len(body) >= 24


def parse_clarify(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = _FENCE.sub("", text).strip()
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and _LABEL.match(lines[0].strip()):
        lines[0] = _LABEL.sub("", lines[0].strip())
        if not lines[0]:
            lines.pop(0)
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) > 800:
        cut = text[:800]
        text = cut.rsplit("\n", 1)[0].strip() or cut.strip()
    low = text.lower()
    if not text or low in {"none", "n/a", "clear", "ok"}:
        return ""
    if "```" in text or "<tool_call>" in low:
        return ""
    return text


def _clip(content: str, limit: int = 600) -> str:
    text = (content or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def clarify_task(
    provider: BaseProvider,
    user_text: str,
    history: list[Message] | None = None,
    abort: AbortFn | None = None,
) -> str:
    """One no-tools pass. Empty string means keep the original wording."""
    if abort and abort():
        return ""
    recent: list[dict[str, Any]] = []
    for msg in (history or [])[-6:]:
        role = str(msg.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        recent.append({"role": role, "content": _clip(str(msg.get("content") or ""))})
    if not recent or recent[-1].get("role") != "user":
        recent.append({"role": "user", "content": _clip(user_text)})
    else:
        recent[-1] = {"role": "user", "content": _clip(user_text)}
    messages: list[Message] = [{"role": "system", "content": CLARIFY_SYSTEM}, *recent]
    try:
        reply = provider.chat(messages, tools=None)
    except Exception:  # noqa: BLE001
        return ""
    if abort and abort():
        return ""
    return parse_clarify(reply.content or "")
