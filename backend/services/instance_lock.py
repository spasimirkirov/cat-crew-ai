"""Single-instance guard for the backend.

Two backends writing the same ``catcrew.db`` / ``registry.json`` would corrupt
shared state, so the server takes an exclusive OS-level lock on
``data/backend.lock`` for its lifetime. The lock is released automatically if
the process dies (no stale-pid cleanup needed). Stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO

from backend.config import BASE_DIR

LOCK_PATH = BASE_DIR / "data" / "backend.lock"

_held: IO[str] | None = None


def acquire(path: Path | None = None) -> bool:
    """Take the lock. Returns False if another backend already holds it."""
    global _held
    if _held is not None:
        return True
    target = Path(path or LOCK_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    fh = open(target, "a+")  # noqa: SIM115 - kept open for the process lifetime
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    _held = fh
    return True


def release() -> None:
    global _held
    fh, _held = _held, None
    if fh is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass
