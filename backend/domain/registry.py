"""In-memory catalog view: bots, teams, projects, and Chief.

Same shape and lookup methods as the original ``platform/models.py::Registry``
— callers across ``platform/`` and the tests build and query this object the
same way. What changed is who fills it in: ``backend/db/state/repositories.py``
loads one from SQLite (or, before migration, straight from a legacy
``registry.json``) instead of ``json.loads``-ing one big blob.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bot import Bot
from .chief import ChiefSpec
from .ids import str_list
from .project import Project
from .team import Team


@dataclass
class Registry:
    bots: dict[str, Bot] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=dict)
    projects: dict[str, Project] = field(default_factory=dict)
    chief: ChiefSpec = field(default_factory=ChiefSpec)
    global_mcp_tools: list[str] = field(default_factory=list)
    global_tools: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Registry":
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
            global_mcp_tools=str_list(data.get("global_mcp_tools") or data.get("global_mcp") or []),
            global_tools=str_list(data.get("global_tools") or []),
        )

    def to_dict(self) -> dict:
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
