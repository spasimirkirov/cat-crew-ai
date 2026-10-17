"""Mid-turn ask-user checkpoint — pause the agent for a short answer."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

from .events import emit_ask_user

AskHook = Callable[[str, list[str]], str]

_hook: ContextVar[AskHook | None] = ContextVar("jinx_ask_user_hook", default=None)


def set_ask_user_hook(hook: AskHook | None):
    return _hook.set(hook)


def reset_ask_user_hook(token) -> None:
    _hook.reset(token)


def ask_user(prompt: str, choices: list[str] | None = None) -> str:
    """Ask the human; returns their answer or a timeout/deny message."""
    text = str(prompt or "").strip()
    if not text:
        return "Error: ask_user needs a prompt."
    opts = [str(c).strip() for c in (choices or []) if str(c).strip()]
    emit_ask_user(text, choices=opts)
    hook = _hook.get()
    if hook is None:
        return (
            "ask_user: no UI hook bound (cannot pause). "
            "Ask the user in the next chat message instead."
        )
    answer = (hook(text, opts) or "").strip()
    if not answer:
        return "User did not answer (empty or cancelled)."
    return f"User answered: {answer}"
