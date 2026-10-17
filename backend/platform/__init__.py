"""Multi-agent platform: Bots, Projects, and the Chief orchestrator."""

from .chief import (
    ChiefBot,
    build_chief_prompt,
    build_direct_bot_prompt,
    chief_tool_schema,
    execute_chief_tool,
    schema_for_direct_bot,
)
from .models import Bot, ChiefSpec, Project, Registry
from .catcrew import migrate_legacy_arcane, seed_catcrew
from .presets import marketplace_builder, seed_marketplace
from .registry import (
    PlatformBuilder,
    default_project_id,
    get_bot,
    get_project,
    load_config_file,
    load_registry,
    save_registry,
)
from .runtime import current_project_id, set_project_id
from .runner import run_bot

__all__ = [
    "Bot",
    "ChiefBot",
    "ChiefSpec",
    "PlatformBuilder",
    "Project",
    "Registry",
    "build_chief_prompt",
    "build_direct_bot_prompt",
    "chief_tool_schema",
    "current_project_id",
    "default_project_id",
    "execute_chief_tool",
    "get_bot",
    "get_project",
    "load_config_file",
    "load_registry",
    "marketplace_builder",
    "migrate_legacy_arcane",
    "run_bot",
    "save_registry",
    "schema_for_direct_bot",
    "seed_catcrew",
    "seed_marketplace",
    "set_project_id",
]
