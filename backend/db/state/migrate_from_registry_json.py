"""One-shot migration: legacy ``registry.json`` -> SQLite.

Runs automatically from ``platform/registry.py::load_registry()`` the
first time it sees a JSON catalog next to a database that doesn't have
one yet. After a successful migration the JSON file is renamed aside to
``registry.json.migrated`` — never deleted, so a mistake here is always
recoverable by hand — and every subsequent read/write goes through
SQLite. Safe to call repeatedly: a database that already holds at least
one bot, team, or project is left completely alone.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.domain import Registry
from backend.db.state.repositories import RegistryRepo


def _read_legacy(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def needs_migration(store_path: Path) -> bool:
    """True if there is a legacy JSON catalog and the database next to it
    looks unseeded (no bots, teams, or projects yet)."""
    data = _read_legacy(store_path)
    if not data:
        return False
    existing = RegistryRepo.load(store_path)
    return not (existing.bots or existing.teams or existing.projects)


def migrate(store_path: Path) -> Registry | None:
    """Import ``registry.json`` into SQLite once, then rename it aside.

    Returns the migrated :class:`Registry`, or ``None`` if there was
    nothing to migrate (no legacy file) or nothing safe to do (the
    database already has a real catalog — this never overwrites it).
    """
    data = _read_legacy(store_path)
    if not data:
        return None
    existing = RegistryRepo.load(store_path)
    if existing.bots or existing.teams or existing.projects:
        return None
    registry = Registry.from_dict(data)
    RegistryRepo.save(store_path, registry)
    backup = store_path.with_suffix(store_path.suffix + ".migrated")
    try:
        if not backup.exists():
            store_path.rename(backup)
    except OSError:
        pass  # the SQLite import already succeeded; renaming aside is best-effort
    return registry
