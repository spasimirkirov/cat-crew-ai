"""Entry point for jinx-ai.

    python main.py
    python main.py --smoke
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from jinx_ai.logutil import install as _quiet_logs

_quiet_logs()


def main() -> int:
    smoke = "--smoke" in sys.argv
    debug_avatar = "--debug-avatar" in sys.argv

    try:
        from jinx_ai.app import run
        from jinx_ai.single_instance import acquire, focus_existing
    except ImportError as exc:
        print(f"[error] Missing dependency: {exc}")
        print("Install with: python -m pip install -r requirements.txt")
        return 1

    if not acquire():
        print("Jinx is already running. Use the tray or the open window.")
        focus_existing()
        return 1

    print("=== Jinx AI ===")
    print("Home opens first (Projects and Teams).")
    print("Tray: right-click → Open Jinx / Show orb / Exit.")
    print("Ctrl+C in this terminal stops the app.")
    print()

    return run(smoke=smoke, debug_avatar=debug_avatar)


if __name__ == "__main__":
    sys.exit(main())
