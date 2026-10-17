"""Bot / Team / Project / Chief data models for the multi-agent platform."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _str_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("id") or "").strip()
        else:
            text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


@dataclass
class Bot:
    """One specialist agent: prompt, builtin tools, and MCP connectors it may use."""

    id: str
    name: str
    role: str
    system_prompt: str
    mcp_tools: list[str] = field(default_factory=list)
    team_id: str | None = None
    tools: list[str] = field(default_factory=list)
    inherit_global_mcp: bool = True
    lead: bool = False
    max_rounds: int = 8
    description: str = ""
    title: str = ""
    avatar: str = ""
    skills: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Bot:
        team = str(raw.get("team_id") or "").strip() or None
        return cls(
            id=str(raw.get("id") or new_id("bot")).strip(),
            name=str(raw.get("name") or raw.get("id") or "Bot").strip(),
            role=str(raw.get("role") or "general").strip().lower() or "general",
            system_prompt=str(raw.get("system_prompt") or raw.get("prompt") or "").strip(),
            mcp_tools=_str_list(raw.get("mcp_tools") or raw.get("mcp") or []),
            team_id=team,
            tools=_str_list(raw.get("tools") or raw.get("builtin_tools") or []),
            inherit_global_mcp=bool(raw.get("inherit_global_mcp", True)),
            lead=bool(raw.get("lead")),
            max_rounds=max(1, int(raw.get("max_rounds") or 8)),
            description=str(raw.get("description") or "").strip(),
            title=str(raw.get("title") or "").strip(),
            avatar=str(raw.get("avatar") or raw.get("photo") or "").strip(),
            skills=_str_list(raw.get("skills") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "system_prompt": self.system_prompt,
            "mcp_tools": list(self.mcp_tools),
            "team_id": self.team_id,
            "tools": list(self.tools),
            "inherit_global_mcp": self.inherit_global_mcp,
            "lead": self.lead,
            "max_rounds": self.max_rounds,
            "description": self.description,
            "title": self.title,
            "avatar": self.avatar,
            "skills": list(self.skills),
        }


@dataclass
class Team:
    """A named group of bots (Software Engineering, Accounting, …)."""

    id: str
    name: str
    description: str = ""
    bot_ids: list[str] = field(default_factory=list)
    lead_bot_id: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Team:
        members = raw.get("bot_ids") or raw.get("bot_members") or raw.get("members") or []
        ids = _str_list(members)
        lead = str(raw.get("lead_bot_id") or raw.get("lead") or "").strip() or None
        return cls(
            id=str(raw.get("id") or new_id("team")).strip(),
            name=str(raw.get("name") or raw.get("id") or "Team").strip(),
            description=str(raw.get("description") or "").strip(),
            bot_ids=ids,
            lead_bot_id=lead,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "bot_ids": list(self.bot_ids),
            "lead_bot_id": self.lead_bot_id,
        }


@dataclass
class ProjectEvent:
    """One entry in shared project memory (chief + bot outputs)."""

    role: str
    text: str
    bot_id: str = ""
    team_id: str = ""
    created_at: str = field(default_factory=_now)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProjectEvent:
        return cls(
            role=str(raw.get("role") or "system").strip() or "system",
            text=str(raw.get("text") or raw.get("content") or "").strip(),
            bot_id=str(raw.get("bot_id") or "").strip(),
            team_id=str(raw.get("team_id") or "").strip(),
            created_at=str(raw.get("created_at") or _now()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "bot_id": self.bot_id,
            "team_id": self.team_id,
            "created_at": self.created_at,
        }


@dataclass
class Project:
    """A unit of work with assigned teams and shared state."""

    id: str
    name: str
    description: str = ""
    team_ids: list[str] = field(default_factory=list)
    bot_ids: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    events: list[ProjectEvent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Project:
        teams = raw.get("team_ids") or raw.get("assigned_teams") or raw.get("teams") or []
        ids = _str_list(teams)
        if ids and isinstance(teams, list) and teams and isinstance(teams[0], dict):
            ids = _str_list([t.get("id") for t in teams if isinstance(t, dict)])
        state = raw.get("state") or raw.get("context") or {}
        if not isinstance(state, dict):
            state = {"note": str(state)}
        events_raw = raw.get("events") or raw.get("messages") or raw.get("history") or []
        events: list[ProjectEvent] = []
        if isinstance(events_raw, list):
            for item in events_raw:
                if isinstance(item, dict) and (item.get("text") or item.get("content")):
                    events.append(ProjectEvent.from_dict(item))
        folders = _str_list(raw.get("folders") or [])
        workspace = str(raw.get("workspace") or "").strip()
        if workspace and workspace not in folders:
            folders.insert(0, workspace)
        return cls(
            id=str(raw.get("id") or new_id("proj")).strip(),
            name=str(raw.get("name") or raw.get("id") or "Project").strip(),
            description=str(raw.get("description") or "").strip(),
            team_ids=ids,
            bot_ids=_str_list(raw.get("bot_ids") or raw.get("solo_bots") or []),
            folders=folders,
            state=dict(state),
            events=events,
        )

    def to_dict(self) -> dict[str, Any]:
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
        blob = str(text or "").strip()
        if not blob:
            return
        self.events.append(
            ProjectEvent(role=role, text=blob[:12_000], bot_id=bot_id, team_id=team_id)
        )
        if len(self.events) > cap:
            self.events = self.events[-cap:]


@dataclass
class ChiefSpec:
    """Global orchestrator — never a team member."""

    id: str = "chief"
    name: str = "Chief"
    system_prompt: str = ""
    mcp_tools: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    max_rounds: int = 12

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ChiefSpec:
        data = raw if isinstance(raw, dict) else {}
        return cls(
            id=str(data.get("id") or "chief").strip() or "chief",
            name=str(data.get("name") or "Chief").strip() or "Chief",
            system_prompt=str(data.get("system_prompt") or data.get("prompt") or "").strip(),
            mcp_tools=_str_list(data.get("mcp_tools") or data.get("mcp") or []),
            tools=_str_list(data.get("tools") or []),
            max_rounds=max(1, int(data.get("max_rounds") or 12)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "system_prompt": self.system_prompt,
            "mcp_tools": list(self.mcp_tools),
            "tools": list(self.tools),
            "max_rounds": self.max_rounds,
        }

    def as_bot(self) -> Bot:
        return Bot(
            id=self.id,
            name=self.name,
            role="chief",
            system_prompt=self.system_prompt,
            mcp_tools=list(self.mcp_tools),
            team_id=None,
            tools=list(self.tools),
            inherit_global_mcp=True,
            lead=False,
            max_rounds=self.max_rounds,
            description="Chief of Staff. Group chat talks to Chief; specialists may be 1:1.",
            title="Chief of Staff",
        )


@dataclass
class Registry:
    """In-memory catalog of bots, teams, projects, and Chief."""

    bots: dict[str, Bot] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=dict)
    projects: dict[str, Project] = field(default_factory=dict)
    chief: ChiefSpec = field(default_factory=ChiefSpec)
    global_mcp_tools: list[str] = field(default_factory=list)
    global_tools: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Registry:
        data = raw if isinstance(raw, dict) else {}
        bots: dict[str, Bot] = {}
        for item in data.get("bots") or []:
            if not isinstance(item, dict):
                continue
            bot = Bot.from_dict(item)
            if bot.id:
                bots[bot.id] = bot
        teams: dict[str, Team] = {}
        for item in data.get("teams") or []:
            if not isinstance(item, dict):
                continue
            team = Team.from_dict(item)
            if team.id:
                teams[team.id] = team
        projects: dict[str, Project] = {}
        for item in data.get("projects") or []:
            if not isinstance(item, dict):
                continue
            project = Project.from_dict(item)
            if project.id:
                projects[project.id] = project
        return cls(
            bots=bots,
            teams=teams,
            projects=projects,
            chief=ChiefSpec.from_dict(data.get("chief") if isinstance(data.get("chief"), dict) else {}),
            global_mcp_tools=_str_list(data.get("global_mcp_tools") or data.get("global_mcp") or []),
            global_tools=_str_list(data.get("global_tools") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "bots": [bot.to_dict() for bot in self.bots.values()],
            "teams": [team.to_dict() for team in self.teams.values()],
            "projects": [project.to_dict() for project in self.projects.values()],
            "chief": self.chief.to_dict(),
            "global_mcp_tools": list(self.global_mcp_tools),
            "global_tools": list(self.global_tools),
        }

    def bot_members(self, team: Team) -> list[Bot]:
        return [self.bots[bid] for bid in team.bot_ids if bid in self.bots]

    def assigned_teams(self, project: Project) -> list[Team]:
        return [self.teams[tid] for tid in project.team_ids if tid in self.teams]

    def project_bots(self, project: Project) -> list[Bot]:
        seen: set[str] = set()
        out: list[Bot] = []
        for team in self.assigned_teams(project):
            for bot in self.bot_members(team):
                if bot.id in seen:
                    continue
                seen.add(bot.id)
                out.append(bot)
        for bid in project.bot_ids:
            if bid in seen or bid not in self.bots:
                continue
            seen.add(bid)
            out.append(self.bots[bid])
        return out
