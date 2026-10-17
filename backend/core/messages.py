"""Wire shapes for chat transcripts (see ``docs/API.md`` → ``Message``).

Session nodes (``data/sessions/*.json``) keep their historical layout
(``role``/``content``/``speaker``/``activity``…); this module is the single
place that turns them into the API's ``Message`` objects, so the REST listing
and the ``chat.message`` WebSocket event can never disagree.
"""

from __future__ import annotations

import re
from typing import Any

from backend.services.session_tree import path_to_head


def strip_wake_boilerplate(text: str) -> str:
    """Drop leftover Hey CatCrewAI / goodbye-to-sleep lines from old greetings."""
    out: list[str] = []
    for line in (text or "").splitlines():
        low = line.lower()
        if "hey catcrew" in low or "goodbye puts me back" in low or "goodbye / ok bye" in low:
            continue
        out.append(line)
    return "\n".join(out).strip()


_ROLE_HEADER = re.compile(r"^\[([^\]/\n]{1,60}) / [^\]\n]{1,60}\][ \t]*\n?")


def _strip_role_header(text: str, speaker: str) -> str:
    """Delegated results start with a ``[Name / role]`` banner. The wire message
    already carries ``speaker`` and ``bot_id``, so the banner is dropped when it
    names that same speaker."""
    m = _ROLE_HEADER.match(text or "")
    if m and speaker and m.group(1).strip().lower() == speaker.strip().lower():
        return text[m.end():].lstrip()
    return text


# The runner appends a ground-truth "Computer after this turn: …" note (plus an indented
# listing line) for the model. It is history for the model, not something to show a person.
_COMPUTER_NOTE = re.compile(r"(?:^|\n+)Computer after this turn:[^\n]*(?:\n[ \t]+[^\n]*)?\s*$")


def _strip_computer_note(text: str) -> str:
    return _COMPUTER_NOTE.sub("", text or "").rstrip()


_ANY_ROLE_HEADER = re.compile(r"^\s*\[[^\]/\n]{1,60} / [^\]\n]{1,60}\][ \t]*\n?")


def clean_summary(text: str) -> str:
    """Task summaries are model-facing hand-offs: drop the leading ``[Name / role]``
    banner and the runner's trailing "Computer after this turn" note for display."""
    out = _ANY_ROLE_HEADER.sub("", text or "", count=1)
    return _strip_computer_note(strip_wake_boilerplate(out))


def _strip_tool_block(text: str, tool_events: list[str]) -> str:
    """Room turns store ``text + "\\n\\n· step…"`` in ``content`` (that is what the
    model history has always seen). When the node also carries ``tool_events``
    the trailing block is redundant on the wire, so drop it."""
    if not tool_events:
        return text
    suffix = "\n\n" + "\n".join(f"· {line}" for line in tool_events)
    return text[: -len(suffix)] if text.endswith(suffix) else text


def node_to_message(node: dict[str, Any], *, bot_id: str = "") -> dict[str, Any]:
    role = str(node.get("role") or "assistant")
    tool_events = [str(x) for x in (node.get("tool_events") or [])]
    text = _strip_tool_block(str(node.get("content") or ""), tool_events)
    if role == "assistant":
        text = strip_wake_boilerplate(text)
        text = _strip_role_header(text, str(node.get("speaker") or ""))
        text = _strip_computer_note(text)
    msg: dict[str, Any] = {
        "id": str(node.get("id") or ""),
        "role": role,
        "text": text,
        "created_at": str(node.get("created_at") or ""),
    }
    speaker = str(node.get("speaker") or "").strip()
    if speaker:
        msg["speaker"] = speaker
    if role == "assistant":
        who = str(node.get("bot_id") or bot_id or "").strip()
        if who:
            msg["bot_id"] = who
    phase = str(node.get("phase") or "").strip()
    if phase:
        msg["phase"] = phase
    if tool_events:
        msg["tool_events"] = tool_events
    activity = node.get("activity")
    if isinstance(activity, dict) and activity.get("steps"):
        msg["activity"] = {
            "elapsed_s": float(activity.get("elapsed_s") or 0.0),
            "steps": [
                {"line": str(s.get("line") or ""), **({"preview": str(s["preview"])} if s.get("preview") else {})}
                for s in activity["steps"]
                if isinstance(s, dict)
            ],
        }
    return msg


def session_messages(session: dict[str, Any], *, limit: int = 200, bot_id: str = "") -> list[dict[str, Any]]:
    """Visible transcript (current branch, oldest first, hidden turns skipped)."""
    out: list[dict[str, Any]] = []
    for node in path_to_head(session):
        if node.get("role") not in ("user", "assistant") or node.get("hidden"):
            continue
        if not node.get("content"):
            continue
        out.append(node_to_message(node, bot_id=bot_id))
    limit = max(1, int(limit or 200))
    return out[-limit:]


def error_message(text: str, *, message_id: str = "") -> dict[str, Any]:
    import time
    from datetime import datetime, timezone

    return {
        "id": message_id or f"err_{int(time.time() * 1000)}",
        "role": "error",
        "text": text,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
