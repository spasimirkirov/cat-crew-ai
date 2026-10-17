"""Tool approval policy — destructive / connector writes can require a human.

Interactive pause UI lands with the activity timeline; for now the gate is a
ContextVar hook the chat worker can set. Without a hook, configured policy still
emits ``approval_required`` events when a match would have paused.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

from ..settings_store import agent_settings, load_settings, save_settings

ApprovalDecision = str  # "allow" | "deny" | "allow_always"
ApprovalHook = Callable[[str, dict], ApprovalDecision]

_hook: ContextVar[ApprovalHook | None] = ContextVar("jinx_approval_hook", default=None)

# Tools that default to needing approval when the setting is on.
DEFAULT_DESTRUCTIVE = frozenset(
    {
        "run_command",
        "run_code_mode",
        "git_push",
        "git_commit",
        "write_file",
        "replace_in_file",
        "move_path",
        "self_modify",
        "restart_jinx",
    }
)


def set_approval_hook(hook: ApprovalHook | None):
    return _hook.set(hook)


def reset_approval_hook(token) -> None:
    _hook.reset(token)


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
    return raw in DEFAULT_DESTRUCTIVE


def gate_tool(name: str, arguments: dict | None = None) -> str | None:
    """Return a deny message, or None to proceed.

    Emits ``approval_required`` when policy matches. If no UI hook is set, allows
    the call (logged) so existing sessions keep working until the chat UI wires
    the pause bar.
    """
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
