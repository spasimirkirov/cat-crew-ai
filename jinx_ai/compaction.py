"""Summarize older agent turns when the context window fills."""

from __future__ import annotations

import json
import re
from typing import Any

from .config import COMPACT_CHARS, COMPACT_KEEP
from .providers.base import BaseProvider, Message

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
    "Keep it under 400 words. Quote file paths exactly."
)

_PATH_KEY = re.compile(r'["\']path["\']\s*:\s*["\']([^"\']+)["\']')
_FILEISH = re.compile(
    r"(?:[A-Za-z]:)?[^\s\"'<>|*?]+\.(?:py|pyw|js|ts|tsx|jsx|md|json|yml|yaml|toml|cs|cpp|h|rs|go|sql|html|css|txt)"
)


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
        if message.get("role") == "tool" and content.lower().startswith("error"):
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


def compact_messages(provider: BaseProvider, messages: list[Message]) -> list[Message] | None:
    """Return a shorter copy, or None if compaction is not needed / failed."""
    if estimate_chars(messages) < COMPACT_CHARS:
        return None
    if len(messages) <= COMPACT_KEEP + 1:
        return None
    system = messages[0] if messages and messages[0].get("role") == "system" else None
    body = messages[1:] if system is not None else messages
    keep_n = max(4, COMPACT_KEEP)
    if len(body) <= keep_n:
        return None
    older, recent = body[:-keep_n], body[-keep_n:]
    files = files_touched(older)
    try:
        reply = provider.chat(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": _format_for_summary(older)},
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
                "[compacted earlier conversation — treat as remembered context]\n"
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
