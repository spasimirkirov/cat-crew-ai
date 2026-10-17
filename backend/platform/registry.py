"""Load / save the platform catalog (JSON, optional YAML) and a Python builder."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..config import BASE_DIR, CATCREW_HOME
from .models import Bot, ChiefSpec, Project, Registry, new_id

STORE_PATH = BASE_DIR / "data" / "platform" / "registry.json"
DEFAULTS_PATH = Path(__file__).resolve().parent / "defaults.json"

_cache: Registry | None = None


def store_path() -> Path:
    return STORE_PATH


def _read_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]

            data = yaml.safe_load(text)
        except ImportError:
            data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"{path} did not contain a YAML object.")
        return data
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a JSON object.")
    return data


def load_registry(*, refresh: bool = False) -> Registry:
    global _cache
    if _cache is not None and not refresh:
        return _cache
    if STORE_PATH.is_file():
        try:
            _cache = Registry.from_dict(_read_mapping(STORE_PATH))
            return _cache
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    seeded = _defaults()
    _cache = seeded
    if seeded.bots or seeded.projects:
        save_registry(seeded)
    return _cache


def save_registry(registry: Registry | None = None) -> Registry:
    global _cache
    current = registry if registry is not None else load_registry()
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(current.to_dict(), ensure_ascii=False, indent=2)
    # Atomic replace: the sidebar now re-reads the catalog the moment a bot is
    # staffed mid-pipeline, so a reader must never observe a half-written file.
    tmp = STORE_PATH.with_name(STORE_PATH.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, STORE_PATH)
    _cache = current
    return current


def reset_registry_cache() -> None:
    global _cache
    _cache = None


def _defaults() -> Registry:
    if DEFAULTS_PATH.is_file():
        try:
            return Registry.from_dict(_read_mapping(DEFAULTS_PATH))
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    return Registry()


def load_config_file(path: str | Path, *, save: bool = True) -> Registry:
    """Replace the live catalog from a JSON or YAML file."""
    data = _read_mapping(Path(path).expanduser())
    registry = Registry.from_dict(data)
    if save:
        save_registry(registry)
    else:
        global _cache
        _cache = registry
    return registry


def extra_config_paths() -> list[Path]:
    """Optional user overlay: C:\\CatCrewAI\\platform\\*.json."""
    root = CATCREW_HOME / "platform"
    if not root.is_dir():
        return []
    return sorted(root.glob("*.json")) + sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))


def get_project(project_id: str) -> Project | None:
    if not project_id:
        return None
    return load_registry().projects.get(project_id)


def default_project_id() -> str:
    """First catalog project, if any. Empty catalog is valid."""
    registry = load_registry()
    if registry.projects:
        return next(iter(registry.projects))
    return ""


def get_bot(bot_id: str) -> Bot | None:
    if not bot_id:
        return None
    return load_registry().bots.get(bot_id)


def find_bot(token: str) -> Bot | None:
    """Resolve a bot by id or name (case-insensitive). Catalog, not only the project."""
    raw = (token or "").strip()
    if not raw:
        return None
    registry = load_registry()
    if raw in registry.bots:
        return registry.bots[raw]
    needle = raw.lower()
    for bot in registry.bots.values():
        if bot.id.lower() == needle or bot.name.lower() == needle:
            return bot
        first = bot.name.split()[0].lower() if bot.name else ""
        if first and first == needle:
            return bot
    return None


def staff_bot_on_project(registry: Registry, project: Project, bot: Bot) -> str:
    """Add the bot directly to the project's roster. People exist before a checkbox."""
    notes: list[str] = []
    if bot.id not in project.bot_ids:
        project.bot_ids.append(bot.id)
        notes.append(f"Assigned {bot.name} [{bot.id}] to {project.name}.")
    if notes:
        save_registry(registry)
        # Real-time roster: the bot is now on the project's persisted
        # roster, so tell every client (the roster also merges the ledger's
        # project_members, but this covers a project that has no ledger row
        # yet, e.g. an @mention before any pipeline phase ran).
        from .bus import emit_bot_staffed

        emit_bot_staffed(project.id, bot.id)
    return " ".join(notes)


def save_bot(bot: Bot) -> Registry:
    registry = load_registry()
    registry.bots[bot.id] = bot
    return save_registry(registry)


def delete_bot(bot_id: str) -> Registry:
    registry = load_registry()
    existing = registry.bots.get(bot_id)
    if existing is not None and existing.is_system:
        # System roster members (the CatCrewAI core 8) are not deletable from
        # the catalog — they can still be removed from a given project.
        return registry
    registry.bots.pop(bot_id, None)
    for project in registry.projects.values():
        project.bot_ids = [bid for bid in project.bot_ids if bid != bot_id]
    return save_registry(registry)


def save_project(project: Project) -> Registry:
    registry = load_registry()
    registry.projects[project.id] = project
    return save_registry(registry)


def delete_project(project_id: str) -> Registry:
    registry = load_registry()
    registry.projects.pop(project_id, None)
    return save_registry(registry)


class PlatformBuilder:
    """Fluent API: register bots and assign them directly to a project."""

    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry or Registry()

    def global_mcp(self, *connector_ids: str) -> PlatformBuilder:
        for cid in connector_ids:
            text = str(cid).strip()
            if text and text not in self.registry.global_mcp_tools:
                self.registry.global_mcp_tools.append(text)
        return self

    def global_tools(self, *names: str) -> PlatformBuilder:
        for name in names:
            text = str(name).strip()
            if text and text not in self.registry.global_tools:
                self.registry.global_tools.append(text)
        return self

    def chief(
        self,
        *,
        name: str = "Cat Crew",
        prompt: str = "",
        mcp_tools: list[str] | None = None,
        tools: list[str] | None = None,
        max_rounds: int = 12,
    ) -> PlatformBuilder:
        self.registry.chief = ChiefSpec(
            name=name,
            system_prompt=prompt,
            mcp_tools=list(mcp_tools or []),
            tools=list(tools or []),
            max_rounds=max_rounds,
        )
        return self

    def bot(
        self,
        bot_id: str,
        name: str,
        *,
        role: str,
        prompt: str,
        tools: list[str] | None = None,
        mcp_tools: list[str] | None = None,
        lead: bool = False,
        description: str = "",
        title: str = "",
        skills: list[str] | None = None,
        avatar: str = "",
        max_rounds: int = 8,
        inherit_global_mcp: bool = True,
    ) -> PlatformBuilder:
        row = Bot(
            id=bot_id or new_id("bot"),
            name=name,
            role=role,
            system_prompt=prompt,
            mcp_tools=list(mcp_tools or []),
            tools=list(tools or []),
            inherit_global_mcp=inherit_global_mcp,
            lead=lead,
            max_rounds=max_rounds,
            description=description,
            title=title,
            avatar=avatar,
            skills=list(skills or []),
        )
        self.registry.bots[row.id] = row
        return self

    def project(
        self,
        project_id: str,
        name: str,
        description: str = "",
        *,
        bots: list[str] | None = None,
        folders: list[str] | None = None,
        state: dict[str, Any] | None = None,
    ) -> PlatformBuilder:
        row = Project(
            id=project_id,
            name=name,
            description=description,
            bot_ids=list(bots or []),
            folders=list(folders or []),
            state=dict(state or {}),
        )
        self.registry.projects[row.id] = row
        return self

    def assign_bot(self, project_id: str, bot_id: str) -> PlatformBuilder:
        project = self.registry.projects.get(project_id)
        if project is not None and bot_id not in project.bot_ids:
            project.bot_ids.append(bot_id)
        return self

    def save(self) -> Registry:
        return save_registry(self.registry)

    def build(self) -> Registry:
        return self.registry
