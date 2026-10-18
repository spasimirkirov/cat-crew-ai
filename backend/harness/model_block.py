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


# -- failover memory --------------------------------------------------------------------------
# After a run switched from a turned-away model to the Fallback model, later calls in the same
# project (the next SOP phase, the next chat turn) go straight to the fallback for a while instead
# of hitting the same wall first (and its retries with backoff).
FAILOVER_TTL_S = 15 * 60
_failovers: dict[tuple[str, str, str], float] = {}


def remember_failover(project_id: str, host: str, model: str) -> None:
    with _lock:
        _failovers[(str(project_id or ""), str(host or ""), str(model or ""))] = time.time()


def recently_failed_over(project_id: str, host: str, model: str) -> bool:
    key = (str(project_id or ""), str(host or ""), str(model or ""))
    with _lock:
        at = _failovers.get(key)
        if at is None:
            return False
        if time.time() - at > FAILOVER_TTL_S:
            _failovers.pop(key, None)
            return False
        return True


def reset() -> None:
    """Forget every block and failover (tests)."""
    with _lock:
        _blocks.clear()
        _failovers.clear()

