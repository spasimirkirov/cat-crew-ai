"""Each cat's bundle: the tools and playbooks it ships with. The user does not pick them.

Every cat gets exactly what its job in the pipeline (``platform/sop.py``) needs, and nothing it
has to be talked out of. Three layers, all fixed here:

* **Built-in tools** (``backend/tools.py``) per role: ``ESSENTIAL_BUILTINS``. A role's pack in
  ``platform/toolset.py`` may add a few more, but never fewer.
* **Registry tools** (``backend/tool_functions/``) per cat: ``ESSENTIAL_ROLE_TOOLS``. Registered
  and (re)assigned on every start-up by ``platform/role_tools.py``.
* **Playbooks** (``backend/skills/``) per cat: ``ESSENTIAL_SKILLS``, plus ``CREW_SKILLS`` for
  the Cat Crew chat (the orchestrator, which is never a row in ``agent_skills``).

A bundle item cannot be unassigned through the API, dropped through an agent's ``tools`` list,
or lost with a database row: it is re-attached on every start, and it always shows in the cat's
skill index and passes ``read_skill``. The Skills and Tools pages only show the bundles.

A skill or tool that is not in any bundle (written by the user, imported from a URL, registered
from the marketplace) is placed automatically: on the cats its ``target_roles`` names, or on
every cat when it names none (``role_tools.place_extra_skills`` / ``place_extra_tools``).

This module has no imports, so the tool sets, the start-up seeding, the API and the tests can
all read it without cycles. To change what a cat ships with, change it here.
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
# Read-only history: what changed and when, for a planner or a reviewer.
GIT_READ = ("git_status", "git_diff", "git_log")
# Current facts from the web: regulations, competitors, search trends.
WEB = ("web_search", "web_fetch")
# Roles that build or check software: they must be able to look up the official docs (Forge picked
# a package its Laravel version does not support, with no way to check).
DOCS = ("docs_search", *WEB)

# Built-in tools (backend/tools.py) per role.
ESSENTIAL_BUILTINS: dict[str, tuple[str, ...]] = {
    # Plans the build and turns problem reports into fix plans: it reads what changed on disk.
    "vanguard": (*UNIVERSAL, *WRITE, *DOCS, *GIT_READ),
    "architect": (*UNIVERSAL, *WRITE, *DOCS),
    # Checks that the stack the brief names exists and fits (versions, hosting) before Vanguard plans.
    "prompt_engineer": (*UNIVERSAL, *WRITE, *DOCS),
    "lead": UNIVERSAL,
    "accountant": UNIVERSAL,
    "legal": (*UNIVERSAL, *WRITE, *WEB),
    "pr": (*UNIVERSAL, *WRITE, *WEB),
    "marketing": (*UNIVERSAL, *WRITE, *WEB, "image_search"),
    "designer": (*UNIVERSAL, *WRITE, *SHELL, *DOCS, "copy_path", "image_search"),
    "developer": (*UNIVERSAL, *WRITE, *SHELL, *DOCS, "scaffold_project", "copy_path", "move_path", "git_add", "git_log", "git_show"),
    # Forge owns releases: it commits, then pushes and publishes through its GitHub tools.
    "devops": (*UNIVERSAL, *WRITE, *SHELL, *DOCS, "scaffold_project", "run_smoke_test", "git_add", "git_commit", "git_log", "git_show", "git_branch", "docker_info"),
    # QA writes the automated tests it re-runs on every check, so it needs to write files too.
    "qa": (*UNIVERSAL, *WRITE, *SHELL, *DOCS, "run_smoke_test", "browser_check", "git_log"),
}

# The interactive Playwright session (backend/tool_functions/qa_tools.py), text-first.
BROWSER_ALL = (
    "browser_navigate", "browser_snapshot", "browser_inspect", "browser_set_viewport",
    "browser_click", "browser_type", "browser_select_option", "browser_take_screenshot",
    "browser_look", "browser_close",
)
# Enough to open a page and read it as text.
BROWSER_READ = ("browser_navigate", "browser_snapshot", "browser_close")
# GitHub with the connector's token (backend/tool_functions/github_tools.py): Forge only.
GITHUB = (
    "github_status", "github_push", "github_create_branch", "github_create_pull_request",
    "github_merge_pull_request", "github_create_release", "github_actions_runs", "github_run_workflow",
)

# Registry tools (backend/tool_functions/) per cat: (cat id, slug).
ESSENTIAL_ROLE_TOOLS: frozenset[tuple[str, str]] = frozenset(
    {
        # Vanguard turns a problem report into fix tasks with this; without it there is no plan.
        ("vanguard", "create_fix_task"),
        # Pulse patches code, sees the shape of its own diff, and requests each page it built.
        *(("pulse", slug) for slug in ("write_file_patch", "git_diff_inspector", "read_code_ast", "http_endpoint_audit")),
        # Pixel opens, inspects and resizes the page it styles, and asks Vision when one is set.
        *(("pixel", slug) for slug in BROWSER_ALL),
        # Aegis drives the page, runs the suite and audits the endpoints.
        *(("aegis", slug) for slug in (*BROWSER_ALL, "browser_mcp_screenshot", "run_test_suite", "http_endpoint_audit")),
        # Forge runs the environment, deploys over SSH, checks it after a deploy, and ships through
        # GitHub: branches, pull requests, merges, Actions and releases.
        *(("forge", slug) for slug in ("execute_terminal_cmd", "docker_container_status", "vps_health_check", "http_endpoint_audit", "ssh_command", *GITHUB)),
        # Blaze reads the live copy and audits what search engines and share cards see.
        *(("blaze", slug) for slug in (*BROWSER_READ, "seo_page_audit")),
        # Lex grounds the privacy policy in what the code actually collects and the site shows.
        *(("lex", slug) for slug in (*BROWSER_READ, "data_collection_scan")),
    }
)


def essential_builtins(role: str) -> tuple[str, ...]:
    return ESSENTIAL_BUILTINS.get((role or "").strip().lower(), ())


def is_essential_role_tool(bot_id: str, slug: str) -> bool:
    return (str(bot_id or "").strip(), str(slug or "").strip()) in ESSENTIAL_ROLE_TOOLS


def essential_agents_for_slug(slug: str) -> frozenset[str]:
    """Cat ids whose bundle holds registry tool ``slug`` (empty: it is in no bundle)."""
    return frozenset(bot for bot, name in ESSENTIAL_ROLE_TOOLS if name == slug)


def essential_slugs_for_agent(bot_id: str) -> tuple[str, ...]:
    return tuple(sorted(slug for bot, slug in ESSENTIAL_ROLE_TOOLS if bot == str(bot_id or "").strip()))


def _pairs(bundles: dict[str, tuple[str, ...]]) -> frozenset[tuple[str, str]]:
    return frozenset((cat, slug) for cat, slugs in bundles.items() for slug in slugs)


# Playbooks (backend/skills/<slug>/SKILL.md) per cat.
SKILL_BUNDLES: dict[str, tuple[str, ...]] = {
    # Turns the raw brief into the blueprint everyone downstream reads; checks the stack is real.
    "cipher": ("task-blueprint", "docs-lookup", "web-research"),
    # Plans the build and hands it out: how the coder works, the stack, the design system, and
    # acceptance checks QA can turn into tests.
    "vanguard": ("coding-companion", "docs-lookup", "laravel", "static-web", "design-md", "automated-testing"),
    # Writes DESIGN.md and builds what it styles; images through ComfyUI, 3D through Blender.
    "pixel": ("design-md", "static-web", "laravel", "docs-lookup", "comfy", "blender-mcp"),
    # Builds: long scaffolds, the stack, keeps the tests green, commits cleanly; Unity through MCP.
    "pulse": ("coding-companion", "static-web", "laravel", "docs-lookup", "automated-testing", "design-md", "commit-style", "unity-mcp"),
    # Proves it works with tests it writes once and re-runs, and checks the obvious security holes.
    "aegis": ("automated-testing", "security-checks", "laravel", "static-web", "docs-lookup", "design-md"),
    # Sets up and ships the environment: the stack, containers, secrets and release hygiene.
    "forge": ("laravel", "static-web", "docker", "docs-lookup", "security-checks", "commit-style", "deploy", "github-release"),
    # Drafts the legal pages from the facts of this product and current regulations.
    "lex": ("legal-docs", "web-research"),
    # Writes the copy, the metadata and the launch; edits Blade views when the copy lives there.
    # Also prices the product: there is no accountant cat, so pricing briefs come to Blaze.
    "blaze": ("seo-copy", "pricing", "web-research", "laravel"),
}
ESSENTIAL_SKILLS: frozenset[tuple[str, str]] = _pairs(SKILL_BUNDLES)

# What the Cat Crew chat (the orchestrator) can read: who it is, current facts, and the honest
# answer about home devices. Never stored in agent_skills (Cat Crew is not an agent).
CREW_SKILLS: tuple[str, ...] = ("catcrew-self", "web-research", "home-lan")


def essential_agents_for_skill(slug: str) -> frozenset[str]:
    """Cat ids whose bundle holds playbook ``slug`` (empty: it is in no cat's bundle)."""
    key = str(slug or "").strip().lower()
    return frozenset(bot for bot, name in ESSENTIAL_SKILLS if name == key)


def essential_skills_for_agent(bot_id: str) -> tuple[str, ...]:
    return tuple(sorted(slug for bot, slug in ESSENTIAL_SKILLS if bot == str(bot_id or "").strip()))


def bundled_skill_slugs() -> frozenset[str]:
    """Every playbook some bundle (a cat's or Cat Crew's) already places."""
    return frozenset(slug for _bot, slug in ESSENTIAL_SKILLS) | frozenset(CREW_SKILLS)
