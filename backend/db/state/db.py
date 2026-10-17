"""SQLite connection management for the platform state store.

One short-lived connection per operation — no cached/global handle — so
that test isolation via monkeypatching ``platform.registry.STORE_PATH``
keeps working exactly like it did for the JSON store: point at a
different path, get a completely independent database, with no stale
connection left over from a previous test pointing at the old one.

The database file lives beside the legacy ``registry.json`` path
(``STORE_PATH.parent / "catcrew_state.db"``) rather than at a fixed location, so
``platform/registry.py`` can keep ``STORE_PATH`` as the one identity it
exposes to callers and tests, and this module never needs to know about
``platform.registry`` at all (that import would be circular — registry.py
is what calls into this module, not the other way around).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def db_path_for(store_path: Path) -> Path:
    """Where the SQLite file lives, given the legacy JSON store path."""
    return Path(store_path).parent / "catcrew_state.db"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply schema.sql. Safe to call on every open — every statement in
    it is ``CREATE TABLE/INDEX IF NOT EXISTS``."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))


@contextmanager
def connect(store_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a short-lived connection to the database beside ``store_path``.

    Commits on clean exit, rolls back on exception, always closes. Callers
    do one unit of work per ``with connect(...) as conn:`` block — this is
    intentionally not a pooled/cached connection, trading a little
    per-call overhead for the guarantee that a path change (a new test's
    ``tmp_path``, a config reload) can never see a stale handle.
    """
    path = db_path_for(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        ensure_schema(conn)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
