"""One message history per work run, with the cat as a swappable mask (opt-in).

Off by default (``CATCREW_UNIFIED_CONTEXT=1`` turns it on). By default every specialist run starts
from a fresh context: its own system prompt, its assignment, and a short hand-off. In unified mode
a work run (a Cat Crew SOP turn or a problem report) owns one ``SharedThread``:

* the history is ONE list of messages that every cat in the run reads and appends to;
* switching cat replaces only the *mask*: the system message becomes the new cat's prompt (its
  tools are bound separately, by ``run_bot``), everything after it is the shared history;
* a finished cat's turn is collapsed to its assignment line plus its result (and the files it
  touched), so the shared history grows by about a page per cat, not by every tool call it made;
* the active cat's assignment is marked (``TASK_PREFIX``), so compaction keeps pinning the task
  that cat is actually working on instead of the first cat's.

The trade-off is real and worth measuring before making it the default: a swapped system message
changes the very start of the prompt, so a provider's prompt/KV cache cannot be reused across cats,
and the history a cat reads includes other cats' work. The isolated mode does neither.
"""

from __future__ import annotations

import threading
from typing import Any

from backend import config
from backend.services.compaction import TASK_PREFIX, files_touched

Message = dict[str, Any]

_TASK_LINE_CAP = 400  # what stays of a finished cat's assignment
_RESULT_CAP = 1_800  # what stays of a finished cat's result
_HISTORY_CAP = 14_000  # characters; the oldest turns are dropped first


def unified_enabled() -> bool:
    return bool(getattr(config, "UNIFIED_CONTEXT", False))


def assignment_message(cat_name: str, role: str, task: str) -> Message:
    return {"role": "user", "content": f"{TASK_PREFIX}{cat_name} ({role})]\n{task}"}


class SharedThread:
    """The single message history of one work run."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.history: list[Message] = []  # user/assistant pairs of the cats that already finished
        self.cats: list[str] = []  # who has taken a turn, in order (for logs and tests)
        self._lock = threading.Lock()

    def open_turn(self, system: str, cat_name: str, role: str, task: str) -> list[Message]:
        """The payload for the next cat: its system prompt (the mask), the shared history, its task."""
        with self._lock:
            self.cats.append(cat_name)
            return [
                {"role": "system", "content": system},
                *[dict(m) for m in self.history],
                assignment_message(cat_name, role, task),
            ]

    def close_turn(self, messages: list[Message], cat_name: str, role: str, task: str, result: str) -> None:
        """Fold a finished cat's live transcript into one assignment + result pair."""
        try:
            touched = files_touched(messages[1:])
        except Exception:  # noqa: BLE001 - a digest is a help, never a reason to lose the result
            touched = []
        text = " ".join(str(result or "").split())
        # The runner appends a directory listing after every run; it is noise for the next cat.
        text = text.split("Computer after this turn:", 1)[0].strip()
        if len(text) > _RESULT_CAP:
            text = text[:_RESULT_CAP].rstrip() + " …[excerpt]"
        if touched:
            text += "\nFiles touched: " + ", ".join(touched[:12])
        line = " ".join(str(task or "").split())[:_TASK_LINE_CAP]
        with self._lock:
            self.history.append(assignment_message(cat_name, role, line))
            self.history.append({"role": "assistant", "content": f"[{cat_name}] {text}"})
            self._trim()

    def _trim(self) -> None:
        def size() -> int:
            return sum(len(str(m.get("content") or "")) for m in self.history)

        while len(self.history) > 2 and size() > _HISTORY_CAP:
            del self.history[:2]  # always a whole user/assistant pair


_active: dict[str, SharedThread] = {}
_active_lock = threading.Lock()


def begin_run(project_id: str) -> SharedThread | None:
    """Start the shared history for a work run, or ``None`` when unified mode is off."""
    if not unified_enabled() or not project_id:
        return None
    thread = SharedThread(project_id)
    with _active_lock:
        _active[project_id] = thread
    return thread


def end_run(project_id: str) -> None:
    with _active_lock:
        _active.pop(project_id, None)


def active_thread(project_id: str) -> SharedThread | None:
    with _active_lock:
        return _active.get(project_id)
