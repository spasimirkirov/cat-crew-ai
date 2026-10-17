"""A model that cannot answer right now: rate-limited or out of credit.

When a host turns a model away (OpenRouter's 429 on a ``:free`` model is the common case), every
later call in the same run gets the same answer: the crew used to walk through every phase, each
cat failing in turn, while the provider retried each call. Instead the provider raises
:class:`ModelUnavailable` and records it here for the project. The work-run pipeline checks
:func:`pending` between phases and stops, and the chat service turns the record into a failed turn
that tells the person which model it was, which roles use it, and offers to switch them all to
another model and run the same message again (``POST /api/models/replace``).
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass

_lock = threading.Lock()
_blocks: dict[str, "ModelBlock"] = {}


class ModelUnavailable(RuntimeError):
    """The host refused this model for now (``kind``: ``rate_limited`` or ``no_credit``)."""

    def __init__(self, message: str, *, host: str, model: str, status: int, kind: str) -> None:
        super().__init__(message)
        self.host = host
        self.model = model
        self.status = status
        self.kind = kind


@dataclass
class ModelBlock:
    host: str
    model: str
    kind: str
    status: int
    message: str
    free: bool
    at: float

    def public(self) -> dict:
        return asdict(self)


def report(project_id: str, error: ModelUnavailable) -> None:
    """Remember the first blocked model of this project's run (later ones add nothing new)."""
    pid = str(project_id or "")
    with _lock:
        if pid in _blocks:
            return
        _blocks[pid] = ModelBlock(
            host=error.host,
            model=error.model,
            kind=error.kind,
            status=error.status,
            message=str(error),
            free=str(error.model or "").endswith(":free"),
            at=time.time(),
        )


def pending(project_id: str) -> ModelBlock | None:
    with _lock:
        return _blocks.get(str(project_id or ""))


def take(project_id: str) -> ModelBlock | None:
    with _lock:
        return _blocks.pop(str(project_id or ""), None)


def clear(project_id: str) -> None:
    with _lock:
        _blocks.pop(str(project_id or ""), None)
