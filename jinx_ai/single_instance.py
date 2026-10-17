"""One Jinx process per data folder. Qdrant/mem0 cannot share storage."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QLockFile

from .config import BASE_DIR, WINDOW_TITLE

LOCK_FILE = BASE_DIR / "data" / "jinx.lock"

_held: "InstanceLock | None" = None


class InstanceLock:
    """Exclusive lock file. Survives a crash because the OS drops the handle."""

    def __init__(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        self._q = QLockFile(str(target))
        self._q.setStaleLockTime(8000)

    def acquire(self) -> bool:
        return bool(self._q.tryLock(80))

    def release(self) -> None:
        if self._q.isLocked():
            self._q.unlock()


def acquire(path: Path | None = None) -> bool:
    """Take the app lock. False if another live Jinx already holds it."""
    global _held
    if _held is not None:
        return True
    lock = InstanceLock(path or LOCK_FILE)
    if not lock.acquire():
        return False
    _held = lock
    return True


def release() -> None:
    """Drop the app lock (needed before restart_jinx replaces this process)."""
    global _held
    if _held is not None:
        _held.release()
        _held = None


def focus_existing() -> bool:
    """Bring the running window to the front (Windows)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False
