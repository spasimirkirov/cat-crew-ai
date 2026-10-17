"""Pre-pivot data keeps working: old ``cat_crew_ai.*`` handler paths and old DB schemas."""

from __future__ import annotations

import sqlite3

import pytest

from backend.db import ledger
from backend.db.legacy import normalize_module_path
from backend.platform import tool_registry


@pytest.mark.parametrize(
    "old,new",
    [
        ("cat_crew_ai.tool_functions.web:search", "backend.tool_functions.web:search"),
        ("cat_crew_ai.platform.db:create_task", "backend.db.ledger:create_task"),
        ("cat_crew_ai.state:load", "backend.db.state:load"),
        ("cat_crew_ai.terminal:run_foreground", "backend.services.terminal:run_foreground"),
        ("cat_crew_ai.sessions:load_session", "backend.services.sessions:load_session"),
        ("cat_crew_ai:x", "backend:x"),
        ("backend.tool_functions.web:search", "backend.tool_functions.web:search"),  # already new
        ("catcrew_marketplace_tools.foo_bar.tool:fn", "catcrew_marketplace_tools.foo_bar.tool:fn"),
        ("cat_crew_ai_extra.mod:fn", "cat_crew_ai_extra.mod:fn"),  # a different package
        ("", ""),
    ],
)
def test_normalize_module_path(old, new):
    assert normalize_module_path(old) == new


def test_normalize_is_idempotent():
    once = normalize_module_path("cat_crew_ai.platform.db:create_task")
    assert normalize_module_path(once) == once


def test_old_database_is_upgraded_in_place_without_losing_rows(isolated):
    """An old ``catcrew.db`` (skills table without the typing columns, tools that
    point at ``cat_crew_ai.*``) opens, gains the columns, and keeps every row."""
    db = isolated / "catcrew.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT DEFAULT '',
                               status TEXT DEFAULT 'active', created_at TEXT NOT NULL);
        INSERT INTO projects VALUES ('proj_old', 'Legacy project', 'C:\\work', 'active', '2026-01-01T00:00:00Z');
        CREATE TABLE skills (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                             description TEXT NOT NULL DEFAULT '', playbook_path TEXT NOT NULL DEFAULT '',
                             is_global INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO skills VALUES ('skill_old', 'static-web', 'static-web', 'd', 'old/path.md', 1,
                                  '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        CREATE TABLE tools (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                            docstring TEXT NOT NULL DEFAULT '', handler_path TEXT NOT NULL DEFAULT '',
                            schema_json TEXT NOT NULL DEFAULT '{}', is_global INTEGER NOT NULL DEFAULT 0,
                            type TEXT NOT NULL DEFAULT 'system', source_url TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO tools VALUES ('tool_old', 'create_task', 'create_task', 'doc',
                                 'cat_crew_ai.platform.db:create_task', '{}', 0, 'system', '',
                                 '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    ledger.init_db()

    conn = ledger._connect()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(skills)")}
        assert {"type", "version", "target_roles", "source_url"} <= cols
        assert conn.execute("SELECT name, path FROM projects WHERE id='proj_old'").fetchone()["name"] == "Legacy project"
        skill = conn.execute("SELECT * FROM skills WHERE id='skill_old'").fetchone()
        assert skill["is_global"] == 1 and skill["type"] == "system" and skill["playbook_path"] == "old/path.md"
        tool = conn.execute("SELECT handler_path FROM tools WHERE id='tool_old'").fetchone()
        assert tool["handler_path"] == "backend.db.ledger:create_task"
    finally:
        conn.close()

    # running the migration again is a no-op
    ledger._initialized = False
    ledger.init_db()
    conn = ledger._connect()
    try:
        assert conn.execute("SELECT COUNT(*) c FROM tools").fetchone()["c"] == 1
    finally:
        conn.close()


def test_resolve_handler_accepts_an_unmigrated_legacy_path():
    fn = tool_registry.resolve_handler("cat_crew_ai.platform.db:create_task")
    assert callable(fn) and fn.__module__ == "backend.db.ledger"
