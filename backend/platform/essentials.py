"""Which tools and playbooks a cat can never be without ("essential"), and which are the user's choice.

Two kinds of tool exist. **Essential** tools are what the role cannot do its job without:
a developer that cannot write or run anything, a designer that cannot build what it styles,
a specialist that cannot call ``report_task_results`` all end up looping on a tool they were
never given. Those are always attached: they cannot be unassigned in the Tools page, dropped
through an agent's ``tools`` list, or removed from the database by accident, and they are
re-attached on every start-up. **Optional** tools (web search, image generation, the
interactive browser controls, Docker or VPS probes...) are the ones the user toggles.

Skills (playbooks, ``backend/skills/``) follow the same rule: ``ESSENTIAL_SKILLS`` are the
playbooks a cat's job depends on (the developer's stack and coding playbooks, say). They are
re-assigned on every start-up, cannot be unassigned in the Skills page, and always show in the
cat's skill index and pass ``read_skill``, even when the database row is missing.

This module is the single source of truth and has no imports, so the tool sets, the role-tool
seeding, the API and the tests can all read it without cycles. To change what a role must
have, change it here.
"""

from __future__ import annotations

# Every working specialist: orient in the project, follow the playbooks, track and report the task.
UNIVERSAL = (
    "read_skill",
    "update_todo",
    "report_task_results",
    "read_file",
    "list_dir",
    "glob_files",
    "search_files",
    "get_file_info",
    "system_time",
)
# Roles that produce files.
WRITE = ("write_file", "replace_in_file", "create_directory")
# Roles that build and run what is on disk.
SHELL = ("run_command", "read_terminal", "git_status", "git_diff")

# Roles that build or check software: they must be able to look up the official docs (Forge picked
# a package its Laravel version does not support, with no way to check).
DOCS = ("docs_search", "web_search", "web_fetch")

# Built-in tools (backend/tools.py) per role.
ESSENTIAL_BUILTINS: dict[str, tuple[str, ...]] = {
    "vanguard": (*UNIVERSAL, *WRITE, *DOCS),
    "architect": (*UNIVERSAL, *WRITE, *DOCS),
    "prompt_engineer": (*UNIVERSAL, *WRITE),
    "lead": UNIVERSAL,
    "accountant": UNIVERSAL,
    "legal": (*UNIVERSAL, *WRITE),
    "pr": (*UNIVERSAL, *WRITE),
    "marketing": (*UNIVERSAL, *WRITE),
    "designer": (*UNIVERSAL, *WRITE, *SHELL, *DOCS, "copy_path"),
    "developer": (*UNIVERSAL, *WRITE, *SHELL, *DOCS, "scaffold_project", "copy_path", "move_path", "git_add", "git_log", "git_show"),
    "devops": (*UNIVERSAL, *WRITE, *SHELL, *DOCS, "scaffold_project", "run_smoke_test", "git_add", "git_log", "git_show", "docker_info"),
    # QA writes the automated tests it re-runs on every check, so it needs to write files too.
    "qa": (*UNIVERSAL, *WRITE, *SHELL, *DOCS, "run_smoke_test", "browser_check", "git_log"),
}

# Registry tools (backend/tool_functions/, assigned per cat in the Tools page): (cat id, slug).
# Everything else registered for a cat (browser_look, browser_select_option, read_code_ast,
# docker_container_status, vps_health_check, ...) is optional.
ESSENTIAL_ROLE_TOOLS: frozenset[tuple[str, str]] = frozenset(
    {
        # Aegis (QA) has to be able to open, inspect and drive the page and run the checks.
        *(("aegis", slug) for slug in (
            "browser_navigate", "browser_snapshot", "browser_inspect", "browser_click",
            "browser_type", "browser_set_viewport", "browser_take_screenshot", "browser_close",
            "run_test_suite", "http_endpoint_audit",
        )),
        # Pixel (design) has to be able to see the page it styles, text-first.
        *(("pixel", slug) for slug in (
            "browser_navigate", "browser_snapshot", "browser_inspect",
            "browser_set_viewport", "browser_take_screenshot", "browser_close",
        )),
        ("pulse", "write_file_patch"),
        ("pulse", "git_diff_inspector"),
        ("forge", "execute_terminal_cmd"),
        # The lead turns a problem report into fix tasks with this; without it there is no plan.
        ("vanguard", "create_fix_task"),
    }
)


def essential_builtins(role: str) -> tuple[str, ...]:
    return ESSENTIAL_BUILTINS.get((role or "").strip().lower(), ())


def is_essential_role_tool(bot_id: str, slug: str) -> bool:
    return (str(bot_id or "").strip(), str(slug or "").strip()) in ESSENTIAL_ROLE_TOOLS


def essential_agents_for_slug(slug: str) -> frozenset[str]:
    """Cat ids for which registry tool ``slug`` is essential (empty: it is optional everywhere)."""
    return frozenset(bot for bot, name in ESSENTIAL_ROLE_TOOLS if name == slug)


def essential_slugs_for_agent(bot_id: str) -> tuple[str, ...]:
    return tuple(sorted(slug for bot, slug in ESSENTIAL_ROLE_TOOLS if bot == str(bot_id or "").strip()))


# Skills (playbook slugs, backend/skills/<slug>/SKILL.md): (cat id, slug). Everything else is optional.
ESSENTIAL_SKILLS: frozenset[tuple[str, str]] = frozenset(
    {
        # Pulse builds: the coding playbook (long scaffolds, continue-across-turns) and the web stack.
        ("pulse", "coding-companion"),
        ("pulse", "static-web"),
        # Pixel styles and builds what it styles: the same web stack.
        ("pixel", "static-web"),
        # Vanguard plans the build and hands it out: it has to know how the coder is told to work.
        ("vanguard", "coding-companion"),
        # Where the answers are: the official docs of the stack, looked up instead of remembered.
        *((cat, "docs-lookup") for cat in ("vanguard", "forge", "pulse", "pixel", "aegis")),
        # Laravel projects: setup, structure and the checks that prove a page works.
        *((cat, "laravel") for cat in ("vanguard", "forge", "pulse", "pixel", "aegis", "blaze")),
        # QA proves the product with tests it writes once and re-runs; Pulse keeps them green.
        ("aegis", "automated-testing"),
        ("pulse", "automated-testing"),
        # The design system lives in DESIGN.md: Pixel writes it, Pulse and Aegis build and check against it.
        *((cat, "design-md") for cat in ("vanguard", "pulse", "pixel", "aegis")),
    }
)


def is_essential_skill(bot_id: str, slug: str) -> bool:
    return (str(bot_id or "").strip(), str(slug or "").strip().lower()) in ESSENTIAL_SKILLS


def essential_agents_for_skill(slug: str) -> frozenset[str]:
    """Cat ids for which playbook ``slug`` is essential (empty: it is optional everywhere)."""
    key = str(slug or "").strip().lower()
    return frozenset(bot for bot, name in ESSENTIAL_SKILLS if name == key)


def essential_skills_for_agent(bot_id: str) -> tuple[str, ...]:
    return tuple(sorted(slug for bot, slug in ESSENTIAL_SKILLS if bot == str(bot_id or "").strip()))
