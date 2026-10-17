"""Harness event vocabulary — TrueForge-inspired, Python-native.

Persists via ``trace.log_event`` so activity export and a future chat timeline
share one stream. Names differ slightly from TrueForge SSE types on purpose.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

ToolSource = Literal["builtin", "connector"]

EventKind = Literal[
    "turn_start",
    "activity",
    "tool_call",
    "tool_result",
    "approval_required",
    "auth_required",
    "ask_user",
    "turn_end",
    "error",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class ToolInfo:
    """Where a tool came from — drives activity labels (builtin vs connector)."""

    source: ToolSource = "builtin"
    name: str = ""
    connector_id: str = ""
    connector_name: str = ""

    def label(self) -> str:
        if self.source == "connector" and self.connector_name:
            tool = self.name or "tool"
            return f"{self.connector_name} · {tool}"
        return self.name or "tool"


@dataclass
class HarnessEvent:
    kind: EventKind
    id: str = field(default_factory=lambda: new_id())
    created_at: str = field(default_factory=_now)
    turn_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_trace_fields(self) -> dict[str, Any]:
        data = {
            "event_id": self.id,
            "created_at": self.created_at,
            **self.payload,
        }
        if self.turn_id:
            data["turn_id"] = self.turn_id
        return data


def emit(kind: EventKind, *, turn_id: str = "", **payload: Any) -> HarnessEvent:
    """Append a typed event to the session trace (no-op if no session bound)."""
    event = HarnessEvent(kind=kind, turn_id=turn_id, payload=dict(payload))
    try:
        from ..trace import log_event

        log_event(kind, **event.to_trace_fields())
    except Exception:  # noqa: BLE001 — never break the agent loop for logging
        pass
    return event


def emit_turn_start(**payload: Any) -> HarnessEvent:
    return emit("turn_start", **payload)


def emit_activity(title: str, detail: str = "", **payload: Any) -> HarnessEvent:
    return emit("activity", title=title, detail=detail, **payload)


def emit_tool_call(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    tool_info: ToolInfo | None = None,
    call_id: str = "",
    **payload: Any,
) -> HarnessEvent:
    info = tool_info or ToolInfo(source="builtin", name=name)
    return emit(
        "tool_call",
        name=name,
        arguments=arguments or {},
        call_id=call_id or new_id("call"),
        tool_info=asdict(info),
        label=info.label(),
        **payload,
    )


def emit_tool_result(
    name: str,
    result: str,
    *,
    call_id: str = "",
    skipped: bool = False,
    tool_info: ToolInfo | None = None,
    **payload: Any,
) -> HarnessEvent:
    info = tool_info or ToolInfo(source="builtin", name=name)
    # Keep legacy kind="tool" for existing markdown renderers / exports.
    try:
        from ..trace import log_event

        log_event(
            "tool",
            name=name,
            arguments=payload.get("arguments"),
            result=result,
            skipped=skipped,
            call_id=call_id,
            tool_info=asdict(info),
            label=info.label(),
        )
    except Exception:  # noqa: BLE001
        pass
    return emit(
        "tool_result",
        name=name,
        result=result,
        skipped=skipped,
        call_id=call_id,
        tool_info=asdict(info),
        label=info.label(),
        **{k: v for k, v in payload.items() if k != "arguments"},
    )


def emit_approval_required(
    tool_names: list[str],
    *,
    call_ids: list[str] | None = None,
    **payload: Any,
) -> HarnessEvent:
    return emit(
        "approval_required",
        tools=tool_names,
        call_ids=call_ids or [],
        **payload,
    )


def emit_auth_required(
    connector_id: str,
    connector_name: str = "",
    *,
    auth_url: str = "",
    **payload: Any,
) -> HarnessEvent:
    return emit(
        "auth_required",
        connector_id=connector_id,
        connector_name=connector_name or connector_id,
        auth_url=auth_url,
        **payload,
    )


def emit_ask_user(
    prompt: str,
    *,
    choices: list[str] | None = None,
    **payload: Any,
) -> HarnessEvent:
    return emit("ask_user", prompt=prompt, choices=choices or [], **payload)


def emit_turn_end(**payload: Any) -> HarnessEvent:
    return emit("turn_end", **payload)


def emit_error(message: str, **payload: Any) -> HarnessEvent:
    return emit("error", message=message, **payload)


def tool_info_for_name(name: str) -> ToolInfo:
    """Classify a tool name; connector tools use ``connector__{id}__{tool}``."""
    raw = str(name or "").strip()
    if raw.startswith("connector__"):
        parts = raw.split("__", 2)
        if len(parts) == 3:
            _prefix, connector_id, tool = parts
            return ToolInfo(
                source="connector",
                name=tool,
                connector_id=connector_id,
                connector_name=connector_id.replace("_", " ").title(),
            )
    return ToolInfo(source="builtin", name=raw)
