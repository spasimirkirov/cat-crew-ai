"""Unit tests for backend.core — the async execution engine.

Uses plain ``asyncio.run(...)`` inside ordinary ``def test_...`` functions
rather than pytest-asyncio (not a project dependency) — the async bodies
are wrapped in a small ``main()`` coroutine per test.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.core import (
    AgentContext,
    AgentRunRequest,
    CatCrewEngine,
    RunNotFound,
    RunSupervisor,
    verify_and_heal,
)
from backend.domain.run import AgentRun, RunKind, RunStatus


def test_agent_context_defaults_are_independent() -> None:
    run = AgentRun.new(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="x")
    a = AgentContext(run=run)
    b = AgentContext(run=run)
    assert a.abort is not b.abort  # each context gets its own Event, not a shared default
    assert not a.is_aborted()
    a.abort.set()
    assert a.is_aborted()
    assert not b.is_aborted()


def test_supervisor_spawn_and_wait_success(tmp_path) -> None:
    async def main() -> None:
        supervisor = RunSupervisor(tmp_path / "registry.json")

        async def executor(ctx: AgentContext) -> str:
            return f"did: {ctx.run.task}"

        run = await supervisor.spawn(
            AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="build the header"),
            executor,
        )
        assert run.status == RunStatus.QUEUED
        finished = await supervisor.wait(run.id)
        assert finished.status == RunStatus.DONE
        assert finished.summary == "did: build the header"

    asyncio.run(main())


def test_supervisor_spawn_records_failure_without_raising(tmp_path) -> None:
    async def main() -> None:
        supervisor = RunSupervisor(tmp_path / "registry.json")

        async def failing_executor(ctx: AgentContext) -> str:
            raise ValueError("boom")

        run = await supervisor.spawn(
            AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="x"),
            failing_executor,
        )
        finished = await supervisor.wait(run.id)
        assert finished.status == RunStatus.FAILED
        assert "boom" in finished.error

    asyncio.run(main())


def test_supervisor_wait_unknown_run_raises() -> None:
    async def main() -> None:
        supervisor = RunSupervisor(__import__("pathlib").Path("/tmp/unused-registry.json"))
        with pytest.raises(RunNotFound):
            await supervisor.wait("run_does_not_exist")

    asyncio.run(main())


def test_supervisor_delegation_is_a_child_run_not_a_stack_frame(tmp_path) -> None:
    """The concrete claim from the architecture doc: nesting is a row in
    the runs table with a parent_run_id, not Python recursion depth — so
    two, three, many levels of delegation are all just more rows."""

    async def main() -> None:
        supervisor = RunSupervisor(tmp_path / "registry.json")

        async def executor(ctx: AgentContext) -> str:
            return "ok"

        parent = await supervisor.spawn(
            AgentRunRequest(project_id="p1", bot_id="chief", kind=RunKind.CHIEF, task="ship it"),
            executor,
        )
        child = await supervisor.spawn(
            AgentRunRequest(
                project_id="p1", bot_id="ekko", kind=RunKind.DELEGATED, task="build it", parent_run_id=parent.id
            ),
            executor,
        )
        grandchild = await supervisor.spawn(
            AgentRunRequest(
                project_id="p1", bot_id="caitlyn", kind=RunKind.DELEGATED, task="test it", parent_run_id=child.id
            ),
            executor,
        )
        await supervisor.wait(grandchild.id)
        assert [c.id for c in supervisor.children(parent.id)] == [child.id]
        assert [c.id for c in supervisor.children(child.id)] == [grandchild.id]

    asyncio.run(main())


def test_supervisor_cancel_stops_run_and_its_descendants(tmp_path) -> None:
    async def main() -> None:
        supervisor = RunSupervisor(tmp_path / "registry.json")
        started = asyncio.Event()

        async def hangs_until_cancelled(ctx: AgentContext) -> str:
            started.set()
            await asyncio.sleep(30)
            return "never gets here"

        parent = await supervisor.spawn(
            AgentRunRequest(project_id="p1", bot_id="chief", kind=RunKind.CHIEF, task="x"),
            hangs_until_cancelled,
        )
        child = await supervisor.spawn(
            AgentRunRequest(
                project_id="p1", bot_id="ekko", kind=RunKind.DELEGATED, task="y", parent_run_id=parent.id
            ),
            hangs_until_cancelled,
        )
        await started.wait()
        assert supervisor.cancel(parent.id) is True

        # let the cancellation propagate through both tasks
        for _ in range(50):
            await asyncio.sleep(0)

        assert supervisor.get(parent.id).status == RunStatus.CANCELLED
        assert supervisor.get(child.id).status == RunStatus.CANCELLED

    asyncio.run(main())


def test_verify_and_heal_passes_on_first_try(tmp_path) -> None:
    async def main() -> None:
        supervisor = RunSupervisor(tmp_path / "registry.json")

        async def executor(ctx: AgentContext) -> str:
            return "fixed"

        parent = await supervisor.spawn(
            AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="build it"),
            executor,
        )
        await supervisor.wait(parent.id)

        async def verify_cmd() -> tuple[bool, str]:
            return True, "all green"

        outcome = await verify_and_heal(
            supervisor=supervisor, parent_run=parent, executor=executor, verify_cmd=verify_cmd
        )
        assert outcome.ok is True
        assert outcome.attempts == 1
        assert supervisor.children(parent.id) == []  # no healing run was needed

    asyncio.run(main())


def test_verify_and_heal_retries_then_succeeds(tmp_path) -> None:
    async def main() -> None:
        supervisor = RunSupervisor(tmp_path / "registry.json")
        calls = {"n": 0}

        async def executor(ctx: AgentContext) -> str:
            return "patched"

        parent = await supervisor.spawn(
            AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="build it"),
            executor,
        )
        await supervisor.wait(parent.id)

        async def verify_cmd() -> tuple[bool, str]:
            calls["n"] += 1
            if calls["n"] < 2:
                return False, "TypeError: undefined is not a function"
            return True, "all green"

        outcome = await verify_and_heal(
            supervisor=supervisor, parent_run=parent, executor=executor, verify_cmd=verify_cmd, max_attempts=3
        )
        assert outcome.ok is True
        assert outcome.attempts == 2
        healing_children = supervisor.children(parent.id)
        assert len(healing_children) == 1
        assert "TypeError" in healing_children[0].task

    asyncio.run(main())


def test_verify_and_heal_gives_up_after_max_attempts(tmp_path) -> None:
    async def main() -> None:
        supervisor = RunSupervisor(tmp_path / "registry.json")

        async def executor(ctx: AgentContext) -> str:
            return "still broken"

        parent = await supervisor.spawn(
            AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="build it"),
            executor,
        )
        await supervisor.wait(parent.id)

        async def always_fails() -> tuple[bool, str]:
            return False, "permanently on fire"

        outcome = await verify_and_heal(
            supervisor=supervisor, parent_run=parent, executor=executor, verify_cmd=always_fails, max_attempts=3
        )
        assert outcome.ok is False
        assert outcome.attempts == 3
        assert len(supervisor.children(parent.id)) == 2  # two healing attempts spawned before giving up

    asyncio.run(main())


def test_engine_run_coro_threadsafe_supports_custom_orchestration(tmp_path) -> None:
    """This is the seam platform/chief.py uses to spawn a run and then, for
    a developer bot, chain a verify_and_heal call in the same engine
    session without a second start()/stop() round-trip."""
    engine = CatCrewEngine(tmp_path / "registry.json")
    engine.start()
    try:

        async def executor(ctx: AgentContext) -> str:
            return "built it"

        async def orchestrate() -> tuple[str, int]:
            supervisor = engine.supervisor
            run = await supervisor.spawn(
                AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="x"), executor
            )
            finished = await supervisor.wait(run.id)
            return finished.summary, len(supervisor.children(run.id))

        summary, child_count = engine.run_coro_threadsafe(orchestrate(), timeout=5)
        assert summary == "built it"
        assert child_count == 0
    finally:
        engine.stop()


def test_engine_run_coro_threadsafe_before_start_raises(tmp_path) -> None:
    engine = CatCrewEngine(tmp_path / "registry.json")
    coro = asyncio.sleep(0)
    try:
        with pytest.raises(RuntimeError):
            engine.run_coro_threadsafe(coro)
    finally:
        coro.close()  # never scheduled — close explicitly to avoid a "never awaited" warning


def test_engine_run_threadsafe_bridges_a_sync_caller(tmp_path) -> None:
    """This is the exact shape platform/runner.py::run_bot needs: a
    synchronous call from the Qt thread that blocks for a summary while
    the actual work happens on the engine's own asyncio thread."""
    engine = CatCrewEngine(tmp_path / "registry.json")
    engine.start()
    try:

        async def executor(ctx: AgentContext) -> str:
            await asyncio.sleep(0)  # prove it really runs on the engine's loop
            return f"built {ctx.run.task}"

        summary = engine.run_threadsafe(
            AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="the header"),
            executor,
            timeout=5,
        )
        assert summary == "built the header"
    finally:
        engine.stop()


def test_engine_run_threadsafe_reports_failure_as_string_not_exception(tmp_path) -> None:
    engine = CatCrewEngine(tmp_path / "registry.json")
    engine.start()
    try:

        async def failing_executor(ctx: AgentContext) -> str:
            raise RuntimeError("disk on fire")

        summary = engine.run_threadsafe(
            AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="x"),
            failing_executor,
            timeout=5,
        )
        assert summary.startswith("ERROR:")
        assert "disk on fire" in summary
    finally:
        engine.stop()


def test_engine_start_and_stop_are_idempotent(tmp_path) -> None:
    engine = CatCrewEngine(tmp_path / "registry.json")
    engine.start()
    engine.start()  # no-op, must not raise or spawn a second thread
    assert engine.running
    engine.stop()
    engine.stop()  # no-op
    assert not engine.running


def test_engine_submit_before_start_raises(tmp_path) -> None:
    engine = CatCrewEngine(tmp_path / "registry.json")

    async def executor(ctx: AgentContext) -> str:
        return "x"

    with pytest.raises(RuntimeError):
        engine.submit(
            AgentRunRequest(project_id="p1", bot_id="ekko", kind=RunKind.DIRECT, task="x"), executor
        )
