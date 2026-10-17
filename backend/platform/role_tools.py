"""Role-specific Letta-style tools: what each core cat gets from ``tool_functions/``.

Registers every function in ``backend/tool_functions/`` into the ``tools``
catalog (``tool_registry.register_tool_function``) and assigns it to the cats
that should have it. ``ensure_role_tools()`` runs at start-up and is safe to
repeat; ``scripts/seed_role_tools.py`` calls the same code by hand.

A cat only gets the tools assigned to it (plus any ``is_global`` ones). The
optional per-cat assignments are the user's to change in the Tools page, so a
start-up run must not undo that: after a tool is first registered, later runs
only add *new* assignments that shipped in a newer ``ROLE_TOOLS_VERSION`` and
never re-add one the user removed. The exception is an *essential* tool
(``platform/essentials.py``): it is re-attached on every run, so a cat can never
be left without what its role needs.
"""

from __future__ import annotations

from typing import Callable

from backend.db import ledger as db
from backend.platform.essentials import ESSENTIAL_SKILLS, is_essential_role_tool
from backend.platform.tool_registry import register_tool_function
from backend.tool_functions import aegis_tools, forge_tools, lead_tools, pulse_tools, qa_tools

# Bump when ROLE_TOOLS gains an entry that existing installs should receive.
# The version last applied lives in the database (``meta``), so a wiped database re-seeds.
ROLE_TOOLS_VERSION = 4

# (cat id, tool slug, function). The slug is also the schema ``name``.
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

ROLE_TOOLS: list[tuple[str, str, Callable[..., str]]] = [
    ("vanguard", "create_fix_task", lead_tools.create_fix_task),
    ("pulse", "write_file_patch", pulse_tools.write_file_patch),
    ("pulse", "git_diff_inspector", pulse_tools.git_diff_inspector),
    ("pulse", "read_code_ast", pulse_tools.read_code_ast),
    ("forge", "execute_terminal_cmd", forge_tools.execute_terminal_cmd),
    ("forge", "docker_container_status", forge_tools.docker_container_status),
    ("forge", "vps_health_check", forge_tools.vps_health_check),
    ("aegis", "browser_mcp_screenshot", aegis_tools.browser_mcp_screenshot),
    ("aegis", "http_endpoint_audit", aegis_tools.http_endpoint_audit),
    ("aegis", "run_test_suite", aegis_tools.run_test_suite),
    # Interactive Playwright session: QA clicks through flows, the designer
    # checks layout, contrast and responsiveness. Text-only by design.
    *[("aegis", slug, fn) for slug, fn in _BROWSER_TOOLS],
    *[("pixel", slug, fn) for slug, fn in _BROWSER_TOOLS],
]

# Assignments introduced after the first release of the registry: an install
# whose marker is behind gets these once (and never again, so a later
# unassign in the Tools page sticks).
_ADDED_IN: dict[tuple[str, str], int] = {
    ("vanguard", "create_fix_task"): 4,
    **{("pixel", slug): 2 for slug, _ in _BROWSER_TOOLS},
    ("pixel", "browser_look"): 3,
    ("aegis", "browser_look"): 3,
    **{
        ("aegis", slug): 2
        for slug in ("browser_snapshot", "browser_inspect", "browser_set_viewport", "browser_close")
    },
}


_META_KEY = "role_tools_version"


def _read_marker() -> int:
    try:
        return int(db.get_meta(_META_KEY, "0") or "0")
    except ValueError:
        return 0


def ensure_role_tools(*, force: bool = False) -> list[str]:
    """Register the role tools and assign the ones this install has not seen yet.

    * A tool whose catalog row does not exist yet (a fresh database) is
      registered and assigned to its cats.
    * A tool that already exists is refreshed (schema/docstring) but only
      assigned when this install's marker is older than the version that
      introduced that assignment, so an optional tool the user unassigned stays
      off. An essential tool is always (re)assigned.
    * ``force`` (the manual seed script) assigns everything.

    Returns human-readable notes for logging.
    """
    marker = _read_marker()
    notes: list[str] = []
    # Read once up front: a tool shared by two cats (the browser set) must count
    # as "new" for both, not only for whichever cat registers it first.
    known = {slug for _, slug, _ in ROLE_TOOLS if db.get_tool_by_slug(slug) is not None}
    for bot_id, slug, fn in ROLE_TOOLS:
        register_tool_function(slug, fn, is_global=False, type="system")
        new_here = _ADDED_IN.get((bot_id, slug), 0) > marker
        if force or slug not in known or new_here or is_essential_role_tool(bot_id, slug):
            db.assign_tool_by_slug(bot_id, slug)
            notes.append(f"assigned {slug!r} -> {bot_id}")
    if marker < ROLE_TOOLS_VERSION:
        db.set_meta(_META_KEY, str(ROLE_TOOLS_VERSION))
    return notes


def ensure_essential_skills() -> list[str]:
    """(Re)assign every essential playbook (``platform/essentials.ESSENTIAL_SKILLS``) to its cat.

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
    return notes
