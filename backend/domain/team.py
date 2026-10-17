"""A named group of bots (Software Engineering, Accounting, ...)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .ids import new_id, str_list


@dataclass
class Team:
    id: str
    name: str
    description: str = ""
    bot_ids: list[str] = field(default_factory=list)
    lead_bot_id: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "Team":
        members = raw.get("bot_ids") or raw.get("bot_members") or raw.get("members") or []
        ids = str_list(members)
        lead = str(raw.get("lead_bot_id") or raw.get("lead") or "").strip() or None
        return cls(
            id=str(raw.get("id") or new_id("team")).strip(),
            name=str(raw.get("name") or raw.get("id") or "Team").strip(),
            description=str(raw.get("description") or "").strip(),
            bot_ids=ids,
            lead_bot_id=lead,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "bot_ids": list(self.bot_ids),
            "lead_bot_id": self.lead_bot_id,
        }
