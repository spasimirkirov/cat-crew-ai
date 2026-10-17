"""RunSupervisor — owns every AgentRun's lifecycle.

This is what makes delegation "spawn a child task and await it" instead
of "call a function on your own stack" (Master Architecture Document,
section 5.2). Nesting depth stops being a Python recursion limit
(``runner.py::_MAX_NEST`` and ``subagent.py::_MAX_DEPTH``, both capped at
1 today, only ever guarding against a stack overflow) and becomes a row
in the ``runs`` table plus a live ``asyncio.Task`` — queryable,
individually cancellable, and no longer bounded by the call stack.

Deliberately decoupled from ``platform/``: the supervisor does not know
how to actually run a bot turn. Every ``spawn()`` call is handed an
``executor`` coroutine function that does that (``AgentContext -> str``).
That keeps this module unit-testable with a fake executor today, and is
exactly the seam ``platform/runner.py`` plugs into once it is rewired to
use the engine instead of calling ``provider.run_agent()`` directly
(tracked separately — see the README's "not yet done" list).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from ..domain.run import AgentRun, RunKind
from backend.db.state.repositories import RunRepo
from .context import AgentContext

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a hard import cycle
    from ..providers.base import BaseProvider

Executor = Callable[[AgentContext], Awaitable[str]]


@dataclass
class AgentRunRequest:
    """What the caller wants started — the input to :meth:`RunSupervisor.spawn`."""

    project_id: str
    bot_id: str
    kind: RunKind
    task: str = ""
    parent_run_id: str | None = None
    provider: "BaseProvider | None" = None
    tool_allowlist: frozenset[str] = field(default_factory=frozenset)
    approval_cb: Callable[[str, dict], Awaitable[str]] | None = None
    ask_user_cb: Callable[[str, list[str]], Awaitable[str]] | None = None


class RunNotFound(KeyError):
    """Raised by ``wait()`` for a run_id the supervisor never spawned."""


class RunSupervisor:
    """Tracks every in-flight AgentRun and persists its lifecycle to SQLite.

    One supervisor per running app (owned by :class:`~backend.core.engine.CatCrewEngine`),
    backed by the ``runs`` table via :class:`~backend.db.state.repositories.RunRepo`
    so "how is bot X's delegation doing" is a query, never "block until the
    whole nested call stack unwinds."
    """

    def __init__(self, store_path: Path) -> None:
        self._store_path = store_path
        self._runs: dict[str, AgentRun] = {}
        self._contexts: dict[str, AgentContext] = {}
        self._tasks: dict[str, "asyncio.Task[str]"] = {}

    def get(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    def children(self, parent_run_id: str) -> list[AgentRun]:
        return [r for r in self._runs.values() if r.parent_run_id == parent_run_id]

    async def spawn(self, request: AgentRunRequest, executor: Executor) -> AgentRun:
        """Create and start one run. Returns immediately once the run is
        recorded and its task is scheduled — use :meth:`wait` to await its
        result."""
        run = AgentRun.new(
            project_id=request.project_id,
            bot_id=request.bot_id,
            kind=request.kind,
            task=request.task,
            parent_run_id=request.parent_run_id,
        )
        ctx = AgentContext(
            run=run,
            provider=request.provider,
            tool_allowlist=request.tool_allowlist,
            approval_cb=request.approval_cb,
            ask_user_cb=request.ask_user_cb,
        )
        self._runs[run.id] = run
        self._contexts[run.id] = ctx
        RunRepo.create(self._store_path, run)
        self._tasks[run.id] = asyncio.ensure_future(self._drive(run, ctx, executor))
        return run

    async def wait(self, run_id: str) -> AgentRun:
        """Await a spawned run to completion (terminal status) and return it."""
        task = self._tasks.get(run_id)
        if task is None:
            raise RunNotFound(run_id)
        await task  # result/exception is already folded into the run's status by _drive
        return self._runs[run_id]

    def cancel(self, run_id: str) -> bool:
        """Best-effort cancellation: sets the run's abort event and cancels
        its ``asyncio.Task``, then does the same for every live descendant —
        so stopping a Chief turn takes its whole delegation tree down with
        it instead of either leaving orphans running or killing the app."""
        cancelled_any = False
        ctx = self._contexts.get(run_id)
        task = self._tasks.get(run_id)
        if ctx is not None and task is not None and not task.done():
            ctx.abort.set()
            task.cancel()
            cancelled_any = True
        for child in self.children(run_id):
            cancelled_any = self.cancel(child.id) or cancelled_any
        return cancelled_any

    async def _drive(self, run: AgentRun, ctx: AgentContext, executor: Executor) -> str:
        run.mark_running()
        RunRepo.update(self._store_path, run)
        try:
            summary = await executor(ctx)
            run.mark_done(summary)
            RunRepo.update(self._store_path, run)
            return summary
        except asyncio.CancelledError:
            run.mark_cancelled()
            RunRepo.update(self._store_path, run)
            raise
        except Exception as exc:  # noqa: BLE001 - a worker's failure must never crash the engine
            run.mark_failed(str(exc))
            RunRepo.update(self._store_path, run)
            return f"ERROR: {exc}"
