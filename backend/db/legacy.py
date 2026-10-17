"""One-way mapping from the pre-pivot module layout (``cat_crew_ai.*``) to the
current one (``backend.*``).

Tool handler paths (``"module:function"``) are persisted in ``catcrew.db`` and
in user-imported tool rows, so they outlive a refactor. ``normalize_module_path``
is used by the additive DB migration (``ledger._migrate_schema``) and, as a
safety net, by ``tool_registry.resolve_handler`` — an old row keeps working
even if it was never migrated. Pure functions, no imports: safe from anywhere.
"""

from __future__ import annotations

OLD_PKG = "cat_crew_ai"
NEW_PKG = "backend"

# old dotted module (relative to the package) -> new one. Longest prefix wins.
_MOVES: dict[str, str] = {
    "platform.db": "db.ledger",
    "state": "db.state",
    **{
        name: f"services.{name}"
        for name in (
            "sessions session_tree session_fs workspace terminal settings_store api_access "
            "memory trace todo compaction clarify git_ops docker_runtime comfyui searxng "
            "ddgs_search image_search lm_runtime llama_server gguf_browser local_models "
            "model_recs downloads hardware here desktop pending_restart self_modifier "
            "timeutil logutil"
        ).split()
    },
}


def normalize_module_path(path: str) -> str:
    """``cat_crew_ai.state.x:fn`` -> ``backend.db.state.x:fn``; anything else is returned unchanged."""
    text = str(path or "")
    module, sep, func = text.partition(":")
    if module != OLD_PKG and not module.startswith(OLD_PKG + "."):
        return text
    rel = module[len(OLD_PKG) + 1 :] if module != OLD_PKG else ""
    for old, new in sorted(_MOVES.items(), key=lambda kv: -len(kv[0])):
        if rel == old:
            rel = new
            break
        if rel.startswith(old + "."):
            rel = new + rel[len(old) :]
            break
    mapped = f"{NEW_PKG}.{rel}" if rel else NEW_PKG
    return f"{mapped}{sep}{func}"
