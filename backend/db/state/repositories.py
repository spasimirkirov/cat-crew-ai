"""SQLite-backed persistence for the domain package.

``RegistryRepo.load()``/``save()`` are drop-in replacements for the JSON
read/write that used to live directly in ``platform/registry.py`` — same
shape in, same shape out — so ``load_registry()``/``save_registry()``
there become thin wrappers around this module instead of being the store
themselves (see ``docs`` master architecture doc, section 6).

Two tables are deliberately *not* part of the destructive whole-registry
replace that ``RegistryRepo.save()`` does for the catalog (bots / teams /
projects / chief / globals): ``project_state`` and ``events``. Nothing in
the app has ever deleted a state key or an event — the old JSON code only
ever appended events (trimming the in-memory list with a client-side cap)
and overwrote state keys — so these two get upsert-only repos
(``GlobalStateRepo``, ``EventRepo``) that never delete a row on save. That
is also what makes concurrent event-append safe: two bots writing at once
each ``INSERT OR IGNORE`` their own stably-id'd row, so neither can
clobber the other — a whole-table rewrite could not guarantee that.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.domain import AgentRun, Bot, ChiefSpec, Project, ProjectEvent, Registry, RunKind, RunStatus, Team
from backend.domain.ids import now_iso
from backend.db.state import db


def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _sync_table(conn: sqlite3.Connection, *, table: str, rows: dict[str, dict]) -> None:
    """Make ``table`` match ``rows`` exactly: delete ids not present,
    upsert every id that is. Used for the small catalog tables
    (bots/teams/projects) where the in-memory Registry is always the
    whole truth — never for ``project_state``/``events``, which are
    upsert-only (see module docstring)."""
    existing = {row["id"] for row in conn.execute(f"SELECT id FROM {table}")}
    stale = existing - set(rows)
    if stale:
        placeholders = ",".join("?" for _ in stale)
        conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", tuple(stale))
    for values in rows.values():
        columns = list(values)
        placeholders = ",".join("?" for _ in columns)
        assignments = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "id")
        conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {assignments}",
            tuple(values[c] for c in columns),
        )


class RegistryRepo:
    """Whole-catalog load/save — the SQLite counterpart of the old
    ``registry.json`` blob, minus ``project_state``/``events`` (see module
    docstring)."""

    @staticmethod
    def load(store_path: Path) -> Registry:
        with db.connect(store_path) as conn:
            bots = {row["id"]: _bot_from_row(row) for row in conn.execute("SELECT * FROM bots")}
            teams = {row["id"]: _team_from_row(row) for row in conn.execute("SELECT * FROM teams")}
            projects: dict[str, Project] = {}
            for row in conn.execute("SELECT * FROM projects"):
                project = _project_from_row(row)
                project.state = GlobalStateRepo._load(conn, project.id)
                project.events = EventRepo._load(conn, project.id)
                projects[project.id] = project
            # Singleton config table (Cat Crew): one row, whatever its legacy key was.
            chief_row = conn.execute("SELECT * FROM chief ORDER BY (id = 'crew') DESC LIMIT 1").fetchone()
            chief = _chief_from_row(chief_row) if chief_row is not None else ChiefSpec()
            scalars = {row["key"]: _loads(row["value_json"], None) for row in conn.execute("SELECT * FROM globals")}
            return Registry(
                bots=bots,
                teams=teams,
                projects=projects,
                chief=chief,
                global_mcp_tools=[str(x) for x in (scalars.get("global_mcp_tools") or [])],
                global_tools=[str(x) for x in (scalars.get("global_tools") or [])],
            )

    @staticmethod
    def save(store_path: Path, registry: Registry) -> None:
        with db.connect(store_path) as conn:
            _sync_table(conn, table="bots", rows={b.id: _bot_to_row(b) for b in registry.bots.values()})
            _sync_table(conn, table="teams", rows={t.id: _team_to_row(t) for t in registry.teams.values()})
            _sync_table(
                conn,
                table="projects",
                rows={p.id: _project_to_row(p) for p in registry.projects.values()},
            )
            # project_state / events: upsert only — see module docstring.
            # A project's in-memory state/events at save time always wins
            # for the keys/ids present, but nothing is ever deleted here.
            for project in registry.projects.values():
                GlobalStateRepo._save_all(conn, project.id, project.state)
                EventRepo._save_all(conn, project.id, project.events)
            conn.execute("DELETE FROM chief")  # singleton: replaces the legacy-keyed row too
            conn.execute(
                "INSERT INTO chief (id, name, system_prompt, mcp_tools_json, tools_json, max_rounds) "
                "VALUES ('crew', ?, ?, ?, ?, ?)",
                (
                    registry.chief.name,
                    registry.chief.system_prompt,
                    _dumps(registry.chief.mcp_tools),
                    _dumps(registry.chief.tools),
                    registry.chief.max_rounds,
                ),
            )
            for key, value in (
                ("global_mcp_tools", registry.global_mcp_tools),
                ("global_tools", registry.global_tools),
            ):
                conn.execute(
                    "INSERT INTO globals (key, value_json) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                    (key, _dumps(value)),
                )


class GlobalStateRepo:
    """Key/value Global State for one project — the ``project_state``
    table. This is the durable path for a single key write; the running
    app (once wired) should call ``set()`` directly instead of mutating
    ``project.state`` and round-tripping the whole registry."""

    @staticmethod
    def get(store_path: Path, project_id: str) -> dict:
        with db.connect(store_path) as conn:
            return GlobalStateRepo._load(conn, project_id)

    @staticmethod
    def set(store_path: Path, project_id: str, key: str, value, *, updated_by: str = "") -> None:
        with db.connect(store_path) as conn:
            conn.execute(
                "INSERT INTO project_state (project_id, key, value_json, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(project_id, key) DO UPDATE SET "
                "value_json = excluded.value_json, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (project_id, key, _dumps(value), now_iso(), updated_by),
            )

    @staticmethod
    def _load(conn: sqlite3.Connection, project_id: str) -> dict:
        out: dict = {}
        rows = conn.execute(
            "SELECT key, value_json FROM project_state WHERE project_id = ?", (project_id,)
        )
        for row in rows:
            out[row["key"]] = _loads(row["value_json"], None)
        return out

    @staticmethod
    def _save_all(conn: sqlite3.Connection, project_id: str, state: dict) -> None:
        stamp = now_iso()
        for key, value in state.items():
            conn.execute(
                "INSERT INTO project_state (project_id, key, value_json, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, '') "
                "ON CONFLICT(project_id, key) DO UPDATE SET "
                "value_json = excluded.value_json, updated_at = excluded.updated_at",
                (project_id, key, _dumps(value), stamp),
            )


class EventRepo:
    """Append-only shared project memory — the ``events`` table.

    ``append`` is the durable, concurrency-safe path: each event is its
    own row, inserted once by its stable id (``INSERT OR IGNORE``), so two
    bots appending at the same moment can never clobber each other's entry
    the way two writers racing to rewrite one JSON blob could.
    """

    @staticmethod
    def append(store_path: Path, project_id: str, event: ProjectEvent) -> None:
        with db.connect(store_path) as conn:
            EventRepo._insert(conn, project_id, event)

    @staticmethod
    def recent(store_path: Path, project_id: str, *, limit: int = 80) -> list[ProjectEvent]:
        with db.connect(store_path) as conn:
            return EventRepo._load(conn, project_id, limit=limit)

    @staticmethod
    def _insert(conn: sqlite3.Connection, project_id: str, event: ProjectEvent) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO events (id, project_id, role, bot_id, team_id, text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event.id, project_id, event.role, event.bot_id, event.team_id, event.text, event.created_at),
        )

    @staticmethod
    def _save_all(conn: sqlite3.Connection, project_id: str, events: list[ProjectEvent]) -> None:
        for event in events:
            EventRepo._insert(conn, project_id, event)

    @staticmethod
    def _load(conn: sqlite3.Connection, project_id: str, *, limit: int = 80) -> list[ProjectEvent]:
        rows = conn.execute(
            "SELECT * FROM events WHERE project_id = ? ORDER BY created_at ASC, rowid ASC",
            (project_id,),
        ).fetchall()
        tail = rows[-limit:] if limit else list(rows)
        return [
            ProjectEvent(
                id=row["id"],
                role=row["role"],
                text=row["text"],
                bot_id=row["bot_id"],
                team_id=row["team_id"],
                created_at=row["created_at"],
            )
            for row in tail
        ]


class RunRepo:
    """One row per :class:`~backend.domain.run.AgentRun` — the queryable
    delegation ledger described in ``domain/run.py``. Owned in practice by
    ``core/supervisor.py::RunSupervisor``, but plain enough to unit-test
    on its own."""

    @staticmethod
    def create(store_path: Path, run: AgentRun) -> None:
        with db.connect(store_path) as conn:
            conn.execute(
                "INSERT INTO runs (id, project_id, bot_id, kind, parent_run_id, status, task, "
                "created_at, ended_at, summary, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.project_id,
                    run.bot_id,
                    run.kind.value,
                    run.parent_run_id,
                    run.status.value,
                    run.task,
                    run.created_at,
                    run.ended_at,
                    run.summary,
                    run.error,
                ),
            )

    @staticmethod
    def update(store_path: Path, run: AgentRun) -> None:
        with db.connect(store_path) as conn:
            conn.execute(
                "UPDATE runs SET status = ?, ended_at = ?, summary = ?, error = ? WHERE id = ?",
                (run.status.value, run.ended_at, run.summary, run.error, run.id),
            )

    @staticmethod
    def get(store_path: Path, run_id: str) -> AgentRun | None:
        with db.connect(store_path) as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return _run_from_row(row) if row is not None else None

    @staticmethod
    def children(store_path: Path, parent_run_id: str) -> list[AgentRun]:
        with db.connect(store_path) as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE parent_run_id = ? ORDER BY created_at ASC", (parent_run_id,)
            ).fetchall()
            return [_run_from_row(r) for r in rows]

    @staticmethod
    def for_bot(store_path: Path, project_id: str, bot_id: str, *, limit: int = 50) -> list[AgentRun]:
        with db.connect(store_path) as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE project_id = ? AND bot_id = ? ORDER BY created_at DESC LIMIT ?",
                (project_id, bot_id, limit),
            ).fetchall()
            return [_run_from_row(r) for r in rows]


# --- row <-> dataclass conversion -------------------------------------------


def _bot_to_row(bot: Bot) -> dict:
    return {
        "id": bot.id,
        "name": bot.name,
        "role": bot.role,
        "system_prompt": bot.system_prompt,
        "mcp_tools_json": _dumps(bot.mcp_tools),
        "team_id": bot.team_id,
        "tools_json": _dumps(bot.tools),
        "inherit_global_mcp": int(bot.inherit_global_mcp),
        "lead": int(bot.lead),
        "max_rounds": bot.max_rounds,
        "description": bot.description,
        "title": bot.title,
        "avatar": bot.avatar,
        "skills_json": _dumps(bot.skills),
    }


def _bot_from_row(row: sqlite3.Row) -> Bot:
    return Bot(
        id=row["id"],
        name=row["name"],
        role=row["role"],
        system_prompt=row["system_prompt"],
        mcp_tools=_loads(row["mcp_tools_json"], []),
        team_id=row["team_id"],
        tools=_loads(row["tools_json"], []),
        inherit_global_mcp=bool(row["inherit_global_mcp"]),
        lead=bool(row["lead"]),
        max_rounds=row["max_rounds"],
        description=row["description"],
        title=row["title"],
        avatar=row["avatar"],
        skills=_loads(row["skills_json"], []),
    )


def _team_to_row(team: Team) -> dict:
    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "bot_ids_json": _dumps(team.bot_ids),
        "lead_bot_id": team.lead_bot_id,
    }


def _team_from_row(row: sqlite3.Row) -> Team:
    return Team(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        bot_ids=_loads(row["bot_ids_json"], []),
        lead_bot_id=row["lead_bot_id"],
    )


def _project_to_row(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "team_ids_json": _dumps(project.team_ids),
        "bot_ids_json": _dumps(project.bot_ids),
        "folders_json": _dumps(project.folders),
    }


def _project_from_row(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        team_ids=_loads(row["team_ids_json"], []),
        bot_ids=_loads(row["bot_ids_json"], []),
        folders=_loads(row["folders_json"], []),
    )


def _chief_from_row(row: sqlite3.Row) -> ChiefSpec:
    return ChiefSpec(
        id=row["id"],
        name=row["name"],
        system_prompt=row["system_prompt"],
        mcp_tools=_loads(row["mcp_tools_json"], []),
        tools=_loads(row["tools_json"], []),
        max_rounds=row["max_rounds"],
    )


def _run_from_row(row: sqlite3.Row) -> AgentRun:
    return AgentRun(
        id=row["id"],
        project_id=row["project_id"],
        bot_id=row["bot_id"],
        kind=RunKind(row["kind"]),
        parent_run_id=row["parent_run_id"],
        status=RunStatus(row["status"]),
        task=row["task"],
        created_at=row["created_at"],
        ended_at=row["ended_at"],
        summary=row["summary"],
        error=row["error"],
    )
