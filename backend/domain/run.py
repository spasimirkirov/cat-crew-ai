"""AgentRun — a delegated worker turn as a real object, not a function call.

This is the concept that replaces recursive, in-stack delegation
(``platform/runner.py::run_bot`` calling ``provider.run_agent`` directly,
capped at ``_MAX_NEST = 1``). Every Chief turn, every 1:1 turn, and every
delegated worker turn is one ``AgentRun`` row, owned by
``backend/core/supervisor.py::RunSupervisor``. Delegation becomes "create a
child AgentRun and await it" instead of "call a function on my own stack" —
nesting depth is then a config knob on the supervisor, not a Python
recursion limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .ids import new_id, now_iso


class RunKind(str, Enum):
    CHIEF = "chief"        # the Cat Crew (orchestrator) turn; persisted value kept for old rows
    DIRECT = "direct"      # a 1:1 turn with a named bot
    DELEGATED = "delegated"  # a child run spawned by delegate_to_bot/delegate_to_team


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"       # waiting on approval / ask_user
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED})


@dataclass
class AgentRun:
    id: str
    project_id: str
    bot_id: str                       # "crew" (legacy "chief") for the Cat Crew thread
    kind: RunKind
    parent_run_id: str | None = None
    status: RunStatus = RunStatus.QUEUED
    task: str = ""
    created_at: str = field(default_factory=now_iso)
    ended_at: str | None = None
    summary: str = ""
    error: str = ""

    @classmethod
    def new(
        cls,
        *,
        project_id: str,
        bot_id: str,
        kind: RunKind,
        task: str = "",
        parent_run_id: str | None = None,
    ) -> "AgentRun":
        return cls(
            id=new_id("run"),
            project_id=project_id,
            bot_id=bot_id,
            kind=kind,
            parent_run_id=parent_run_id,
            task=task,
        )

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def mark_running(self) -> None:
        self.status = RunStatus.RUNNING

    def mark_done(self, summary: str) -> None:
        self.status = RunStatus.DONE
        self.summary = summary
        self.ended_at = now_iso()

    def mark_failed(self, error: str) -> None:
        self.status = RunStatus.FAILED
        self.error = error
        self.ended_at = now_iso()

    def mark_cancelled(self) -> None:
        self.status = RunStatus.CANCELLED
        self.ended_at = now_iso()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "bot_id": self.bot_id,
            "kind": self.kind.value,
            "parent_run_id": self.parent_run_id,
            "status": self.status.value,
            "task": self.task,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "summary": self.summary,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "AgentRun":
        return cls(
            id=str(raw["id"]),
            project_id=str(raw.get("project_id") or ""),
            bot_id=str(raw.get("bot_id") or ""),
            kind=RunKind(str(raw.get("kind") or "delegated")),
            parent_run_id=(str(raw["parent_run_id"]) if raw.get("parent_run_id") else None),
            status=RunStatus(str(raw.get("status") or "queued")),
            task=str(raw.get("task") or ""),
            created_at=str(raw.get("created_at") or now_iso()),
            ended_at=raw.get("ended_at"),
            summary=str(raw.get("summary") or ""),
            error=str(raw.get("error") or ""),
        )
