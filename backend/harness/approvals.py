"""Tool approval policy — destructive / connector writes can require a human.

Interactive pause UI lands with the activity timeline; for now the gate is a
ContextVar hook the chat worker can set. Without a hook, configured policy still
emits ``approval_required`` events when a match would have paused.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

from backend.harness import tool_meta
from backend.services.settings_store import agent_settings, load_settings, save_settings

ApprovalDecision = str  # "allow" | "deny" | "allow_always"
ApprovalHook = Callable[[str, dict], ApprovalDecision]

_hook: ContextVar[ApprovalHook | None] = ContextVar("catcrew_approval_hook", default=None)

# Tools that need approval when the setting is on: every tool that acts (files, shell, code, git
# history, databases), from the one classification table (harness/tool_meta.py). An unknown tool
# counts as one that acts, so a new or marketplace tool cannot slip past the gate unclassified.
DEFAULT_DESTRUCTIVE = tool_meta.names(tool_meta.ACT)


def set_approval_hook(hook: ApprovalHook | None):
    return _hook.set(hook)


def reset_approval_hook(token) -> None:
    _hook.reset(token)


def current_approval_hook() -> ApprovalHook | None:
    """The hook bound on this thread. Like the provider and the abort flag it does not follow a call
    onto another thread or event loop, so a caller that hands work to the delegation engine must
    capture it first and pass it on (``platform/delegation.run_delegated``)."""
    return _hook.get()


def approval_required_enabled() -> bool:
    return bool(agent_settings().get("tool_approval", False))


def set_approval_required(enabled: bool) -> None:
    data = load_settings()
    agent = dict(data.get("agent") or {})
    agent["tool_approval"] = bool(enabled)
    data["agent"] = agent
    save_settings(data)


def needs_approval(name: str) -> bool:
    if not approval_required_enabled():
        return False
    raw = str(name or "").strip()
    if not raw:
        return False
    if raw.startswith("connector__"):
        # Connector writes — treat as sensitive until allowlists exist.
        return True
    if raw.startswith("mcp__") or raw == "mcp_call_tool":
        # A tool of an MCP server (Blender's bpy, the Unity Editor...) changes another program's state. Only tools
        # that declare themselves read-only (the MCP ``readOnlyHint``) are let through unasked.
        from backend.services import mcp_service

        try:
            return not mcp_service.get_manager().is_read_only(raw)
        except Exception:  # noqa: BLE001
            return True
    return tool_meta.meta(raw).kind == tool_meta.ACT


def gate_tool(name: str, arguments: dict | None = None) -> str | None:
    """Return a deny message, or None to proceed.

    Emits ``approval_required`` when policy matches. If no UI hook is set, allows
    the call (logged) so existing sessions keep working until the chat UI wires
    the pause bar.
    """
    if name == "mcp_call_tool":
        # The on-demand door to hidden MCP tools: judge the tool behind it, not the door.
        from backend.services import mcp_service

        try:
            name = mcp_service.get_manager().meta_target(arguments or {})
        except Exception:  # noqa: BLE001
            pass
    if not needs_approval(name):
        return None
    from .events import emit_approval_required

    emit_approval_required([name], arguments=arguments or {})
    hook = _hook.get()
    if hook is None:
        return None
    decision = (hook(name, dict(arguments or {})) or "deny").strip().lower()
    if decision in {"allow", "allow_always", "yes"}:
        return None
    return f"Denied by user approval gate: {name}"
