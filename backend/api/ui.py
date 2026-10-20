"""Browser mode: the backend serves the built front end itself, so the app runs in any browser.

``python -m backend --ui`` mounts ``frontend/dist`` (``npm run build``) at ``/`` next to the API.
The page and the API share one origin (``http://127.0.0.1:<port>``), so the front end talks to
the address it was loaded from (``frontend/src/lib/config.ts``) and ``api/security.py`` lets that
same-origin page through. The desktop shell (Tauri) keeps working as before; it is now optional.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.config import BASE_DIR

FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


def ui_ready(dist: Path | None = None) -> bool:
    return ((dist or FRONTEND_DIST) / "index.html").is_file()


def mount_ui(app: FastAPI, dist: Path | None = None) -> None:
    """Serve ``dist`` at ``/``. Mounted last, so every ``/api`` and ``/ws`` route wins over it.
    The app routes with a hash (``/#/project/...``), so no per-path fallback is needed."""
    app.mount("/", StaticFiles(directory=str(dist or FRONTEND_DIST), html=True), name="ui")
