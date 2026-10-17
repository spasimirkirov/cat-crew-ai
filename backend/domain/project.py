"""A unit of work with assigned teams and shared (Global) state.

``Project.state`` and ``Project.events`` keep the same shape callers already
depend on (``platform/chief.py::describe_project``, ``platform/runner.py``,
etc. read them directly) — what changes is how they get there. Before, they
were slices of one JSON blob loaded/saved as a whole. Now ``state/`` repositories
populate them from the ``project_state`` and ``events`` SQLite tables, and
mutations go back through ``GlobalStateRepo``/``EventRepo`` instead of a
full-registry rewrite. See ``backend/db/state/repositories.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ids import new_id, now_iso, str_list


@dataclass
class ProjectEvent:
    """One entry in shared project memory (Chief + bot outputs).

    ``id`` makes each event a stable, independently-persistable row in the
    ``events`` table (``INSERT OR IGNORE`` by id) instead of a slice of one
    JSON blob — two bots appending events concurrently no longer risk
    clobbering each other's entries.
    """

    role: str
    text: str
    bot_id: str = ""
    team_id: str = ""
    created_at: str = field(default_factory=now_iso)
    id: str = field(default_factory=lambda: new_id("evt"))

    @classmethod
    def from_dict(cls, raw: dict) -> "ProjectEvent":
        return cls(
            role=str(raw.get("role") or "system").strip() or "system",
            text=str(raw.get("text") or raw.get("content") or "").strip(),
            bot_id=str(raw.get("bot_id") or "").strip(),
            team_id=str(raw.get("team_id") or "").strip(),
            created_at=str(raw.get("created_at") or now_iso()),
            id=str(raw.get("id") or new_id("evt")),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "text": self.text,
            "bot_id": self.bot_id,
            "team_id": self.team_id,
            "created_at": self.created_at,
        }


@dataclass
class Project:
    id: str
    name: str
    description: str = ""
    team_ids: list[str] = field(default_factory=list)
    bot_ids: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    events: list[ProjectEvent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "Project":
        teams = raw.get("team_ids") or raw.get("assigned_teams") or raw.get("teams") or []
        ids = str_list(teams)
        if ids and isinstance(teams, list) and teams and isinstance(teams[0], dict):
            ids = str_list([t.get("id") for t in teams if isinstance(t, dict)])
        state = raw.get("state") or raw.get("context") or {}
        if not isinstance(state, dict):
            state = {"note": str(state)}
        events_raw = raw.get("events") or raw.get("messages") or raw.get("history") or []
        events: list[ProjectEvent] = []
        if isinstance(events_raw, list):
            for item in events_raw:
                if isinstance(item, dict) and (item.get("text") or item.get("content")):
                    events.append(ProjectEvent.from_dict(item))
        folders = str_list(raw.get("folders") or [])
        workspace = str(raw.get("workspace") or "").strip()
        if workspace and workspace not in folders:
            folders.insert(0, workspace)
        return cls(
            id=str(raw.get("id") or new_id("proj")).strip(),
            name=str(raw.get("name") or raw.get("id") or "Project").strip(),
            description=str(raw.get("description") or "").strip(),
            team_ids=ids,
            bot_ids=str_list(raw.get("bot_ids") or raw.get("solo_bots") or []),
            folders=folders,
            state=dict(state),
            events=events,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "team_ids": list(self.team_ids),
            "bot_ids": list(self.bot_ids),
            "folders": list(self.folders),
            "state": dict(self.state),
            "events": [item.to_dict() for item in self.events],
        }

    def remember(
        self,
        text: str,
        *,
        role: str = "bot",
        bot_id: str = "",
        team_id: str = "",
        cap: int = 80,
    ) -> None:
        """In-memory convenience used by callers that build a Project ad hoc
        (tests, the marketplace builder). ``state/repositories.EventRepo``
        is the durable path used by the running app — see its ``append()``.
        """
        blob = str(text or "").strip()
        if not blob:
            return
        self.events.append(
            ProjectEvent(role=role, text=blob[:12_000], bot_id=bot_id, team_id=team_id)
        )
        if len(self.events) > cap:
            self.events = self.events[-cap:]
