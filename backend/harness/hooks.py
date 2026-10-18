"""Tool-loop lifecycle hooks: one ordered pipeline around every tool call.

``BaseProvider.run_agent`` used to inline each gate it knew about (the approval gate, Jev, the
large-result offload, the untrusted-content wrapper), so adding a policy meant editing the loop.
The loop now runs two hook chains instead, the way OpenClaw's plugin hooks work:

* ``before_tool_call(ctx)`` runs before the tool. A hook may change ``ctx.arguments``; returning a
  string blocks the call and that string becomes the tool result the model sees. The first hook
  that blocks wins; later ones do not run. A hook that raises blocks the call (fail closed: a gate
  that broke must not wave a destructive call through).
* ``after_tool_call(ctx, result)`` runs after the tool (also after a blocked call) and returns the
  result to record. A hook that raises leaves the result unchanged (fail open: these only shape
  text).

Hooks run in ``order`` (lower first). The built-ins keep the order the loop always had: approval
(10), Jev (20); offload (10), untrusted wrapper (20). A read-only call that ran in a parallel batch
(``ctx.prefetched``) has already run, so the before-chain is skipped for it, as before.

Registration is process-wide; ``register_*`` returns a function that removes the hook again.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolCallContext:
    """What a hook knows about one call. ``arguments`` may be replaced by a before-hook."""

    name: str
    arguments: dict[str, Any]
    #: The conversation as sent this round (read-only by convention).
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: The turn's goal as it stood before the loop added its own nudges (Jev judges against it).
    goal: str = ""
    #: True when the call already ran in a parallel read-only batch.
    prefetched: bool = False
    call_id: str = ""


BeforeHook = Callable[[ToolCallContext], "str | None"]
AfterHook = Callable[[ToolCallContext, str], str]


@dataclass(order=True)
class _Entry:
    order: int
    name: str
    fn: Callable[..., Any] = field(compare=False)


_lock = threading.Lock()
_before: list[_Entry] = []
_after: list[_Entry] = []


def _register(chain: list[_Entry], name: str, fn: Callable[..., Any], order: int) -> Callable[[], None]:
    entry = _Entry(int(order), str(name), fn)
    with _lock:
        chain[:] = [e for e in chain if e.name != entry.name]  # re-registering a name replaces it
        chain.append(entry)
        chain.sort()

    def unregister() -> None:
        with _lock:
            if entry in chain:
                chain.remove(entry)

    return unregister


def register_before_tool(name: str, fn: BeforeHook, *, order: int = 50) -> Callable[[], None]:
    return _register(_before, name, fn, order)


def register_after_tool(name: str, fn: AfterHook, *, order: int = 50) -> Callable[[], None]:
    return _register(_after, name, fn, order)


def hook_names() -> dict[str, list[str]]:
    with _lock:
        return {"before_tool_call": [e.name for e in _before], "after_tool_call": [e.name for e in _after]}


def run_before(ctx: ToolCallContext) -> str | None:
    """The block message of the first hook that blocks, else ``None``."""
    if ctx.prefetched:
        return None
    with _lock:
        chain = list(_before)
    for entry in chain:
        try:
            verdict = entry.fn(ctx)
        except Exception as exc:  # noqa: BLE001 - fail closed
            return f"Blocked by the {entry.name} check, which failed: {type(exc).__name__}: {exc}"
        if verdict:
            return str(verdict)
    return None


def run_after(ctx: ToolCallContext, result: str) -> str:
    with _lock:
        chain = list(_after)
    text = result if isinstance(result, str) else str(result)
    for entry in chain:
        try:
            out = entry.fn(ctx, text)
        except Exception:  # noqa: BLE001 - fail open
            continue
        if isinstance(out, str):
            text = out
    return text


# -- built-in hooks ---------------------------------------------------------------------------
# Each one looks its implementation up at call time, so patching the module function (tests,
# settings) keeps working.


def _approval(ctx: ToolCallContext) -> str | None:
    from backend.harness import approvals

    return approvals.gate_tool(ctx.name, ctx.arguments)


def _jev(ctx: ToolCallContext) -> str | None:
    from backend.middleware import jev_gatekeeper

    return jev_gatekeeper.guard_tool_call(ctx.name, ctx.arguments, messages=ctx.messages, goal=ctx.goal)


def _offload(ctx: ToolCallContext, result: str) -> str:
    from backend.harness import offload

    return offload.maybe_offload_tool_result(ctx.name, result)


def _untrusted(ctx: ToolCallContext, result: str) -> str:
    from backend.harness import untrusted

    return untrusted.mark_untrusted(ctx.name, result)


register_before_tool("approval", _approval, order=10)
register_before_tool("jev", _jev, order=20)
register_after_tool("offload", _offload, order=10)
register_after_tool("untrusted", _untrusted, order=20)
