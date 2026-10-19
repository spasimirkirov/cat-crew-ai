"""What each tool *is*: one table the whole harness reads.

Before this, every guard kept its own list -- the approval gate's destructive set, the loop guard's
read-only set, the untrusted-output set, the shell set -- and they drifted: a role tool that runs a
shell (``execute_terminal_cmd``) or writes a file (``write_file_patch``) slipped past the approval
gate because only ``run_command`` and ``write_file`` were on its list. Now each tool is classified
once, the old lists are derived from here, and ``tests/test_tool_meta.py`` fails when a shipped tool
has no entry.

Kinds:

* ``read``      only looks (files, git, web, status). Does not change the world for the loop guard.
* ``note``      harness bookkeeping (checklist, task report, a question to the person, a hand-off).
* ``contained`` changes something, but nothing a person must approve first (the QA browser, image
                generation, opening a program, staging a git change, a delegated cat whose own calls
                are gated).
* ``act``       changes files, runs a shell or code, moves git history, writes a database: needs
                approval when tool approvals are on.

An unknown tool is ``act``: a new or marketplace tool is gated until someone classifies it. MCP and
connector tools are judged by their own read-only hints in ``harness/approvals.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

READ, NOTE, CONTAINED, ACT = "read", "note", "contained", "act"


@dataclass(frozen=True)
class ToolMeta:
    kind: str = ACT
    #: Runs a shell command line (``command`` argument): "always allow" remembers the program only.
    shell: bool = False
    #: Its result is text from outside CatCrewAI (web, a page, another program): marked as data.
    untrusted: bool = False
    #: Safe to run at the same time as other such calls in one batch (no shared session state).
    parallel: bool = False
    #: Its result changes with time on its own (a terminal, a live page): an identical call is not
    #: a pointless repeat just because no tool changed anything.
    volatile: bool = False


def _read(*, untrusted: bool = False, parallel: bool = True, volatile: bool = False) -> ToolMeta:
    return ToolMeta(kind=READ, untrusted=untrusted, parallel=parallel, volatile=volatile)


_NOTE = ToolMeta(kind=NOTE)
_CONTAINED = ToolMeta(kind=CONTAINED)
_ACT = ToolMeta(kind=ACT)
_SHELL = ToolMeta(kind=ACT, shell=True)

TOOLS: dict[str, ToolMeta] = {
    # -- files ---------------------------------------------------------------------------------------
    "read_file": _read(),
    "list_dir": _read(),
    "glob_files": _read(),
    "search_files": _read(),
    "get_file_info": _read(),
    "list_allowed_directories": _read(),
    "read_code_ast": _read(),
    "write_file": _ACT,
    "replace_in_file": _ACT,
    "write_file_patch": _ACT,
    "move_path": _ACT,
    "copy_path": _ACT,
    "create_directory": _CONTAINED,
    # -- shell, code, the app itself -----------------------------------------------------------------
    "run_command": _SHELL,
    "execute_terminal_cmd": _SHELL,
    "run_test_suite": _SHELL,
    "run_smoke_test": _SHELL,
    "scaffold_project": _SHELL,
    "run_code_mode": _ACT,
    "read_terminal": _read(parallel=False, volatile=True),
    "self_modify": _ACT,
    "restart_catcrew": _ACT,
    # -- git -----------------------------------------------------------------------------------------
    "git_status": _read(),
    "git_diff": _read(),
    "git_log": _read(),
    "git_show": _read(),
    "git_diff_inspector": _read(),
    "git_add": _CONTAINED,
    "git_unstage": _CONTAINED,
    "git_branch": _CONTAINED,
    "git_commit": _ACT,
    "git_push": _ACT,
    "git_pull": _ACT,
    "git_checkout": _ACT,
    # -- databases -----------------------------------------------------------------------------------
    "db_list_connections": _read(),
    "db_tables": _read(),
    "db_describe_table": _read(),
    "db_query": _ACT,  # may be a write
    # -- web and outside text ------------------------------------------------------------------------
    "web_search": _read(untrusted=True),
    "docs_search": _read(untrusted=True),
    "web_fetch": _read(untrusted=True),
    "image_search": _read(untrusted=True),
    "http_endpoint_audit": _read(untrusted=True, volatile=True),
    "seo_page_audit": _read(untrusted=True, volatile=True),
    "data_collection_scan": _read(),
    "mcp_call_tool": ToolMeta(kind=ACT, untrusted=True),  # judged by its target in approvals.py
    # -- the QA browser (one shared session: never in parallel) ---------------------------------------
    "browser_check": _read(untrusted=True, parallel=False, volatile=True),
    "browser_snapshot": _read(untrusted=True, parallel=False, volatile=True),
    "browser_inspect": _read(untrusted=True, parallel=False, volatile=True),
    "browser_look": _read(untrusted=True, parallel=False, volatile=True),
    "browser_take_screenshot": _read(parallel=False, volatile=True),
    "browser_mcp_screenshot": _read(parallel=False, volatile=True),
    "browser_navigate": ToolMeta(kind=CONTAINED, untrusted=True),
    "browser_click": _CONTAINED,
    "browser_type": _CONTAINED,
    "browser_select_option": _CONTAINED,
    "browser_set_viewport": _CONTAINED,
    "browser_close": _CONTAINED,
    "close_all_sessions": _CONTAINED,
    # -- this machine --------------------------------------------------------------------------------
    "system_time": _read(volatile=True),
    "hardware_info": _read(),
    "docker_info": _read(),
    "docker_container_status": _read(volatile=True),
    "vps_health_check": _read(untrusted=True, volatile=True),
    "screenshot": _read(parallel=False, volatile=True),
    "clipboard": _CONTAINED,
    "notify": _CONTAINED,
    "open_url": _CONTAINED,
    "open_program": _CONTAINED,
    "open_in_vscode": _CONTAINED,
    # -- images (ComfyUI / an image host) ------------------------------------------------------------
    "comfy_info": _read(),
    "comfy_list": _read(),
    "generate_image": _CONTAINED,
    "comfy_run_workflow": _CONTAINED,
    "comfy_upload": _CONTAINED,
    "upscale_image": _CONTAINED,
    "remove_background": _CONTAINED,
    "comfy_interrupt": _CONTAINED,
    "comfy_stop": _CONTAINED,
    # -- the team, skills and the harness -------------------------------------------------------------
    "read_skill": _read(),
    "list_roster": _read(),
    "list_members": _read(),
    "get_member": _read(),
    "list_project": _read(),
    "list_bots": _read(),
    "list_connectors": _read(),
    "load_connector_tools": _NOTE,
    "update_todo": _NOTE,
    "report_task_results": _NOTE,
    "ask_user": _NOTE,
    "hand_off_to_crew": _NOTE,
    "update_project_state": _NOTE,
    "create_fix_task": _NOTE,
    "delegate_to_bot": _CONTAINED,  # the cat's own calls go through the gate
    "run_subagent": _CONTAINED,  # likewise
}


def meta(name: str) -> ToolMeta:
    """The tool's entry; an unknown tool is treated as one that acts (gated, changes the world)."""
    return TOOLS.get(str(name or ""), _ACT)


def is_known(name: str) -> bool:
    return str(name or "") in TOOLS


def names(kind: str) -> frozenset[str]:
    return frozenset(name for name, item in TOOLS.items() if item.kind == kind)


def flagged(flag: str) -> frozenset[str]:
    return frozenset(name for name, item in TOOLS.items() if getattr(item, flag))
