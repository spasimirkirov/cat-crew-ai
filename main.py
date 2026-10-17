"""Compatibility entry point: ``python main.py`` runs the backend API server.

The desktop UI is the Tauri + React app in ``frontend/``; this repository's
Python side is a headless FastAPI service (``python -m backend``).
"""

from __future__ import annotations

import sys

from backend.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
