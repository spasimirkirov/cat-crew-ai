"""CatCrewEngine — the single asyncio event loop workers actually run on.

Boots once, on its own dedicated thread, alongside (not instead of) the
API server's event loop:

    API (uvicorn loop)  --submit()/run_threadsafe()-->  CatCrewEngine (asyncio thread)

After this pass, callers should never call ``provider.run_agent()``
directly — every turn becomes an engine call instead, which is
thread-safe (``asyncio.run_coroutine_threadsafe`` under the hood) and
never blocks the API event loop (Master Architecture Document, section
5.1). Actually rewiring the current call sites
(``platform/runner.py::run_bot``, ``platform/chief.py``'s delegation
handlers, ``harness/subagent.py::run_subagent``) onto this engine is
tracked as a separate step — see the README's "not yet done" list; this
module is complete and independently tested without that wiring.

Deliberately minimal for this pass: it owns the loop, the thread, the
:class:`~backend.core.supervisor.RunSupervisor`, and the local-model
concurrency gate (section 5.4). It does not publish anything to Qt
signals — that event-bus-to-UI bridge is UI wiring, explicitly out of
scope here.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from pathlib import Path

from ..domain.run import AgentRun
from .supervisor import AgentRunRequest, Executor, RunSupervisor


class LocalInferenceSemaphore:
    """Gates runs bound to the one local (LM Studio) model.

    ``AGENTS.md`` already documents the real constraint: one local model
    fits in VRAM at a time. Runs on distinct cloud providers never touch
    this and execute truly in parallel; local-bound runs queue for it —
    turning "the GPU is a shared resource" into an explicit, configurable
    queue (default 1 slot) instead of an accident of call ordering.
    """

    def __init__(self, slots: int = 1) -> None:
        self._sem = asyncio.Semaphore(slots)

    async def __aenter__(self) -> "LocalInferenceSemaphore":
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._sem.release()


class CatCrewEngine:
    """Owns one asyncio event loop on a dedicated background thread.

    ``start()``/``stop()`` are idempotent and safe to call from the Qt
    thread. ``submit()`` and ``run_threadsafe()`` are the two entry points
    from outside the engine's own thread — both use
    ``asyncio.run_coroutine_threadsafe`` and are safe to call from Qt
    signal handlers.
    """

    def __init__(self, store_path: Path, *, local_inference_slots: int = 1) -> None:
        self._store_path = store_path
        self._local_inference_slots = local_inference_slots
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.supervisor: RunSupervisor | None = None
        self.local_inference: LocalInferenceSemaphore | None = None

    @property
    def running(self) -> bool:
        return self._loop is not None

    def start(self) -> None:
        if self._thread is not None:
            return  # already running — start() is idempotent
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_loop, name="catcrew-engine", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self.supervisor = RunSupervisor(self._store_path)
        self.local_inference = LocalInferenceSemaphore(self._local_inference_slots)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def stop(self) -> None:
        if self._loop is None:
            return
        loop = self._loop
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self.supervisor = None
        self.local_inference = None
        self._ready.clear()

    def run_coro_threadsafe(self, coro, *, timeout: float | None = None):
        """Thread-safe escape hatch for orchestration that ``run_threadsafe``
        doesn't cover — e.g. a caller that needs the :class:`AgentRun`
        object as well as its summary, or wants to chain a verify/heal loop
        (``core.verify.verify_and_heal``) after the initial run without a
        second engine round-trip. ``coro`` should itself use
        ``self.supervisor`` (read after ``start()``) to spawn/wait/cancel."""
        if self._loop is None:
            raise RuntimeError("CatCrewEngine.start() must be called before run_coro_threadsafe().")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def submit(self, request: AgentRunRequest, executor: Executor) -> "Future[AgentRun]":
        """Thread-safe: spawn a run from any thread (typically the Qt
        thread). Returns a ``concurrent.futures.Future`` resolving to the
        started :class:`AgentRun` (not its final result) — poll
        ``supervisor.get(run_id)`` or use ``run_threadsafe`` if you want to
        block for the summary instead."""
        if self._loop is None or self.supervisor is None:
            raise RuntimeError("CatCrewEngine.start() must be called before submit().")
        return asyncio.run_coroutine_threadsafe(self.supervisor.spawn(request, executor), self._loop)

    def run_threadsafe(self, request: AgentRunRequest, executor: Executor, *, timeout: float | None = None) -> str:
        """Blocking convenience for callers not yet updated to the async
        API: spawn a run and block the calling thread until it finishes,
        returning its summary (or an ``"ERROR: ..."`` string on failure —
        this never raises for a worker-side failure, matching the string
        contract ``run_bot()``/``execute_chief_tool`` already expose).

        This is the shape ``platform/runner.py::run_bot`` needs to become a
        thin bridge onto the engine without forcing every caller to become
        async in the same pass.
        """
        if self._loop is None or self.supervisor is None:
            raise RuntimeError("CatCrewEngine.start() must be called before run_threadsafe().")
        supervisor = self.supervisor

        async def _spawn_and_wait() -> str:
            run = await supervisor.spawn(request, executor)
            finished = await supervisor.wait(run.id)
            if finished.status.value == "done":
                return finished.summary
            return f"ERROR: {finished.error or finished.status.value}"

        future = asyncio.run_coroutine_threadsafe(_spawn_and_wait(), self._loop)
        return future.result(timeout=timeout)
