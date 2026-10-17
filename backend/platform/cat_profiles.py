"""Cat profiles: who each cat is, and the mask it puts on the engine.

A profile is a *view* over the registry (``registry.json`` / ``defaults.json``, the backend
configuration that already defines every cat), not a second copy of it: one source of truth, so a
prompt edited on the Agents page is the prompt the engine runs. ``allowed_tools`` is what the
cat's turn is really bound to: the built-in pack for its role (essentials first, see
``platform/essentials.py``) plus the registry tools assigned to it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .models import Bot
from .toolset import builtin_names_for_role

# Font Awesome icon name (free-solid) per role, for clients that want an icon without an avatar image.
_ROLE_ICON = {
    "prompt_engineer": "faWandMagicSparkles",
    "vanguard": "faCompass",
    "developer": "faCode",
    "designer": "faPalette",
    "qa": "faShieldHalved",
    "devops": "faServer",
    "legal": "faScaleBalanced",
    "marketing": "faBullhorn",
}
_DEFAULT_ICON = "faPaw"


@dataclass(frozen=True)
class CatProfile:
    id: str
    name: str
    role: str
    system_prompt: str
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    avatar_icon: str = _DEFAULT_ICON

    def to_dict(self, *, with_prompt: bool = True) -> dict:
        out = asdict(self)
        out["allowed_tools"] = list(self.allowed_tools)
        if not with_prompt:
            out.pop("system_prompt")
        return out


def registry_tool_slugs(bot_id: str) -> list[str]:
    """Registry (Letta-style) tools bound to the cat: its assignments plus its essentials."""
    from backend.db import ledger as db

    from .essentials import essential_slugs_for_agent

    slugs: list[str] = []
    try:
        slugs = [str(t.get("slug") or "") for t in db.tools_for_agent(bot_id)]
    except Exception:  # noqa: BLE001 - a profile must render even before the database exists
        pass
    for slug in essential_slugs_for_agent(bot_id):
        if slug not in slugs:
            slugs.append(slug)
    return [s for s in slugs if s]


def profile_for(bot: Bot) -> CatProfile:
    names = [n for n in builtin_names_for_role(bot.role, bot.tools or None) if n != "ask_user"]
    for slug in registry_tool_slugs(bot.id):
        if slug not in names:
            names.append(slug)
    return CatProfile(
        id=bot.id,
        name=bot.name,
        role=bot.role,
        system_prompt=bot.system_prompt,
        allowed_tools=tuple(names),
        avatar_icon=_ROLE_ICON.get((bot.role or "").strip().lower(), _DEFAULT_ICON),
    )


def load_cat_profiles(registry=None) -> list[CatProfile]:
    """Every cat in the registry as a profile (Cat Crew, the orchestrator, is not a cat)."""
    if registry is None:
        from .registry import load_registry

        registry = load_registry()
    return [profile_for(bot) for bot in registry.bots.values()]
