"""Verify / auto-heal loop.

Generalizes ``platform/runner.py``'s ``_bridge_build`` — a single-shot
``npm run build`` heuristic with no retry — into the "auto-heals from
build errors" behavior described in the brief (Master Architecture
Document, section 5.3).

Deliberately decoupled from any specific build tool: ``verify_cmd`` is
supplied by the caller and can wrap ``npm run build``, ``pytest``,
``go test``, or anything else that reports pass/fail plus captured
output. On failure, a new child run is spawned for the *same* bot with
the failure appended as context, bounded by ``max_attempts`` — this
replaces the old one-shot heuristic with a real, bounded retry loop
instead of a single try that either works or silently doesn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..domain.run import AgentRun, RunKind
from .supervisor import AgentRunRequest, Executor, RunSupervisor

VerifyCommand = Callable[[], Awaitable[tuple[bool, str]]]


@dataclass
class VerifyOutcome:
    ok: bool
    attempts: int
    log: str = ""


async def verify_and_heal(
    *,
    supervisor: RunSupervisor,
    parent_run: AgentRun,
    executor: Executor,
    verify_cmd: VerifyCommand,
    max_attempts: int = 3,
) -> VerifyOutcome:
    """Run ``verify_cmd``; on failure, spawn a healing child run for the
    same bot with the captured output as context, then verify again — up
    to ``max_attempts`` total checks (default 3, matching the Master
    Architecture Document's ``max_verify_attempts``).
    """
    attempt = 0
    output = ""
    while attempt < max_attempts:
        attempt += 1
        passed, output = await verify_cmd()
        if passed:
            return VerifyOutcome(ok=True, attempts=attempt, log=output)
        if attempt >= max_attempts:
            break
        heal_task = (
            "The previous change failed verification. Fix the failure below, "
            "making only the changes needed to pass.\n\n--- verify output ---\n"
            f"{output[-6000:]}"
        )
        child = await supervisor.spawn(
            AgentRunRequest(
                project_id=parent_run.project_id,
                bot_id=parent_run.bot_id,
                kind=RunKind.DELEGATED,
                task=heal_task,
                parent_run_id=parent_run.id,
            ),
            executor,
        )
        await supervisor.wait(child.id)
    return VerifyOutcome(ok=False, attempts=attempt, log=output)
