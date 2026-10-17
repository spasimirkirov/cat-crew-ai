"""Only one backend may own ``catcrew.db`` at a time."""

from __future__ import annotations

import subprocess
import sys
import textwrap

from backend.services import instance_lock


def test_lock_is_exclusive_across_processes_and_released(tmp_path, monkeypatch):
    lock = tmp_path / "backend.lock"
    monkeypatch.setattr(instance_lock, "_held", None)
    assert instance_lock.acquire(lock) is True
    assert instance_lock.acquire(lock) is True  # re-entrant within the owner

    probe = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(instance_lock.BASE_DIR)!r})
        from pathlib import Path
        from backend.services import instance_lock as il
        print("ACQUIRED" if il.acquire(Path({str(lock)!r})) else "BUSY")
        """
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60).stdout
    assert "BUSY" in out, out

    instance_lock.release()
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60).stdout
    assert "ACQUIRED" in out, out
    instance_lock.release()  # releasing twice is harmless
