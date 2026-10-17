"""Row/object -> wire-shape serializers (``docs/API.md`` §1).

Pure functions: no HTTP, no globals beyond reading the registry, the ledger and
the in-memory activity board. Shared by the REST routes and the WebSocket event
enrichment so an object looks identical whichever way it reaches the client.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import BASE_DIR
from backend.core.messages import clean_summary
from backend.db import ledger
from backend.platform import activity_board
from backend.platform.essentials import essential_builtins, essential_slugs_for_agent
from backend.platform.models import Bot, Project, Registry
from backend.platform.orchestrator import (
    ROOM_AVATAR_FILE,
    ROOM_DESCRIPTION,
    ROOM_ID,
    ROOM_NAME,
    ROOM_TITLE,
    without_room,
)
from backend.platform.registry import load_registry

_AVATAR_DIR = BASE_DIR / "assets" / "images" / "avatars"
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

_ACCENTS = ["#38BDF8", "#34D399", "#A78BFA", "#FBBF24", "#FB7185", "#22D3EE"]
_CORE_COLORS: dict[str, str] = {}


def _core_colors() -> dict[str, str]:
    if not _CORE_COLORS:
        try:
            from backend.platform.catcrew import CATCREW_MEMBERS

            for spec in CATCREW_MEMBERS:
                _CORE_COLORS[spec["id"]] = str(spec.get("color") or "")
        except Exception:  # noqa: BLE001
            pass
    return _CORE_COLORS


def iso_from_ts(ts: float | None) -> str | None:
    if not ts or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def color_for(bot_id: str) -> str:
    known = _core_colors().get(bot_id)
    if known:
        return known
    digest = hashlib.sha1(bot_id.encode("utf-8")).digest()
    return _ACCENTS[digest[0] % len(_ACCENTS)]


def avatar_file(bot: Bot) -> Path | None:
    """The image to serve for ``bot`` (its own photo, else the bundled one), or None."""
    raw = (bot.avatar or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate
        if candidate.suffix.lower() in _IMAGE_EXT and candidate.is_file():
            return candidate
    bundled = _AVATAR_DIR / f"{bot.id}.png"
    if bundled.is_file():
        return bundled
    return None


class Assignments:
    """Playbook / Letta-tool assignments per agent, loaded with two queries.

    ``playbooks``/``letta_tools`` on an agent are its explicit assignments plus
    everything flagged global (global items attach to every agent)."""

    def __init__(self, skills: dict[str, list[str]], tools: dict[str, list[str]],
                 g_skills: list[str], g_tools: list[str]) -> None:
        self._skills, self._tools = skills, tools
        self._g_skills, self._g_tools = g_skills, g_tools

    @classmethod
    def load(cls) -> "Assignments":
        skills: dict[str, list[str]] = {}
        tools: dict[str, list[str]] = {}
        g_skills: list[str] = []
        g_tools: list[str] = []
        try:
            conn = ledger._connect()
            try:
                for r in conn.execute(
                    "SELECT a.agent_id, s.name FROM agent_skills a JOIN skills s ON s.id = a.skill_id ORDER BY s.name"
                ):
                    skills.setdefault(r["agent_id"], []).append(r["name"])
                for r in conn.execute(
                    "SELECT a.agent_id, t.name FROM agent_tools a JOIN tools t ON t.id = a.tool_id ORDER BY t.name"
                ):
                    tools.setdefault(r["agent_id"], []).append(r["name"])
                g_skills = [r["name"] for r in conn.execute("SELECT name FROM skills WHERE is_global = 1 ORDER BY name")]
                g_tools = [r["name"] for r in conn.execute("SELECT name FROM tools WHERE is_global = 1 ORDER BY name")]
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - a ledger hiccup must not break listing agents
            pass
        return cls(skills, tools, g_skills, g_tools)

    def playbooks(self, agent_id: str) -> list[str]:
        return sorted(set(self._skills.get(agent_id, [])) | set(self._g_skills))

    def letta_tools(self, agent_id: str) -> list[str]:
        return sorted(set(self._tools.get(agent_id, [])) | set(self._g_tools))


def agent_view(
    bot: Bot, *, with_prompt: bool = False, assignments: Assignments | None = None
) -> dict[str, Any]:
    assignments = assignments or Assignments.load()
    out: dict[str, Any] = {
        "id": bot.id,
        "name": bot.name,
        "role": bot.role,
        "title": bot.title or bot.role.replace("_", " ").title(),
        "description": bot.description,
        "avatar": f"/api/agents/{bot.id}/avatar" if avatar_file(bot) else "",
        "color": color_for(bot.id),
        "skills": list(bot.skills),
        "playbooks": assignments.playbooks(bot.id),
        "tools": list(bot.tools),
        "letta_tools": assignments.letta_tools(bot.id),
        "mcp_tools": list(bot.mcp_tools),
        # What this cat is never without (platform/essentials.py): built-ins of its role plus the registry
        # tools that are essential for it. Read-only: the Tools page cannot unassign them.
        "essential_tools": list(dict.fromkeys([*essential_builtins(bot.role), *essential_slugs_for_agent(bot.id)])),
        "max_rounds": bot.max_rounds,
        "is_system": bool(bot.is_system),
    }
    if with_prompt:
        out["system_prompt"] = bot.system_prompt
    return out


def room_agent(registry: Registry) -> dict[str, Any]:
    """Cat Crew (the orchestrator / system chat) as a pseudo-agent: the first
    sidebar card. It is never a specialist — no playbooks, no tools, no matrix column."""
    chief = registry.chief.as_bot()
    view = agent_view(chief, assignments=Assignments({}, {}, [], []))
    view.update(
        id=ROOM_ID,
        name=ROOM_NAME,
        role="room",
        title=ROOM_TITLE,
        description=ROOM_DESCRIPTION,
        avatar=f"/api/agents/{ROOM_ID}/avatar" if room_avatar_file() else "",
        color="#38BDF8",
        is_system=True,
        playbooks=[],
        letta_tools=[],
        skills=[],
        tools=[],
        mcp_tools=[],
        essential_tools=[],
    )
    return view


def room_avatar_file() -> Path | None:
    """The bundled ``default.png`` Cat Crew wears."""
    path = _AVATAR_DIR / ROOM_AVATAR_FILE
    return path if path.is_file() else None


def activity_view(bot_id: str) -> dict[str, Any]:
    status = activity_board.display_status(bot_id)
    text, ts = activity_board.get_activity(bot_id)
    return {
        "bot_id": bot_id,
        "status": status,
        "state": activity_board.get_state(bot_id),
        "text": text if status != "idle" else "",
        "updated_at": iso_from_ts(ts),
    }


def project_view(project: Project) -> dict[str, Any]:
    state = project.state or {}
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "folders": list(project.folders),
        "bot_ids": list(project.bot_ids),
        "created_at": state.get("created_at") or None,
        "phase": str(state.get("sop_phase") or ""),
    }


def _member_joined_at(project_id: str) -> dict[str, str]:
    """``{bot_id: added_at}`` from the ledger (the moment the orchestrator staffed it)."""
    try:
        from backend.db.ledger import _connect  # read-only convenience query

        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT agent_id, added_at FROM project_members WHERE project_id = ? ORDER BY added_at",
                (project_id,),
            ).fetchall()
            return {r["agent_id"]: r["added_at"] for r in rows}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {}


def roster_entry(
    bot: Bot, joined_at: str | None = None, *, assignments: Assignments | None = None
) -> dict[str, Any]:
    return {
        "bot_id": bot.id,
        "agent": agent_view(bot, assignments=assignments),
        "is_room": False,
        "activity": activity_view(bot.id),
        "joined_at": joined_at,
    }


def roster_for(project: Project, registry: Registry | None = None) -> list[dict[str, Any]]:
    """Cat Crew first, then the persisted roster, then bots the orchestrator
    staffed mid-run (the ledger is merged in: its write can land before the
    registry's)."""
    registry = registry or load_registry()
    joined = _member_joined_at(project.id)
    assignments = Assignments.load()
    entries: list[dict[str, Any]] = [
        {
            "bot_id": ROOM_ID,
            "agent": room_agent(registry),
            "is_room": True,
            "activity": activity_view(ROOM_ID),
            "joined_at": None,
        }
    ]
    seen: set[str] = set()
    for bid in list(project.bot_ids) + list(joined):
        bot = registry.bots.get(bid)
        if bot is None or bid in seen:
            continue
        seen.add(bid)
        entries.append(roster_entry(bot, joined.get(bid), assignments=assignments))
    return entries


def task_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "assigned_to": row["assigned_to"],
        "created_by": row.get("created_by") or "lead",
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "status": row.get("status") or "pending",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "latest_summary": clean_summary(row.get("latest_summary") or ""),
        # Work-order fields (see the ledger migration): what kind of task, what it belongs to,
        # how it is accepted, who raised it.
        "kind": row.get("kind") or "build",
        "parent_id": row.get("parent_id") or "",
        "acceptance": row.get("acceptance") or "",
        "depends_on": [d for d in str(row.get("depends_on") or "").split(",") if d],
        "attempt": int(row.get("attempt") or 0),
        "reported_by": row.get("reported_by") or "",
    }


def result_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "final_state": row.get("final_state") or "",
        "summary": clean_summary(row.get("summary") or ""),
        "created_at": row.get("created_at") or "",
    }


def change_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id") or row.get("diff_uuid") or "",
        "task_id": row.get("task_id") or "",
        "project_id": row.get("project_id") or "",
        "agent_id": row.get("agent_id") or "",
        "file_path": row.get("file_path") or "",
        "diff_uuid": row.get("diff_uuid") or "",
        "created_at": row.get("created_at") or "",
    }


_SKILL_TYPES = {"bundled", "custom", "marketplace", "auto_generated"}


def _display_path(path: str) -> str:
    """Repo-relative, forward-slash path for the UI (no machine-specific prefix)."""
    if not path:
        return ""
    try:
        return Path(path).resolve().relative_to(BASE_DIR).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def skill_view(
    row: dict[str, Any],
    *,
    agent_ids: list[str] | None = None,
    body: str | None = None,
    locked_agent_ids: list[str] | None = None,
) -> dict[str, Any]:
    from backend.agent_context import is_bundled_skill

    raw_type = str(row.get("type") or "")
    path = str(row.get("playbook_path") or "")
    if raw_type in _SKILL_TYPES:
        kind = raw_type
    elif path and is_bundled_skill(Path(path)):
        kind = "bundled"
    else:  # legacy rows carry type 'system'
        kind = "bundled" if raw_type in ("", "system") and not path else "custom"
    roles = [r.strip() for r in str(row.get("target_roles") or "").split(",") if r.strip()]
    out: dict[str, Any] = {
        "id": row["id"],
        "name": row.get("name") or row.get("slug") or "",
        "description": row.get("description") or "",
        "type": kind,
        "version": row.get("version") or "1.0.0",
        "target_roles": roles,
        "playbook_path": _display_path(path),
        "source_url": row.get("source_url") or "",
        "is_global": bool(row.get("is_global")),
        "agent_ids": sorted(without_room(agent_ids)) if agent_ids is not None else [],
        # Cats for which this playbook is essential: always on, cannot be unassigned or deleted.
        "locked_agent_ids": sorted(locked_agent_ids or []),
    }
    if body is not None:
        out["body"] = body
    return out


def tool_view(
    row: dict[str, Any],
    *,
    agent_ids: list[str] | None = None,
    schema: Any = None,
    locked_agent_ids: list[str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row["id"],
        "name": row.get("name") or row.get("slug") or "",
        "description": row.get("docstring") or row.get("description") or "",
        "handler": row.get("handler_path") or "",
        "is_global": bool(row.get("is_global")),
        "agent_ids": sorted(without_room(agent_ids)) if agent_ids is not None else [],
        # Cats for which this tool is essential: always on, cannot be unassigned or deleted.
        "locked_agent_ids": sorted(locked_agent_ids or []),
    }
    if schema is not None:
        out["schema"] = schema
    return out
