"""FastAPI + WebSocket bridge between the Python backend and the Tauri UI.

See ``docs/API.md`` for the contract. ``create_app()`` builds the application;
``python -m backend`` runs it on loopback.
"""

from __future__ import annotations

API_VERSION = 1
