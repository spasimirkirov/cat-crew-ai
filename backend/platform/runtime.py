"""Context for the active project / bot during a Chief turn."""

from __future__ import annotations

from contextvars import ContextVar

_project_id: ContextVar[str] = ContextVar("catcrew_platform_project", default="")
_bot_id: ContextVar[str] = ContextVar("catcrew_platform_bot", default="")


def set_project_id(project_id: str):
    return _project_id.set(str(project_id or "").strip())


def reset_project_id(token) -> None:
    try:
        _project_id.reset(token)
    except (ValueError, LookupError):
        _project_id.set("")


def current_project_id() -> str:
    return str(_project_id.get() or "").strip()


def set_bot_id(bot_id: str):
    return _bot_id.set(str(bot_id or "").strip())


def reset_bot_id(token) -> None:
    try:
        _bot_id.reset(token)
    except (ValueError, LookupError):
        _bot_id.set("")


def current_bot_id() -> str:
    return str(_bot_id.get() or "").strip()
