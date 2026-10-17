-- CatCrewAI platform state — replaces data/platform/registry.json as the live
-- store (see the Master Architecture Document, section 6). Every mutation
-- here is one SQLite transaction: no half-written files, no lost catalog on
-- a mid-write crash, and events are append-only rows instead of slices of
-- one JSON blob.
--
-- Applied with `CREATE TABLE IF NOT EXISTS` so it is safe to run against an
-- existing database on every startup (see db.py::ensure_schema).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    mcp_tools_json TEXT NOT NULL DEFAULT '[]',
    team_id TEXT,
    tools_json TEXT NOT NULL DEFAULT '[]',
    inherit_global_mcp INTEGER NOT NULL DEFAULT 1,
    lead INTEGER NOT NULL DEFAULT 0,
    max_rounds INTEGER NOT NULL DEFAULT 8,
    description TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    skills_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    bot_ids_json TEXT NOT NULL DEFAULT '[]',
    lead_bot_id TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    team_ids_json TEXT NOT NULL DEFAULT '[]',
    bot_ids_json TEXT NOT NULL DEFAULT '[]',
    folders_json TEXT NOT NULL DEFAULT '[]'
);

-- Global State: key/value per project, replaces the flat Project.state dict.
-- Each row is versioned by updated_at/updated_by so a future UI can show
-- "who changed this and when" instead of a silent overwrite.
CREATE TABLE IF NOT EXISTS project_state (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, key)
);

-- Append-only shared memory. Replaces the old 80-entry-total flat cap —
-- retention is a query-time LIMIT, not a silent drop on write, and two bots
-- appending concurrently can never clobber each other (each event is its
-- own row, inserted once by its stable id).
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    bot_id TEXT NOT NULL DEFAULT '',
    team_id TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_project_created
    ON events (project_id, created_at);

CREATE TABLE IF NOT EXISTS chief (
    id TEXT PRIMARY KEY DEFAULT 'chief',
    name TEXT NOT NULL DEFAULT 'Chief',
    system_prompt TEXT NOT NULL DEFAULT '',
    mcp_tools_json TEXT NOT NULL DEFAULT '[]',
    tools_json TEXT NOT NULL DEFAULT '[]',
    max_rounds INTEGER NOT NULL DEFAULT 12
);

-- Small scalar globals (global_mcp_tools / global_tools lists today).
CREATE TABLE IF NOT EXISTS globals (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

-- One row per AgentRun (see backend/domain/run.py and core/supervisor.py).
-- This is what makes "how is the QA bot's delegation doing" a query instead
-- of "block until the whole nested call stack unwinds."
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_run_id TEXT,
    status TEXT NOT NULL,
    task TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    ended_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs (parent_run_id);
CREATE INDEX IF NOT EXISTS idx_runs_project_bot ON runs (project_id, bot_id);

-- Reserved for the next migration phase (not yet written or read by any
-- code path in this pass — see the Master Architecture Document section 8
-- "migration plan" and the follow-ups called out in the README). Defined
-- now so that phase does not need another schema migration.
CREATE TABLE IF NOT EXISTS worker_state (
    project_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, bot_id, key)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    bot_id TEXT NOT NULL DEFAULT 'chief',
    session_id TEXT NOT NULL,
    parent_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages (session_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_project_bot ON chat_messages (project_id, bot_id);
