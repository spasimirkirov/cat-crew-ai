"""Bridge from ``platform/chief.py``'s delegation tools onto ``core/``'s
async engine (Master Architecture Document, section 5.2).

Before this module, ``_delegate_bot``/``_delegate_team`` called
``runner.run_bot()`` directly — a synchronous, recursive function call on
the same Python stack frame as the Chief turn that triggered it. That
turn vanished the moment it returned: no record, no way to inspect "is
Ekko still working" while it ran, and nesting was bounded by
``runner.py::_MAX_NEST`` (a recursion guard, not a design choice).

``run_delegated`` replaces that with "spawn a child ``AgentRun`` on the
engine and block this thread until it finishes" — same external
contract (still a synchronous call returning a summary string, since
``chat_window.py``'s ``ChatWorker`` is not touched in this pass — see the
README), but the turn is now a persisted row in the ``runs`` table
(queryable after the fact via ``state.RunRepo``) instead of a stack frame.

For a developer bot specifically, this also swaps ``runner.py``'s old
one-shot ``_bridge_build`` check (run once, report the result, never
retry) for a bounded verify/auto-heal loop
(``core.verify.verify_and_heal``) — the concrete "auto-heals from build
errors" behavior described in the brief.

Deliberately conservative about what it changes: ``_delegate_team``'s
sequential hand-off between specialists (each bot's ``extra_context``
includes every prior bot's result) is left exactly as it was. The
architecture doc suggests running independent specialists (e.g. architect
and designer) concurrently via ``asyncio.gather`` — that is real and
supported by ``core/`` (see its tests), but applying it here would mean a
designer no longer sees the architect's notes before running, which is a
product behavior change, not just a performance one. That's flagged as a
follow-up decision for the user, not made silently here.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from ..core import AgentRunRequest, CatCrewEngine, verify_and_heal
from ..domain.run import RunKind
from ..harness.runtime import current_abort, current_provider
from . import registry as registry_module
from .models import Bot, Project, Registry
from .runner import _bridge_build, run_bot

_EXIT_CODE = re.compile(r"^exit (\d+) @", re.MULTILINE)


def _build_passed(output: str) -> bool:
    """``_bridge_build``/``run_command`` report ``"exit N @ <cwd>\\n..."``.
    No output at all means there was nothing to verify (no build script,
    or a ``dist/``/``build/`` already present) — that counts as a pass."""
    if not output:
        return True
    match = _EXIT_CODE.search(output)
    return bool(match) and match.group(1) == "0"


def run_delegated(
    bot: Bot,
    task: str,
    *,
    project: Project,
    registry: Registry,
    extra_context: str = "",
    max_rounds: int = 0,
    stall_rounds: int = 0,
    max_verify_attempts: int = 3,
    collect_tool_events: list[str] | None = None,
    provider: Any = None,
) -> str:
    """Run one specialist bot through the engine instead of a direct call.

    ``provider`` overrides the model for this one run (the SOP passes the Planning
    model for the architecture and distribution phases); ``None`` keeps the
    chat's provider.

    Captures ``current_provider()``/``current_abort()`` here, on the
    caller's own thread — they must be resolved before crossing onto the
    engine's thread, since ``ContextVar`` state does not follow a call
    across threads/event loops (see ``core/context.py``).

    ``collect_tool_events`` is passed straight through to ``run_bot`` — see
    its docstring. It is a plain list, appended to from whichever thread
    ``run_bot`` ends up running on (``asyncio.to_thread`` below); the
    Cat Crew SOP reads it back after ``await``-ing this call, which is
    a safe enough hand-off for a "what did this bot just do" log without
    needing a lock.
    """
    if provider is None:
        provider = current_provider()
    if provider is None:
        return f"Error: no chat provider bound for {bot.name}."
    abort_fn = current_abort()
    from backend.harness.approvals import current_approval_hook

    # Without this, a specialist's run_command / write_file never reached the approval gate: the
    # gate allows every call when no hook is bound, and the hook lives on the turn's own thread.
    approval_hook = current_approval_hook()
    role = (bot.role or "").strip().lower()
    is_developer = role == "developer"

    async def executor(ctx: Any) -> str:
        # A healing child run carries the failed verification output as its own task text.
        # It used to be dropped here (the closure's original task ran again, blind), so every
        # retry re-did the same work without ever seeing the failure. Keep the original task
        # for context and put the failure in front of the cat.
        run = getattr(ctx, "run", None)
        heal = str(getattr(run, "task", "") or "").strip() if getattr(run, "parent_run_id", None) else ""
        run_task = f"{task}\n\n{heal}" if heal and heal != task else task
        return await asyncio.to_thread(
            run_bot,
            bot,
            run_task,
            project=project,
            registry=registry,
            extra_context=extra_context,
            max_rounds=max_rounds,
            stall_rounds=stall_rounds,
            provider=provider,
            abort=abort_fn,
            approval_hook=approval_hook,
            auto_build_bridge=not is_developer,
            collect_tool_events=collect_tool_events,
        )

    raw_folder = (project.folders or [None])[0]
    folder = Path(raw_folder) if raw_folder else None

    async def verify_cmd() -> tuple[bool, str]:
        if folder is None:
            return True, ""
        output = await asyncio.to_thread(_bridge_build, folder)
        return _build_passed(output), output

    async def run_and_maybe_heal() -> str:
        supervisor = engine.supervisor
        request = AgentRunRequest(
            project_id=project.id, bot_id=bot.id, kind=RunKind.DELEGATED, task=task
        )
        run = await supervisor.spawn(request, executor)
        finished = await supervisor.wait(run.id)
        result = finished.summary if finished.status.value == "done" else f"ERROR: {finished.error}"
        if not is_developer:
            return result
        outcome = await verify_and_heal(
            supervisor=supervisor,
            parent_run=finished,
            executor=executor,
            verify_cmd=verify_cmd,
            max_attempts=max_verify_attempts,
        )
        if outcome.log:
            tag = "fixed after retry" if outcome.ok else f"still failing after {outcome.attempts} attempts"
            result += f"\n\nHarness bridge (auto-heal, {tag}):\n{outcome.log[:2000]}"
        return result

    # A fresh engine per call, not a cached singleton: STORE_PATH can be
    # monkeypatched between calls (tests do this per-test via tmp_path),
    # and a long-lived engine bound to a stale path would silently write
    # AgentRun rows to the wrong database. The thread/loop startup cost is
    # negligible next to an actual LLM turn.
    engine = CatCrewEngine(registry_module.STORE_PATH)
    engine.start()
    try:
        return engine.run_coro_threadsafe(run_and_maybe_heal())
    finally:
        engine.stop()
