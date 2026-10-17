"""One specialist agent: prompt, builtin tools, and MCP connectors it may use."""

from __future__ import annotations

from dataclasses import dataclass, field

from .ids import new_id, str_list


@dataclass
class Bot:
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
    def from_dict(cls, raw: dict) -> "Bot":
        team = str(raw.get("team_id") or "").strip() or None
        return cls(
            id=str(raw.get("id") or new_id("bot")).strip(),
            name=str(raw.get("name") or raw.get("id") or "Bot").strip(),
            role=str(raw.get("role") or "general").strip().lower() or "general",
            system_prompt=str(raw.get("system_prompt") or raw.get("prompt") or "").strip(),
            mcp_tools=str_list(raw.get("mcp_tools") or raw.get("mcp") or []),
            team_id=team,
            tools=str_list(raw.get("tools") or raw.get("builtin_tools") or []),
            inherit_global_mcp=bool(raw.get("inherit_global_mcp", True)),
            lead=bool(raw.get("lead")),
            max_rounds=max(1, int(raw.get("max_rounds") or 8)),
            description=str(raw.get("description") or "").strip(),
            title=str(raw.get("title") or "").strip(),
            avatar=str(raw.get("avatar") or raw.get("photo") or "").strip(),
            skills=str_list(raw.get("skills") or []),
        )

    def to_dict(self) -> dict:
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
