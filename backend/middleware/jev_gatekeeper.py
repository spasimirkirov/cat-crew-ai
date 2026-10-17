"""Jev pre-execution gatekeeper: two cheap checks placed around the expensive parts of a turn, so an
expensive OpenRouter model is never called with its full tool schemas -- and a broken tool call never
runs -- without Jev getting a look first.

1. :func:`precheck_intent` runs once, before the coding model is called at all (``providers/base.py``
   ``run_agent``): it asks Jev whether the turn's request can be answered directly, with no tools and
   no file access. Jev (a System One model, see ``services/jev_client.py``) never drafts a reply --
   it only classifies -- so a confident "no tools needed" verdict does not skip the model call; it
   tells the caller to make that one call without attaching the tool schemas, which is where the
   token saving actually comes from. A wrong verdict just falls through to the normal, tool-enabled
   loop, so the worst case is one extra model call, never a wrong or missing answer.
2. :func:`guard_tool_call` runs on every tool call the model asks for that touches the filesystem, a
   shell, git or another program (the same rough set ``harness.approvals`` treats as sensitive): it
   asks Jev how confident it is that the call is safe and matches the stated goal, and blocks it
   before it runs when Jev is not confident enough. That is the repair loop this is meant to prevent
   -- a bad edit, its error, and the retry that reads and fixes it -- short-circuited before the
   first of those three model calls, not after.

Both are no-ops (return ``None``: proceed exactly as if Jev had never been asked) when the gatekeeper
is turned off, no Jev API key is configured, or the Jev call itself fails. Jev exists to save tokens
and reduce repair loops; it must never become a new reason a turn cannot proceed, so nothing here
ever raises.
"""

from __future__ import annotations

import json
from typing import Any

from backend.services import jev_metrics
from backend.services.compaction import estimate_tokens
from backend.services.jev_client import Decision, JevClient, Question

#: Fixed, not the configurable block threshold: skipping the coding model's tool schemas is a much
#: smaller step than blocking a tool call, but a wrong "no tools needed" verdict still costs an extra
#: round-trip, so it is asked for near-certainty regardless of the slider.
BYPASS_CONFIDENCE = 0.95

#: Tool names always let through unguarded: read-only / reporting calls where a wrong Jev verdict
#: would only add latency, never prevent damage.
_ALWAYS_ALLOW = frozenset({"report_task_results", "read_file", "list_dir", "search_files", "glob_files", "get_file_info"})


def _settings() -> dict[str, Any]:
    from backend.services.settings_store import jev_settings

    return jev_settings()


def enabled() -> bool:
    return bool(_settings().get("enabled"))


#: OpenRouter's Decisions API carries Jev as a model slug; TypeSafe's own API calls it plainly.
_OPENROUTER_MODEL = "typesafe/jev-latest"


def _client() -> JevClient | None:
    """Jev on the chosen backend, with that backend's key from the key catalog; ``None`` when the
    key is missing (Jev then has no say -- nothing breaks)."""
    from backend import config
    from backend.services import api_access

    backend = _settings().get("backend")
    if backend == "typesafe":
        spec = api_access.spec_for("typesafe")
        key = api_access.resolved_key("typesafe")
        if spec is None or not key:
            return None
        return JevClient(api_key=key, model=spec.default_model, base_url=spec.base_url)
    key = api_access.resolved_key("openrouter")
    if not key:
        return None
    return JevClient(
        api_key=key,
        model=str(getattr(config, "JEV_MODEL", "") or _OPENROUTER_MODEL),
        base_url=str(getattr(config, "JEV_BASE_URL", "") or ""),
    )


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    """The person's words in the newest user message, without the app's turn note (clock,
    memories, checklist: ``harness/turn_note``), which is context for the model, not the goal."""
    from backend.harness.turn_note import NOTE_HEAD

    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):  # vision turn: the text parts
                content = "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
            text = content if isinstance(content, str) else ""
            return text.split(NOTE_HEAD, 1)[0].strip()
    return ""


def turn_goal(messages: list[dict[str, Any]]) -> str:
    """The goal a tool call is judged against: the request that started this turn, read once
    before the tool loop. Later user messages in the loop are mostly the harness's own nudges
    ("Nothing has changed since you ran that exact call…"), which used to become the "goal" and
    made every following call look off-goal."""
    return _last_user_text(messages)


def _tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    return [t.get("function", {}).get("name", "") for t in (tools or []) if isinstance(t, dict)]


def _schema_tokens(tools: list[dict[str, Any]] | None) -> int:
    """A rough estimate of the tool-schema tokens a bypass avoids attaching to the model call --
    not a measured saving, just the JSON size of the schemas divided by ~4 characters/token."""
    if not tools:
        return 0
    try:
        return max(0, len(json.dumps(tools, ensure_ascii=False, default=str)) // 4)
    except Exception:  # noqa: BLE001
        return 0


def precheck_intent(messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> Decision | None:
    """Once per turn, before the coding model is given its tool schemas. Returns a ``Decision`` when
    Jev is confident this request needs no tools at all; the caller is expected to then make one
    model call without ``tools`` attached instead of skipping the model. Returns ``None`` to proceed
    normally (with tools) in every other case.

    Opt-in (``skip_tools_precheck``): a turn without its tool schemas changes the request prefix,
    which costs the provider's prompt cache more than the schemas save, and a wrong "no tools"
    verdict leaves the model unable to do the task."""
    if not enabled() or not _settings().get("skip_tools_precheck"):
        return None
    text = _last_user_text(messages).strip()
    if not text:
        return None
    client = _client()
    if client is None:
        return None
    question = Question(
        id="no_tools_needed",
        type="noul",
        instructions=(
            "Can this request be fully answered from general knowledge and the conversation so far "
            "alone -- with no file access, no shell or code execution, and no other tool call?"
        ),
        criteria={
            "true": "A question, chat message, or task answerable purely from knowledge or the conversation.",
            "false": "Needs at least one tool: reading or writing files, running code, searching, browsing, etc.",
        },
    )
    try:
        [decision] = client.decide({"request": text[:4000], "available_tools": _tool_names(tools)}, [question])
    except Exception:  # noqa: BLE001 - Jev must never fail a turn
        return None
    bypass = bool(decision.ok and decision.confidence > BYPASS_CONFIDENCE)
    jev_metrics.record_precheck(decision, bypassed=bypass, tokens=_schema_tokens(tools) if bypass else 0)
    return decision if bypass else None


def _should_guard(name: str) -> bool:
    from backend.harness.approvals import DEFAULT_DESTRUCTIVE

    if name in _ALWAYS_ALLOW:
        return False
    return name in DEFAULT_DESTRUCTIVE or name.startswith(("mcp__", "connector__")) or name == "mcp_call_tool"


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Arguments, JSON-safe and capped -- this goes into a request body sent to a third-party model,
    so it is truncated the same way a tool result is, never expanded."""
    try:
        text = json.dumps(arguments, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return {}
    if len(text) > 4000:
        return {"_truncated": True, "preview": text[:4000]}
    return arguments


def guard_tool_call(
    name: str,
    arguments: dict[str, Any],
    *,
    messages: list[dict[str, Any]] | None = None,
    goal: str | None = None,
) -> str | None:
    """``None`` = proceed. A string = block this call before it runs -- it becomes the tool's result,
    the same shape a denied approval (``harness.approvals.gate_tool``) already uses.

    Opt-in (``guard_tool_calls``), and it blocks only when Jev is confident the call is *unsafe or
    off-goal* (P(safe) at most 1 - threshold). Blocking whenever Jev was merely unsure -- it sees the
    goal and the arguments, never the files -- blocked every correct edit in the harness evals and
    turned a 4-round fix into a 20-round loop."""
    if not enabled() or not _settings().get("guard_tool_calls") or not _should_guard(name):
        return None
    client = _client()
    if client is None:
        return None
    cfg = _settings()
    try:
        threshold = float(cfg.get("confidence_threshold", 0.92))
    except (TypeError, ValueError):
        threshold = 0.92
    if goal is None:
        goal = _last_user_text(messages or [])
    question = Question(
        id="looks_safe",
        type="noul",
        instructions=(
            "Given the stated goal, is this tool call safe to run and clearly in service of that "
            "goal -- not malformed, not destructive beyond what the goal calls for, and not unrelated "
            "to what was asked?"
        ),
        criteria={
            "true": "The call matches the goal and its risk is proportionate to it.",
            "false": "The call looks malformed, overreaching, or unrelated to the stated goal.",
        },
    )
    try:
        [decision] = client.decide({"goal": goal[:2000], "tool": name, "arguments": _safe_arguments(arguments)}, [question])
    except Exception:  # noqa: BLE001 - Jev must never fail a turn
        return None
    blocked = bool(decision.ok and decision.confidence <= 1.0 - threshold)
    tokens = estimate_tokens(messages or []) if blocked else 0
    jev_metrics.record_guard(decision, blocked=blocked, tokens=tokens)
    if not blocked:
        return None
    return (
        f"Jev gatekeeper blocked `{name}` before it ran: it is {1 - decision.confidence:.0%} sure this "
        "call is unsafe or does not serve the goal. Re-check the arguments; do not repeat the same call."
    )


# -- gates on the harness's own yes/no model calls ---------------------------------------------------
#
# The places where a classifier pays for itself: the harness used to spend a whole generative model
# call to reach a yes/no. Each gate answers True ("skip that call") only when Jev is at least
# ``confidence_threshold`` sure, and None/False otherwise -- no opinion means the old behaviour.


def _gate(question: Question, state: dict[str, Any], *, skip_when: bool, saved_tokens: int) -> bool:
    if not enabled():
        return False
    client = _client()
    if client is None:
        return False
    try:
        threshold = float(_settings().get("confidence_threshold", 0.92))
    except (TypeError, ValueError):
        threshold = 0.92
    try:
        [decision] = client.decide(state, [question])
    except Exception:  # noqa: BLE001 - Jev must never fail a turn
        return False
    if not decision.ok:
        return False
    p_yes = decision.confidence
    skip = p_yes >= threshold if skip_when else p_yes <= 1.0 - threshold
    jev_metrics.record_gate(decision, skipped=skip, tokens=saved_tokens if skip else 0)
    return skip


def request_is_clear(text: str) -> bool:
    """True when Jev is sure the request can go to the coding model as written, so the clarify
    rewrite (one generative call on the Chat model, ``services/clarify.py``) can be skipped."""
    body = (text or "").strip()
    if not body:
        return False
    question = Question(
        id="request_is_clear",
        type="noul",
        instructions=(
            "Is this request to a coding agent already clear and specific enough to act on exactly "
            "as written -- no typos that change the meaning, no mixed languages to translate, no "
            "missing or garbled intent?"
        ),
        criteria={
            "true": "Clear and specific as written; rewording it would add nothing.",
            "false": "Loosely typed, ambiguous, mixed-language or garbled; a rewrite would help.",
        },
    )
    # The rewrite it skips: the request plus a short spec back.
    return _gate(question, {"request": body[:3000]}, skip_when=True, saved_tokens=len(body) // 4 + 250)


def no_skill_in_task(context: str) -> bool:
    """True when Jev is sure a finished task holds nothing worth a reusable playbook, so the
    auto-skill builder's generative call (``platform/auto_skill.py``) can be skipped."""
    body = (context or "").strip()
    if not body:
        return False
    question = Question(
        id="task_has_reusable_skill",
        type="noul",
        instructions=(
            "Does this finished task show a general, reusable procedure (a repeatable way to do a "
            "kind of work) that would be worth saving as a playbook for future, different tasks?"
        ),
        criteria={
            "true": "A repeatable procedure that other tasks would reuse.",
            "false": "A one-off change specific to this project or request.",
        },
    )
    return _gate(question, {"task": body[:4000]}, skip_when=False, saved_tokens=len(body) // 4 + 600)
