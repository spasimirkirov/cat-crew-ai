"""Bind the active chat provider for nested harness tools (subagent / code mode)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

_provider: ContextVar[Any] = ContextVar("jinx_runtime_provider", default=None)
_abort: ContextVar[Callable[[], bool] | None] = ContextVar(
    "jinx_runtime_abort", default=None
)
_depth: ContextVar[int] = ContextVar("jinx_runtime_depth", default=0)


def set_provider(provider: Any):
    return _provider.set(provider)


def reset_provider(token) -> None:
    _provider.reset(token)


def current_provider() -> Any:
    return _provider.get()


def set_abort(abort: Callable[[], bool] | None):
    return _abort.set(abort)


def reset_abort(token) -> None:
    _abort.reset(token)


def current_abort() -> Callable[[], bool]:
    fn = _abort.get()
    return fn or (lambda: False)


def nest_depth() -> int:
    return int(_depth.get() or 0)


def push_depth():
    return _depth.set(nest_depth() + 1)


def reset_depth(token) -> None:
    _depth.reset(token)
