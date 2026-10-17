"""SQLite-backed persistence for the platform (see the Master Architecture
Document, section 6, for the full design and reasoning).

``platform/registry.py`` is the only caller most of the app should ever
need — it wraps :class:`RegistryRepo` behind the same ``load_registry()``/
``save_registry()`` functions callers and tests already use, so nothing
outside ``platform/`` should import this package directly except the new
async engine in ``core/``, which writes ``project_state``/``events``/
``runs`` directly via :class:`GlobalStateRepo`, :class:`EventRepo`, and
:class:`RunRepo` without round-tripping the whole catalog.
"""

from __future__ import annotations

from backend.db.state.db import connect, db_path_for, ensure_schema
from backend.db.state.migrate_from_registry_json import migrate, needs_migration
from backend.db.state.repositories import EventRepo, GlobalStateRepo, RegistryRepo, RunRepo

__all__ = [
    "connect",
    "db_path_for",
    "ensure_schema",
    "migrate",
    "needs_migration",
    "EventRepo",
    "GlobalStateRepo",
    "RegistryRepo",
    "RunRepo",
]
