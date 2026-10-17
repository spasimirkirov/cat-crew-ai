"""Relational operational ledger for the multi-agent platform (CatCrewAI
rebrand — "Optimized Agent Fleet & Dynamic Skill Management Engine").

Deliberately scoped: identity data (who a bot IS — name, prompt, tools) and
project identity (name, folders) stay exactly where they already lived,
in ``data/platform/registry.json`` via ``models.py``/``registry.py``. That
store is small, human-editable, and every other module already depends on
its shape — rewriting it wholesale into SQLite in one pass would be a lot
of risk for no behavioral gain.

What genuinely needed a real database, because it did not exist as
structured data anywhere before this module: which agents are actually
staffed on a project right now (``project_members`` — previously only
``Project.bot_ids``, a plain id list with no timestamp or history), a real
task ledger with results (``project_member_tasks`` /
``project_member_task_results`` — previously just free-text chat turns),
a durable record of which file a task touched (``file_changes`` — did not
exist at all), and a skill catalog with a real per-agent assignment matrix
(``skills`` / ``agent_skills``, now the actual source of truth
``agent_context.skills_prompt_index()``/``read_skill()`` filter through —
Phase 15 closed the gap where every skill on disk was shown to every role,
unfiltered, regardless of this table's assignments) plus a matching
executable-function registry (``tools`` / ``agent_tools``, Phase 15).

Every function opens its own short-lived ``sqlite3.Connection`` rather than
holding one open across calls (matching ``delegation.py``'s "a fresh engine
per call" reasoning: this is called from multiple turn and API worker threads at once —
a shared connection object is not thread-safe in Python's sqlite3 module,
and the cost of opening a local SQLite file is negligible next to an LLM
turn). A module-level lock serializes writes so two workers finishing a
tool call in the same instant cannot corrupt a table.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from typing import Any

from backend.config import BASE_DIR

DB_PATH = BASE_DIR / "data" / "platform" / "catcrew.db"

_write_lock = threading.Lock()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


_initialized = False


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    global _initialized
    if not _initialized:
        # Lazy, once-per-process schema creation, so no caller (a tool
        # call, a fresh SOP pipeline run, a script) has to remember to call
        # init_db() first — CREATE TABLE IF NOT EXISTS is cheap and this
        # only runs once per process after the first connection succeeds.
        conn.executescript(_SCHEMA)
        _migrate_schema(conn)
        conn.commit()
        _initialized = True
    return conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Additive, idempotent backfill for DBs created before the CatCrewAI
    Dual Skill/Tool Architecture pass (Phase 15) added typing/versioning
    columns to ``skills``. ``ALTER TABLE ... ADD COLUMN`` is cheap and safe
    to attempt on every process start — real seeded rows (existing skills,
    existing agent_skills assignments) are untouched; only missing columns
    get backfilled with their default. Never drops or rewrites a table."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(skills)").fetchall()}
    for column, ddl in (
        ("type", "ALTER TABLE skills ADD COLUMN type TEXT NOT NULL DEFAULT 'system'"),
        ("version", "ALTER TABLE skills ADD COLUMN version TEXT NOT NULL DEFAULT '1.0.0'"),
        ("target_roles", "ALTER TABLE skills ADD COLUMN target_roles TEXT NOT NULL DEFAULT ''"),
        ("source_url", "ALTER TABLE skills ADD COLUMN source_url TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_type ON skills(type)")
    _migrate_task_work_orders(conn)
    _migrate_legacy_handler_paths(conn)


def _migrate_task_work_orders(conn: sqlite3.Connection) -> None:
    """Additive: a task becomes a *work order* (what to do, how it is accepted, where it came
    from). Old rows keep working: every new column has a default, and ``kind`` defaults to
    ``build``. Never drops or rewrites anything."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(project_member_tasks)").fetchall()}
    for column, ddl in (
        # build | bug | fix | review
        ("kind", "ALTER TABLE project_member_tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'build'"),
        # The task this one belongs to (a fix or a review points at its bug).
        ("parent_id", "ALTER TABLE project_member_tasks ADD COLUMN parent_id TEXT NOT NULL DEFAULT ''"),
        # How it is accepted: a command that must exit 0, or what must be visible in the page.
        ("acceptance", "ALTER TABLE project_member_tasks ADD COLUMN acceptance TEXT NOT NULL DEFAULT ''"),
        # Comma-separated task ids that must be done first.
        ("depends_on", "ALTER TABLE project_member_tasks ADD COLUMN depends_on TEXT NOT NULL DEFAULT ''"),
        ("attempt", "ALTER TABLE project_member_tasks ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0"),
        # Who raised it: 'user', or the id of the cat the user was talking to.
        ("reported_by", "ALTER TABLE project_member_tasks ADD COLUMN reported_by TEXT NOT NULL DEFAULT ''"),
    ):
        if column not in existing:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON project_member_tasks(parent_id)")


def _migrate_legacy_handler_paths(conn: sqlite3.Connection) -> None:
    """Rewrite persisted tool handler paths from the pre-pivot ``cat_crew_ai.*``
    layout to ``backend.*``. Idempotent and data-preserving: only the
    ``handler_path`` text of rows that still carry the old prefix changes."""
    from backend.db.legacy import normalize_module_path

    try:
        rows = conn.execute(
            "SELECT id, handler_path FROM tools WHERE handler_path LIKE 'cat_crew_ai%'"
        ).fetchall()
    except sqlite3.OperationalError:  # tools table not created yet
        return
    for row in rows:
        conn.execute(
            "UPDATE tools SET handler_path = ? WHERE id = ?",
            (normalize_module_path(str(row["handler_path"])), row["id"]),
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_members (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE(project_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id);

CREATE TABLE IF NOT EXISTS project_member_tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    assigned_to TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'lead',
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON project_member_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON project_member_tasks(assigned_to);

CREATE TABLE IF NOT EXISTS project_member_task_results (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    final_state TEXT NOT NULL DEFAULT 'done',
    summary TEXT NOT NULL DEFAULT '',
    md_log_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_task ON project_member_task_results(task_id);

CREATE TABLE IF NOT EXISTS file_changes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL DEFAULT '',
    project_id TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    diff_uuid TEXT NOT NULL,
    diff_content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_changes_project ON file_changes(project_id);
CREATE INDEX IF NOT EXISTS idx_file_changes_task ON file_changes(task_id);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    playbook_path TEXT NOT NULL DEFAULT '',
    is_global INTEGER NOT NULL DEFAULT 0,
    type TEXT NOT NULL DEFAULT 'system',
    version TEXT NOT NULL DEFAULT '1.0.0',
    target_roles TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_skills (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE(agent_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills(agent_id);

-- Letta-style executable function registry (Phase 15's "Tools" half of the
-- Dual Skill/Tool Architecture). Mirrors skills/agent_skills exactly.
CREATE TABLE IF NOT EXISTS tools (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    docstring TEXT NOT NULL DEFAULT '',
    handler_path TEXT NOT NULL DEFAULT '',
    schema_json TEXT NOT NULL DEFAULT '{}',
    is_global INTEGER NOT NULL DEFAULT 0,
    type TEXT NOT NULL DEFAULT 'system',
    source_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tools (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    tool_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE(agent_id, tool_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_tools_agent ON agent_tools(agent_id);

-- Small key/value facts about this database itself (e.g. which seed version ran).
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


def init_db() -> None:
    """Create every table if missing. Idempotent — safe to call on every launch."""
    with _write_lock:
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()


def get_meta(key: str, default: str = "") -> str:
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default
    finally:
        conn.close()


def set_meta(key: str, value: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            conn.commit()
        finally:
            conn.close()


# --- projects / project_members ------------------------------------------------


def ensure_project(project_id: str, name: str, path: str = "", status: str = "active") -> None:
    if not project_id:
        return
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO projects (id, name, path, status, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, path=excluded.path",
                (project_id, name, path, status, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def add_project_member(project_id: str, agent_id: str) -> None:
    """Dynamic fleet allocation: staff ``agent_id`` onto ``project_id``.

    Idempotent (``UNIQUE(project_id, agent_id)``) — safe to call every time
    a bot picks up a task, not just the first time.
    """
    if not project_id or not agent_id:
        return
    with _write_lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO project_members (id, project_id, agent_id, added_at) "
                "VALUES (?, ?, ?, ?)",
                (_new_id("pm"), project_id, agent_id, _now()),
            )
            inserted = (cur.rowcount or 0) > 0
            conn.commit()
        finally:
            conn.close()
    # Real-time fleet sync: announce a *new* member so every connected client
    # pops the bot into the sidebar instantly. Fired from whatever thread
    # called us (the room's worker thread just as often as an API request);
    # the event bus is thread-safe. An idempotent re-staff (already a member)
    # changes nothing on screen, so it stays silent.
    if inserted:
        from backend.platform.bus import emit_bot_staffed

        emit_bot_staffed(project_id, agent_id)


def remove_project_member(project_id: str, agent_id: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM project_members WHERE project_id = ? AND agent_id = ?",
                (project_id, agent_id),
            )
            conn.commit()
        finally:
            conn.close()
    from backend.platform.bus import emit_bot_released

    emit_bot_released(project_id, agent_id)


def list_project_members(project_id: str) -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT agent_id FROM project_members WHERE project_id = ? ORDER BY added_at",
            (project_id,),
        ).fetchall()
        return [row["agent_id"] for row in rows]
    finally:
        conn.close()


# --- tasks / results -------------------------------------------------------


def _task_project(conn: sqlite3.Connection, task_id: str) -> str:
    row = conn.execute(
        "SELECT project_id FROM project_member_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return str(row["project_id"]) if row else ""


def _announce_task(project_id: str, task_id: str) -> None:
    """Tell live clients (Tasks tab, Recent Changes) this project's task
    ledger changed. Best-effort and thread-agnostic — see bus.py."""
    if not project_id:
        return
    from backend.platform.bus import emit_task_changed

    emit_task_changed(project_id, task_id)


def create_task(
    project_id: str,
    assigned_to: str,
    title: str,
    description: str = "",
    *,
    created_by: str = "lead",
    kind: str = "build",
    parent_id: str = "",
    acceptance: str = "",
    depends_on: str = "",
    attempt: int = 0,
    reported_by: str = "",
) -> str:
    task_id = _new_id("task")
    now = _now()
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO project_member_tasks "
                "(id, project_id, assigned_to, created_by, title, description, status, "
                "created_at, updated_at, kind, parent_id, acceptance, depends_on, attempt, reported_by) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id, project_id, assigned_to, created_by, title[:200], description, now, now,
                    kind or "build", parent_id, acceptance, depends_on, int(attempt or 0), reported_by,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    add_project_member(project_id, assigned_to)
    _announce_task(project_id, task_id)
    return task_id


def get_task(task_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM project_member_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_task_status(task_id: str, status: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE project_member_tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), task_id),
            )
            project_id = _task_project(conn, task_id)
            conn.commit()
        finally:
            conn.close()
    _announce_task(project_id, task_id)


def add_task_result(
    task_id: str, final_state: str, summary: str, md_log_path: str = ""
) -> str:
    """Record a specialist's self-reported outcome (the ``report_task_results``
    tool's write path) and close the task out with that same status."""
    result_id = _new_id("result")
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO project_member_task_results "
                "(id, task_id, final_state, summary, md_log_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (result_id, task_id, final_state, summary[:8000], md_log_path, _now()),
            )
            conn.execute(
                "UPDATE project_member_tasks SET status = ?, updated_at = ? WHERE id = ?",
                (final_state, _now(), task_id),
            )
            project_id = _task_project(conn, task_id)
            conn.commit()
        finally:
            conn.close()
    _announce_task(project_id, task_id)
    return result_id


def list_tasks_for_project(project_id: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM project_member_tasks WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_tasks_with_summary(project_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Tasks for a project, newest first, each carrying ``latest_summary`` —
    the most recent ``report_task_results`` text — in ONE query (the stepper
    used to open a connection per task for this)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT t.*, ("
            "SELECT r.summary FROM project_member_task_results r "
            "WHERE r.task_id = t.id ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1"
            ") AS latest_summary "
            "FROM project_member_tasks t WHERE t.project_id = ? "
            "ORDER BY t.created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_child_tasks(parent_id: str, kind: str = "") -> list[dict[str, Any]]:
    """Tasks that belong to ``parent_id`` (a bug's fixes and reviews), oldest first."""
    conn = _connect()
    try:
        if kind:
            rows = conn.execute(
                "SELECT * FROM project_member_tasks WHERE parent_id = ? AND kind = ? "
                "ORDER BY created_at, rowid",
                (parent_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM project_member_tasks WHERE parent_id = ? ORDER BY created_at, rowid",
                (parent_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_task_with_summary(task_id: str) -> dict[str, Any] | None:
    """One task row plus ``latest_summary`` (same shape as ``list_tasks_with_summary``)."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT t.*, ("
            "SELECT r.summary FROM project_member_task_results r "
            "WHERE r.task_id = t.id ORDER BY r.created_at DESC, r.rowid DESC LIMIT 1"
            ") AS latest_summary FROM project_member_tasks t WHERE t.id = ?",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_results_for_task(task_id: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM project_member_task_results WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# --- file_changes ------------------------------------------------------------


def log_file_change(
    project_id: str,
    task_id: str,
    agent_id: str,
    file_path: str,
    diff_content: str = "",
) -> str:
    diff_uuid = _new_id("diff")
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO file_changes "
                "(id, task_id, project_id, agent_id, file_path, diff_uuid, diff_content, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _new_id("fc"),
                    task_id,
                    project_id,
                    agent_id,
                    file_path,
                    diff_uuid,
                    diff_content[:20000],
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    _announce_task(project_id, task_id)
    if project_id:
        from backend.platform.bus import emit_file_changed

        emit_file_changed(project_id, diff_uuid, task_id)
    return diff_uuid


def list_recent_file_changes(project_id: str, limit: int = 10) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT file_path, agent_id, diff_uuid, created_at FROM file_changes "
            "WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_file_changes(project_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """Newest-first file changes for a project — every column except the (up to
    20 kB) diff body, which is fetched on demand via ``get_file_change``."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, task_id, project_id, agent_id, file_path, diff_uuid, created_at "
            "FROM file_changes WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (project_id, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def file_changes_for_task(task_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Every file_changes row for one task, oldest first — the diff
    evidence ``platform/auto_skill.py`` reads to judge whether a
    finished task contains a reusable pattern (Phase 15 §4b)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT file_path, agent_id, diff_content, created_at FROM file_changes "
            "WHERE task_id = ? ORDER BY created_at ASC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_file_change(diff_uuid: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM file_changes WHERE diff_uuid = ?", (diff_uuid,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --- skills / agent_skills -----------------------------------------------------


def upsert_skill(
    slug: str,
    name: str,
    description: str = "",
    playbook_path: str = "",
    *,
    is_global: bool | None = None,
    type: str | None = None,  # noqa: A002 — matches the column name, kept local
    version: str | None = None,
    target_roles: str | None = None,
    source_url: str | None = None,
) -> str:
    """Insert or refresh a skill row. ``is_global`` (and the new typing
    columns) are left alone on an existing row when omitted — they are
    user/assignment-matrix or ingestion-engine owned, not something a plain
    disk rescan (``sync_skills_from_disk``) should silently reset."""
    with _write_lock:
        conn = _connect()
        try:
            existing = conn.execute(
                "SELECT id, is_global, type, version, target_roles, source_url "
                "FROM skills WHERE slug = ?",
                (slug,),
            ).fetchone()
            now = _now()
            if existing:
                skill_id = existing["id"]
                new_global = existing["is_global"] if is_global is None else int(bool(is_global))
                new_type = existing["type"] if type is None else type
                new_version = existing["version"] if version is None else version
                new_roles = existing["target_roles"] if target_roles is None else target_roles
                new_source = existing["source_url"] if source_url is None else source_url
                conn.execute(
                    "UPDATE skills SET name=?, description=?, playbook_path=?, "
                    "is_global=?, type=?, version=?, target_roles=?, source_url=?, "
                    "updated_at=? WHERE id=?",
                    (
                        name,
                        description,
                        playbook_path,
                        new_global,
                        new_type,
                        new_version,
                        new_roles,
                        new_source,
                        now,
                        skill_id,
                    ),
                )
            else:
                skill_id = _new_id("skill")
                conn.execute(
                    "INSERT INTO skills (id, slug, name, description, playbook_path, "
                    "is_global, type, version, target_roles, source_url, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        skill_id,
                        slug,
                        name,
                        description,
                        playbook_path,
                        int(bool(is_global)),
                        type or "system",
                        version or "1.0.0",
                        target_roles or "",
                        source_url or "",
                        now,
                        now,
                    ),
                )
            conn.commit()
            return skill_id
        finally:
            conn.close()


def delete_skill(skill_id: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM agent_skills WHERE skill_id = ?", (skill_id,))
            conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
            conn.commit()
        finally:
            conn.close()


def list_skills() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM skills ORDER BY name").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_skill_by_slug(slug: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM skills WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def assign_skill(agent_id: str, skill_id: str) -> None:
    if _is_orchestrator(agent_id):
        return  # Cat Crew is not an agent: skills are assigned to specialist cats only
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO agent_skills (id, agent_id, skill_id, added_at) "
                "VALUES (?, ?, ?, ?)",
                (_new_id("as"), agent_id, skill_id, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def assign_skill_by_slug(agent_id: str, slug: str) -> None:
    skill = get_skill_by_slug(slug)
    if skill:
        assign_skill(agent_id, skill["id"])


def unassign_skill(agent_id: str, skill_id: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM agent_skills WHERE agent_id = ? AND skill_id = ?",
                (agent_id, skill_id),
            )
            conn.commit()
        finally:
            conn.close()


def agent_skill_ids(agent_id: str) -> set[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT skill_id FROM agent_skills WHERE agent_id = ?", (agent_id,)
        ).fetchall()
        return {row["skill_id"] for row in rows}
    finally:
        conn.close()


def agent_ids_for_skill(skill_id: str) -> set[str]:
    """Reverse of ``agent_skill_ids`` — which agents this skill is explicitly
    assigned to (the assignment-matrix UI's per-skill row state). Does not
    include agents that only see it via the ``is_global`` union; that's a
    display concern for the caller, not something this table encodes."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT agent_id FROM agent_skills WHERE skill_id = ?", (skill_id,)
        ).fetchall()
        return {row["agent_id"] for row in rows}
    finally:
        conn.close()


def set_skill_global(skill_id: str, is_global: bool) -> None:
    """Flip the "Global Skills Toggle" for one skill — auto-attach to every
    agent regardless of the individual assignment matrix."""
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE skills SET is_global = ?, updated_at = ? WHERE id = ?",
                (int(bool(is_global)), _now(), skill_id),
            )
            conn.commit()
        finally:
            conn.close()


def _is_orchestrator(agent_id: str) -> bool:
    """Cat Crew (legacy id ``chief``) is the orchestrator, not an assignable agent."""
    from backend.platform.orchestrator import is_room_id

    return is_room_id(agent_id)


def skills_for_agent(agent_id: str) -> list[dict[str, Any]]:
    """Skills this agent should see: explicitly assigned, plus every
    ``is_global`` skill regardless of assignment (the "Global Skills
    Toggle" from the spec auto-attaches to every agent)."""
    conn = _connect()
    try:
        if _is_orchestrator(agent_id):
            # Cat Crew is not an agent: no per-agent row is ever looked up for it.
            rows = conn.execute(
                "SELECT s.slug, s.name, s.description, s.playbook_path FROM skills s "
                "WHERE s.is_global = 1 ORDER BY s.name"
            ).fetchall()
            return [dict(row) for row in rows]
        rows = conn.execute(
            "SELECT DISTINCT s.slug, s.name, s.description, s.playbook_path FROM skills s "
            "LEFT JOIN agent_skills a ON a.skill_id = s.id AND a.agent_id = ? "
            "WHERE s.is_global = 1 OR a.agent_id IS NOT NULL "
            "ORDER BY s.name",
            (agent_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def sync_skills_from_disk() -> int:
    """Upsert every skill found on disk (``agent_context.list_skills()``) into
    the ``skills`` table so the assignment matrix has something to assign —
    content stays on disk (source of truth for the playbook text itself),
    this table only tracks catalog metadata + the assignment pivot.
    Returns the number of skills synced."""
    from backend.agent_context import list_skills as disk_skills

    count = 0
    for skill in disk_skills():
        upsert_skill(
            slug=skill.name,
            name=skill.name,
            description=skill.description,
            playbook_path=str(skill.path),
        )
        count += 1
    return count


# --- tools / agent_tools (Letta-style executable function registry) -----------
#
# Deliberate 1:1 mirror of the "skills / agent_skills" functions above rather
# than a shared generic "catalog" abstraction — two small, boring, near-
# identical function sets are easier to audit than one clever generic one,
# matching the rest of this module's style (Phase 15 blueprint, §1).


def upsert_tool(
    slug: str,
    name: str,
    docstring: str = "",
    handler_path: str = "",
    schema_json: str = "{}",
    *,
    is_global: bool | None = None,
    type: str | None = None,  # noqa: A002 — matches the column name, kept local
    source_url: str | None = None,
) -> str:
    """Insert or refresh a tool row. ``is_global`` is left alone on an
    existing row when omitted — assignment-matrix owned, not something a
    re-registration pass should silently reset."""
    with _write_lock:
        conn = _connect()
        try:
            existing = conn.execute(
                "SELECT id, is_global, type, source_url FROM tools WHERE slug = ?",
                (slug,),
            ).fetchone()
            now = _now()
            if existing:
                tool_id = existing["id"]
                new_global = existing["is_global"] if is_global is None else int(bool(is_global))
                new_type = existing["type"] if type is None else type
                new_source = existing["source_url"] if source_url is None else source_url
                conn.execute(
                    "UPDATE tools SET name=?, docstring=?, handler_path=?, schema_json=?, "
                    "is_global=?, type=?, source_url=?, updated_at=? WHERE id=?",
                    (
                        name,
                        docstring,
                        handler_path,
                        schema_json,
                        new_global,
                        new_type,
                        new_source,
                        now,
                        tool_id,
                    ),
                )
            else:
                tool_id = _new_id("tool")
                conn.execute(
                    "INSERT INTO tools (id, slug, name, docstring, handler_path, schema_json, "
                    "is_global, type, source_url, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tool_id,
                        slug,
                        name,
                        docstring,
                        handler_path,
                        schema_json,
                        int(bool(is_global)),
                        type or "system",
                        source_url or "",
                        now,
                        now,
                    ),
                )
            conn.commit()
            return tool_id
        finally:
            conn.close()


def delete_tool(tool_id: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM agent_tools WHERE tool_id = ?", (tool_id,))
            conn.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
            conn.commit()
        finally:
            conn.close()


def list_tools() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM tools ORDER BY name").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_tool_by_slug(slug: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM tools WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def assign_tool(agent_id: str, tool_id: str) -> None:
    if _is_orchestrator(agent_id):
        return  # Cat Crew is not an agent: tools are assigned to specialist cats only
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO agent_tools (id, agent_id, tool_id, added_at) "
                "VALUES (?, ?, ?, ?)",
                (_new_id("at"), agent_id, tool_id, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def assign_tool_by_slug(agent_id: str, slug: str) -> None:
    tool = get_tool_by_slug(slug)
    if tool:
        assign_tool(agent_id, tool["id"])


def unassign_tool(agent_id: str, tool_id: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "DELETE FROM agent_tools WHERE agent_id = ? AND tool_id = ?",
                (agent_id, tool_id),
            )
            conn.commit()
        finally:
            conn.close()


def agent_tool_ids(agent_id: str) -> set[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT tool_id FROM agent_tools WHERE agent_id = ?", (agent_id,)
        ).fetchall()
        return {row["tool_id"] for row in rows}
    finally:
        conn.close()


def agent_ids_for_tool(tool_id: str) -> set[str]:
    """Reverse of ``agent_tool_ids`` — which agents this tool is explicitly
    assigned to (the assignment-matrix UI's per-tool row state). Does not
    include agents that only get it via the ``is_global`` union; that's a
    display concern for the caller, not something this table encodes."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT agent_id FROM agent_tools WHERE tool_id = ?", (tool_id,)
        ).fetchall()
        return {row["agent_id"] for row in rows}
    finally:
        conn.close()


def set_tool_global(tool_id: str, is_global: bool) -> None:
    """Flip the "Global" toggle for one tool — auto-attach to every agent
    regardless of the individual assignment matrix."""
    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "UPDATE tools SET is_global = ?, updated_at = ? WHERE id = ?",
                (int(bool(is_global)), _now(), tool_id),
            )
            conn.commit()
        finally:
            conn.close()


def tools_for_agent(agent_id: str) -> list[dict[str, Any]]:
    """Tools this agent should have bound this turn: explicitly assigned,
    plus every ``is_global`` tool regardless of assignment."""
    conn = _connect()
    try:
        if _is_orchestrator(agent_id):
            rows = conn.execute(
                "SELECT t.slug, t.name, t.docstring, t.handler_path, t.schema_json "
                "FROM tools t WHERE t.is_global = 1 ORDER BY t.name"
            ).fetchall()
            return [dict(row) for row in rows]
        rows = conn.execute(
            "SELECT DISTINCT t.slug, t.name, t.docstring, t.handler_path, t.schema_json "
            "FROM tools t "
            "LEFT JOIN agent_tools a ON a.tool_id = t.id AND a.agent_id = ? "
            "WHERE t.is_global = 1 OR a.agent_id IS NOT NULL "
            "ORDER BY t.name",
            (agent_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
