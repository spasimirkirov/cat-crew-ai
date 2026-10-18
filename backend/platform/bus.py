"""In-process event bus: the single source of truth for "something just changed".

Every writer of live state — the activity board, the task ledger
(``db/ledger.py``), the SOP engine, the run supervisor and the chat service —
announces a change here. The API layer (``backend/api/ws.py``) subscribes once
and fans each event out to every connected WebSocket client, which is how the
UI reacts in real time without polling or a page refresh.

Design notes
------------
* Pure Python, no GUI toolkit. Writers run on plain worker threads (the turn
  runner, the delegation engine thread, ``asyncio.to_thread`` workers), so
  ``publish`` is thread-safe and never blocks: subscribers are called
  synchronously on the *emitting* thread and must be cheap and non-blocking
  (the WebSocket bridge only does ``loop.call_soon_threadsafe(queue.put_nowait)``).
* A failing subscriber is isolated: it can never break a DB write or an
  agent turn.
* Events are small and carry ids, not rows. The API layer enriches them into
  the wire shapes documented in ``docs/API.md`` (it may hit SQLite, which is
  why that happens off the emitting thread).
* ``seq`` increases by one per event for the life of the process; the wire
  envelope reuses it so clients can spot gaps.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Event type names (dotted, stable: they are the WebSocket contract).
BOT_STAFFED = "bot.staffed"
BOT_RELEASED = "bot.released"
BOT_ACTIVITY = "bot.activity"
PHASE_STARTED = "phase.started"
CAT_SWAPPED = "cat.swapped"
TASK_CHANGED = "task.changed"
FILE_CHANGED = "file.changed"
CHAT_MESSAGE = "chat.message"
CHAT_ACTIVITY = "chat.activity"
TURN_STARTED = "turn.started"
TURN_FINISHED = "turn.finished"
TURN_FAILED = "turn.failed"
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_RESOLVED = "approval.resolved"
ASK_REQUESTED = "ask.requested"
ASK_RESOLVED = "ask.resolved"
SKILL_PROPOSED = "skill.proposed"
PROJECT_CHANGED = "project.changed"
CONTEXT_USAGE = "context.usage"


@dataclass(frozen=True)
class Event:
    type: str
    project_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: float = 0.0


Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        # Re-entrant: a subscriber may itself publish (e.g. a bridge that
        # turns one event into another) without deadlocking.
        self._lock = threading.RLock()
        self._subs: list[Subscriber] = []
        self._seq = itertools.count(1)
        self._last_seq = 0

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        """Register ``fn``; returns an unsubscribe callable."""
        with self._lock:
            self._subs.append(fn)

        def _off() -> None:
            self.unsubscribe(fn)

        return _off

    def unsubscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subs = [s for s in self._subs if s is not fn]

    def publish(
        self, type_: str, data: dict[str, Any] | None = None, *, project_id: str = ""
    ) -> Event:
        # Sequence assignment and delivery share one critical section so the
        # ordering guarantee ("events of one turn arrive in order") holds: two
        # threads can never deliver seq N+1 before seq N to the same subscriber.
        with self._lock:
            seq = next(self._seq)
            self._last_seq = seq
            event = Event(type_, str(project_id or ""), dict(data or {}), seq, time.time())
            for fn in list(self._subs):
                try:
                    fn(event)
                except Exception:  # noqa: BLE001 - a bad subscriber must not break writers
                    pass
        return event

    def clear(self) -> None:
        with self._lock:
            self._subs.clear()


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


def reset_bus() -> EventBus:
    """Fresh bus (tests, or an embedded re-launch)."""
    global _bus
    _bus = EventBus()
    return _bus


def publish(type_: str, data: dict[str, Any] | None = None, *, project_id: str = "") -> None:
    """Best-effort publish: never let an event hiccup break a DB write."""
    try:
        get_bus().publish(type_, data, project_id=project_id)
    except Exception:  # noqa: BLE001
        pass


# -- typed emit helpers -------------------------------------------------------------
# Kept tiny on purpose: they carry ids only. ``backend/api/events.py`` turns
# them into the full wire shapes.


def emit_bot_staffed(project_id: str, bot_id: str) -> None:
    """``bot_id`` was added to ``project_id``'s fleet (persisted roster or ledger member)."""
    publish(BOT_STAFFED, {"bot_id": str(bot_id or "")}, project_id=str(project_id or ""))


def emit_bot_released(project_id: str, bot_id: str) -> None:
    publish(BOT_RELEASED, {"bot_id": str(bot_id or "")}, project_id=str(project_id or ""))


def emit_bot_activity(bot_id: str, status: str, state: str, text: str, project_id: str = "") -> None:
    """Live per-bot state. ``status`` is already the *display* word (see
    ``activity_board.display_status``). A bot's activity is global, so the envelope has no project
    id and it is delivered to every subscriber; ``data.project_id`` says which project's turn
    wrote it ("" when unknown), so a client running two projects can tell them apart."""
    publish(
        BOT_ACTIVITY,
        {
            "bot_id": str(bot_id or ""),
            "status": str(status or "idle"),
            "state": str(state or "idle"),
            "text": str(text or ""),
            "project_id": str(project_id or ""),
        },
    )


def emit_task_changed(project_id: str, task_id: str = "") -> None:
    publish(TASK_CHANGED, {"task_id": str(task_id or "")}, project_id=str(project_id or ""))


def emit_file_changed(project_id: str, diff_uuid: str = "", task_id: str = "") -> None:
    publish(
        FILE_CHANGED,
        {"diff_uuid": str(diff_uuid or ""), "task_id": str(task_id or "")},
        project_id=str(project_id or ""),
    )


def emit_phase_started(project_id: str, phase: str, bot_id: str, label: str = "") -> None:
    publish(
        PHASE_STARTED,
        {"phase": str(phase or ""), "bot_id": str(bot_id or ""), "label": str(label or phase or "")},
        project_id=str(project_id or ""),
    )


def emit_cat_swapped(project_id: str, cat_id: str, name: str, phase: str, label: str = "", status: str = "") -> None:
    """The active persona of a run changed: ``cat_id`` now holds the engine (one event per hand-over)."""
    publish(
        CAT_SWAPPED,
        {
            "cat_id": str(cat_id or ""),
            "name": str(name or ""),
            "phase": str(phase or ""),
            "label": str(label or phase or ""),
            "status": str(status or "Thinking"),
        },
        project_id=str(project_id or ""),
    )


def emit_project_changed(project_id: str) -> None:
    publish(PROJECT_CHANGED, {}, project_id=str(project_id or ""))


def emit_context_usage(project_id: str, data: dict[str, Any]) -> None:
    """How full the running conversation's context is (``services/usage_meter.report_context``).
    A run with no project id is announced to every client, like ``bot.activity``."""
    publish(CONTEXT_USAGE, dict(data or {}), project_id=str(project_id or ""))
