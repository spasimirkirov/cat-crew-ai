"""Cat Crew — the singleton orchestrator (config). Never a team member, never an
assignable agent. The literals below mirror ``platform/orchestrator.py`` (the domain
layer must not import the platform package); ``tests/test_orchestrator.py`` keeps
them in sync."""

from __future__ import annotations

from dataclasses import dataclass, field

from .bot import Bot
from .ids import str_list


@dataclass
class ChiefSpec:
    id: str = "crew"
    name: str = "Cat Crew"
    system_prompt: str = ""
    mcp_tools: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    max_rounds: int = 12

    def __post_init__(self) -> None:
        # Rows saved before the rename carry id "chief" / name "Chief".
        self.id = "crew"
        if not self.name or self.name.strip().lower() in ("chief", "project room"):
            self.name = "Cat Crew"

    @classmethod
    def from_dict(cls, raw: dict | None) -> "ChiefSpec":
        data = raw if isinstance(raw, dict) else {}
        return cls(
            id="crew",
            name=str(data.get("name") or "Cat Crew").strip() or "Cat Crew",
            system_prompt=str(data.get("system_prompt") or data.get("prompt") or "").strip(),
            mcp_tools=str_list(data.get("mcp_tools") or data.get("mcp") or []),
            tools=str_list(data.get("tools") or []),
            max_rounds=max(1, int(data.get("max_rounds") or 12)),
        )

    def to_dict(self) -> dict:
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
            role="orchestrator",
            system_prompt=self.system_prompt,
            mcp_tools=list(self.mcp_tools),
            team_id=None,
            tools=list(self.tools),
            inherit_global_mcp=True,
            lead=False,
            max_rounds=self.max_rounds,
            description=(
                "Orchestrator & system chat. Talk to the primary model freely: steer progress, "
                "analyse requirements, and let it hand the work to the right specialist cats."
            ),
            title="Orchestrator",
        )
