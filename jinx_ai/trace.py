"""Session tool-activity traces — JSONL + markdown you can share."""

from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BASE_DIR

TRACES_DIR = BASE_DIR / "data" / "traces"
_CLIP = 800

_session_id: ContextVar[str] = ContextVar("jinx_trace_sid", default="")


def traces_dir() -> Path:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    return TRACES_DIR


def jsonl_path(session_id: str) -> Path:
    return traces_dir() / f"{_safe_id(session_id)}.jsonl"


def markdown_path(session_id: str) -> Path:
    return traces_dir() / f"{_safe_id(session_id)}.md"


def bind_session(session_id: str | None) -> None:
    _session_id.set(str(session_id or "").strip())


def current_session_id() -> str:
    return _session_id.get()


def clip(value: Any, limit: int = _CLIP) -> Any:
    if isinstance(value, dict):
        return {str(key): clip(val, limit) for key, val in list(value.items())[:24]}
    if isinstance(value, list):
        return [clip(item, limit) for item in value[:24]]
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text)} chars]"


def log_event(kind: str, **fields: Any) -> None:
    sid = current_session_id()
    if not sid:
        return
    event = {"ts": _now(), "kind": kind, **{key: clip(val) for key, val in fields.items()}}
    path = jsonl_path(sid)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_events(session_id: str) -> list[dict[str, Any]]:
    path = jsonl_path(session_id)
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def render_markdown(session_id: str, *, title: str = "") -> str:
    events = load_events(session_id)
    heading = title.strip() or f"Jinx activity — {session_id}"
    lines = [
        f"# {heading}",
        "",
        f"Session `{session_id}`. Attach this file (or the `.jsonl` next to it) when asking about Jinx's behavior.",
        "",
    ]
    if not events:
        lines.append("_No tool activity recorded yet._")
        return "\n".join(lines) + "\n"
    turn = 0
    for event in events:
        kind = str(event.get("kind") or "")
        if kind == "turn_start":
            turn += 1
            lines.append(f"## Turn {turn}")
            lines.append("")
            if event.get("ts"):
                lines.append(f"- Time: `{event['ts']}`")
            if event.get("model"):
                lines.append(f"- Model: {event['model']}")
            if event.get("workspace"):
                lines.append(f"- Workspace: `{event['workspace']}`")
            if event.get("user"):
                lines.append("")
                lines.append("**User**")
                lines.append("")
                lines.append("```")
                lines.append(str(event.get("user") or "").strip())
                lines.append("```")
                lines.append("")
            continue
        if kind == "tool":
            name = event.get("name") or "tool"
            label = event.get("label") or name
            skipped = bool(event.get("skipped"))
            mark = "skipped" if skipped else "ran"
            lines.append(f"### `{label}` ({mark})")
            lines.append("")
            args = event.get("arguments")
            if args:
                lines.append("Arguments:")
                lines.append("")
                lines.append("```json")
                lines.append(_pretty(args))
                lines.append("```")
                lines.append("")
            result = str(event.get("result") or "").strip()
            if result:
                lines.append("Result:")
                lines.append("")
                lines.append("```")
                lines.append(result)
                lines.append("```")
                lines.append("")
            continue
        if kind == "tool_call":
            label = event.get("label") or event.get("name") or "tool"
            lines.append(f"- Calling `{label}`")
            lines.append("")
            continue
        if kind == "tool_result":
            # Prefer legacy kind=tool rows; skip duplicate tool_result if both exist.
            continue
        if kind == "approval_required":
            tools = event.get("tools") or []
            lines.append(f"- **Approval required:** {', '.join(str(t) for t in tools)}")
            lines.append("")
            continue
        if kind == "auth_required":
            lines.append(
                f"- **Connect required:** {event.get('connector_name') or event.get('connector_id')}"
            )
            lines.append("")
            continue
        if kind == "ask_user":
            lines.append(f"- **Ask user:** {event.get('prompt')}")
            lines.append("")
            continue
        if kind == "activity":
            title = event.get("title") or "Working"
            detail = event.get("detail") or ""
            lines.append(f"- {title}" + (f" — {detail}" if detail else ""))
            lines.append("")
            continue
        if kind == "error":
            lines.append(f"**Error:** {event.get('message')}")
            lines.append("")
            continue
        if kind == "turn_end":
            if event.get("reply"):
                lines.append("**Assistant (shown in chat)**")
                lines.append("")
                lines.append(str(event.get("reply") or "").strip())
                lines.append("")
            if event.get("error"):
                lines.append(f"**Error:** {event['error']}")
                lines.append("")
            continue
        lines.append(f"- `{kind}`: {_pretty(event)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_markdown(session_id: str, *, title: str = "") -> Path:
    path = markdown_path(session_id)
    text = render_markdown(session_id, title=title)
    path.write_text(text, encoding="utf-8")
    try:
        from .session_fs import ensure_session_work_dir

        extra = ensure_session_work_dir(session_id) / "activity.md"
        extra.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return path


def export_copy(session_id: str, dest: Path, *, title: str = "") -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix.lower() == ".jsonl":
        src = jsonl_path(session_id)
        dest.write_text(src.read_text(encoding="utf-8") if src.is_file() else "", encoding="utf-8")
        return dest
    dest.write_text(render_markdown(session_id, title=title), encoding="utf-8")
    return dest


def _safe_id(session_id: str) -> str:
    raw = str(session_id or "session").strip() or "unknown"
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)[:80]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return str(value)
