"""Start-up seeding of the cat bundles (``platform/essentials.py``) into the catalog.

Registers every function a bundle names from ``backend/tool_functions/`` into the ``tools``
catalog (``tool_registry.register_tool_function``) and assigns it to its cats; assigns the bundle
playbooks; and places every skill or tool that is in no bundle (the user's own, imported, from the
marketplace) on the cats it targets, or on every cat. ``ensure_role_tools()`` and
``ensure_essential_skills()`` run at start-up and are safe to repeat; ``scripts/seed_role_tools.py``
calls the same code by hand.

The bundles are fixed: the user does not pick a cat's tools or playbooks, so a start-up run simply
(re)assigns all of them.
"""

from __future__ import annotations

from typing import Callable

from backend.db import ledger as db
from backend.platform.essentials import ESSENTIAL_ROLE_TOOLS, ESSENTIAL_SKILLS, GITHUB, bundled_skill_slugs
from backend.platform.tool_registry import register_tool_function
from backend.tool_functions import aegis_tools, blaze_tools, forge_tools, github_tools, lead_tools, lex_tools, pulse_tools, qa_tools

# Kept so an install's database records which bundle generation it last saw.
ROLE_TOOLS_VERSION = 6

# Slug -> function for every registry tool a bundle may name. The slug is also the schema ``name``.
_BROWSER_TOOLS: list[tuple[str, Callable[..., str]]] = [
    ("browser_navigate", qa_tools.browser_navigate),
    ("browser_snapshot", qa_tools.browser_snapshot),
    ("browser_inspect", qa_tools.browser_inspect),
    ("browser_set_viewport", qa_tools.browser_set_viewport),
    ("browser_click", qa_tools.browser_click),
    ("browser_type", qa_tools.browser_type),
    ("browser_select_option", qa_tools.browser_select_option),
    ("browser_take_screenshot", qa_tools.browser_take_screenshot),
    ("browser_look", qa_tools.browser_look),
    ("browser_close", qa_tools.browser_close),
]
FUNCTIONS: dict[str, Callable[..., str]] = {
    "create_fix_task": lead_tools.create_fix_task,
    "write_file_patch": pulse_tools.write_file_patch,
    "git_diff_inspector": pulse_tools.git_diff_inspector,
    "read_code_ast": pulse_tools.read_code_ast,
    "execute_terminal_cmd": forge_tools.execute_terminal_cmd,
    "docker_container_status": forge_tools.docker_container_status,
    "vps_health_check": forge_tools.vps_health_check,
    "ssh_command": forge_tools.ssh_command,
    **{slug: getattr(github_tools, slug) for slug in GITHUB},
    "browser_mcp_screenshot": aegis_tools.browser_mcp_screenshot,
    "http_endpoint_audit": aegis_tools.http_endpoint_audit,
    "run_test_suite": aegis_tools.run_test_suite,
    "seo_page_audit": blaze_tools.seo_page_audit,
    "data_collection_scan": lex_tools.data_collection_scan,
    **dict(_BROWSER_TOOLS),
}

# (cat id, tool slug, function), straight from the bundles.
ROLE_TOOLS: list[tuple[str, str, Callable[..., str]]] = [
    (bot_id, slug, FUNCTIONS[slug]) for bot_id, slug in sorted(ESSENTIAL_ROLE_TOOLS)
]

_META_KEY = "role_tools_version"


def ensure_role_tools(*, force: bool = False) -> list[str]:  # noqa: ARG001 - kept for the seed script
    """Register every bundle tool and assign it to its cats. Returns notes for logging."""
    notes: list[str] = []
    for slug, fn in FUNCTIONS.items():
        register_tool_function(slug, fn, is_global=False, type="system")
    for bot_id, slug, _fn in ROLE_TOOLS:
        if slug not in _tool_slugs(bot_id):
            db.assign_tool_by_slug(bot_id, slug)
            notes.append(f"assigned {slug!r} -> {bot_id}")
    db.set_meta(_META_KEY, str(ROLE_TOOLS_VERSION))
    notes += place_extra_tools()
    return notes


def _tool_slugs(bot_id: str) -> set[str]:
    return {t["slug"] for t in db.tools_for_agent(bot_id)}


def ensure_essential_skills() -> list[str]:
    """(Re)assign every bundle playbook (``essentials.ESSENTIAL_SKILLS``) to its cat, then place
    the playbooks no bundle names.

    Runs at start-up after the skill catalog is synced from disk; a playbook that is not in the
    catalog (deleted from disk) is skipped. Safe to repeat: assignments are ``INSERT OR IGNORE``."""
    notes: list[str] = []
    for bot_id, slug in sorted(ESSENTIAL_SKILLS):
        row = db.get_skill_by_slug(slug)
        if row is None:
            continue
        if row["id"] not in db.agent_skill_ids(bot_id):
            db.assign_skill(bot_id, row["id"])
            notes.append(f"assigned skill {slug!r} -> {bot_id}")
    notes += place_extra_skills()
    return notes


def cats_for_targets(raw: str) -> list[str]:
    """Core cat ids a ``target_roles`` value names, by cat id (``pulse``) or role (``developer``)."""
    from backend.platform.catcrew import CATCREW_MEMBERS

    wanted = {part.strip().lower() for part in str(raw or "").split(",") if part.strip()}
    return [m["id"] for m in CATCREW_MEMBERS if m["id"] in wanted or m["role"] in wanted]


def place_extra_skills() -> list[str]:
    """Put every playbook no bundle names on the cats its ``target_roles`` lists, or on every cat.

    Only a playbook nobody has placed yet: one already assigned or global is left as it is. The
    bundled playbooks no cat needs (``desktop``) stay off, and a Hermes proposal waits for its
    approval (``auto_skill.approve_proposal``)."""
    from backend.agent_context import is_bundled_skill
    from pathlib import Path

    notes: list[str] = []
    bundled = bundled_skill_slugs()
    for row in db.list_skills():
        slug = str(row.get("slug") or "")
        if slug in bundled or row.get("type") == "auto_generated" or row.get("is_global"):
            continue
        path = str(row.get("playbook_path") or "")
        if path and is_bundled_skill(Path(path)):
            continue
        if db.agent_ids_for_skill(row["id"]):
            continue
        cats = cats_for_targets(str(row.get("target_roles") or ""))
        for cat in cats:
            db.assign_skill(cat, row["id"])
        if not cats:
            db.set_skill_global(row["id"], True)
        notes.append(f"placed skill {slug!r} -> {', '.join(cats) or 'every cat'}")
    return notes


def place_extra_tools() -> list[str]:
    """Give every registered tool no bundle names to every cat (the user registered it to be used).
    One already assigned or global is left as it is."""
    notes: list[str] = []
    for row in db.list_tools():
        slug = str(row.get("slug") or "")
        if slug in FUNCTIONS or row.get("is_global") or db.agent_ids_for_tool(row["id"]):
            continue
        db.set_tool_global(row["id"], True)
        notes.append(f"placed tool {slug!r} -> every cat")
    return notes
