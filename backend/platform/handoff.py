"""Hand-off from a Cat Crew *chat* turn to the specialist pipeline.

Cat Crew answers free-form messages itself. When the primary model decides the
conversation has turned into real work it calls ``hand_off_to_crew``; that tool
only *records* the request here (it never runs a specialist inside the model's
own tool loop, where the cats' progress would be invisible). When the chat turn
ends, ``ChatService`` takes the request and starts a normal room turn, so every
specialist streams to the UI over the WebSocket exactly like an auto-routed one.

Thread-safe and dependency-free; one pending request per project (last one wins).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

_lock = threading.Lock()
_pending: dict[str, "Handoff"] = {}
# Projects whose next pipeline run should finish the work already planned (hand_off_to_crew with
# resume): the SOP skips the phases that are done instead of planning everything again.
_resume: set[str] = set()


@dataclass(frozen=True)
class Handoff:
    brief: str
    roles: tuple[str, ...] | None = None


def request(project_id: str, brief: str, roles: tuple[str, ...] | None = None, *, resume: bool = False) -> None:
    pid = str(project_id or "").strip()
    text = str(brief or "").strip()
    if not pid or not text:
        return
    with _lock:
        _pending[pid] = Handoff(text, tuple(roles) if roles else None)
        if resume:
            _resume.add(pid)
        else:
            _resume.discard(pid)


def take_resume(project_id: str) -> bool:
    """True once when the last hand-off asked to finish the planned work (see ``request``)."""
    with _lock:
        pid = str(project_id or "").strip()
        asked = pid in _resume
        _resume.discard(pid)
        return asked


def take(project_id: str) -> Handoff | None:
    with _lock:
        return _pending.pop(str(project_id or "").strip(), None)


def discard(project_id: str) -> None:
    take(project_id)
    take_resume(project_id)
