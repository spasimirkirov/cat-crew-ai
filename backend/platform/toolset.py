"""Builtin + MCP tool assignment: global catalog vs per-bot allowlists."""

from __future__ import annotations

from typing import Any

from ..harness.deferred import extra_tool_schemas, load_connector_tools_text
from .essentials import ESSENTIAL_BUILTINS, essential_builtins

# Role packs — used when a bot does not list explicit builtin tools.
ROLE_PACKS: dict[str, tuple[str, ...]] = {
    "vanguard": (
        # Merges the old "lead" + "architect" packs: Vanguard runs both the
        # architecture phase (needs write_file/create_directory for specs)
        # and the distribution/coordination phase (needs ask_user/update_todo).
        "read_skill",
        "update_todo",
        "ask_user",
        "read_file",
        "write_file",
        "list_dir",
        "get_file_info",
        "create_directory",
        "glob_files",
        "search_files",
        "web_search",
        "web_fetch",
        "system_time",
        "report_task_results",
    ),
    "prompt_engineer": (
        "read_skill",
        "update_todo",
        "ask_user",
        "read_file",
        "write_file",
        "list_dir",
        "glob_files",
        "web_search",
        "web_fetch",
        "system_time",
        "report_task_results",
    ),
    "orchestrator": (
        # Cat Crew. No delegate_to_bot here on purpose: specialists are never run
        # inside the model's own tool loop (their progress would be invisible).
        # platform/orchestrator.py routes work messages to platform/sop.py, and a chat
        # turn can ask for the same with hand_off_to_crew, which only *records* the
        # request; the specialists then stream over the WebSocket. Cat Crew keeps
        # read/lookup tools only — no write access, no per-agent assignments.
        "ask_user",
        "update_todo",
        "read_skill",
        "list_connectors",
        "load_connector_tools",
        "web_search",
        "web_fetch",
        "read_file",
        "list_dir",
        "glob_files",
        "search_files",
        "system_time",
        "hardware_info",
        "list_project",
        "list_bots",
        "list_members",
        "get_member",
        "list_roster",
        "update_project_state",
        "hand_off_to_crew",
    ),
    "architect": (
        "read_skill",
        "update_todo",
        "read_file",
        "write_file",
        "list_dir",
        "get_file_info",
        "create_directory",
        "glob_files",
        "search_files",
        "web_search",
        "web_fetch",
        "system_time",
        "report_task_results",
    ),
    "designer": (
        "read_skill",
        "update_todo",
        "read_file",
        "write_file",
        "replace_in_file",
        "list_dir",
        "get_file_info",
        "create_directory",
        "glob_files",
        "search_files",
        # A design is only done when it compiles and renders: the designer builds the
        # project and reads the dev server output like everyone else who touches source.
        "run_command",
        "read_terminal",
        "git_status",
        "git_diff",
        "system_time",
        "image_search",
        "generate_image",
        "comfy_info",
        "comfy_list",
        "comfy_run_workflow",
        "web_search",
        "web_fetch",
        "copy_path",
        "report_task_results",
    ),
    "developer": (
        "read_skill",
        "update_todo",
        "read_file",
        "write_file",
        "replace_in_file",
        "list_dir",
        "get_file_info",
        "create_directory",
        "glob_files",
        "copy_path",
        "move_path",
        "search_files",
        "run_command",
        "read_terminal",
        "web_search",
        "web_fetch",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_add",
        "git_branch",
        "system_time",
        "hardware_info",
        "docker_info",
        "report_task_results",
    ),
    "qa": (
        "read_skill",
        "update_todo",
        "read_file",
        "list_dir",
        "glob_files",
        "search_files",
        "run_command",
        "run_smoke_test",
        "browser_check",
        "read_terminal",
        "web_search",
        "web_fetch",
        "git_status",
        "git_diff",
        "git_log",
        "system_time",
        "report_task_results",
    ),
    "accountant": (
        "read_skill",
        "web_search",
        "web_fetch",
        "read_file",
        "list_dir",
        "glob_files",
        "search_files",
        "system_time",
    ),
    "lead": (
        "read_skill",
        "update_todo",
        "ask_user",
        "read_file",
        "list_dir",
        "glob_files",
        "search_files",
        "web_search",
        "web_fetch",
        "system_time",
    ),
    "legal": (
        "read_skill",
        "web_search",
        "web_fetch",
        "read_file",
        "write_file",
        "list_dir",
        "glob_files",
        "search_files",
        "system_time",
    ),
    "pr": (
        "read_skill",
        "web_search",
        "web_fetch",
        "read_file",
        "write_file",
        "list_dir",
        "glob_files",
        "system_time",
    ),
    "marketing": (
        "read_skill",
        "web_search",
        "web_fetch",
        "read_file",
        "write_file",
        "list_dir",
        "glob_files",
        "search_files",
        "image_search",
        "system_time",
    ),
    "assistant": (
        "read_skill",
        "ask_user",
        "system_time",
        "web_search",
        "web_fetch",
        "read_file",
        "write_file",
        "clipboard",
        "notify",
    ),
    "devops": (
        "read_skill",
        "update_todo",
        "read_file",
        "write_file",
        "replace_in_file",
        "list_dir",
        "glob_files",
        "search_files",
        "run_command",
        "run_smoke_test",
        "read_terminal",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_add",
        "git_branch",
        "docker_info",
        "hardware_info",
        "system_time",
        "report_task_results",
    ),
    "morale": (
        "read_skill",
        "system_time",
    ),
    "general": (
        "read_skill",
        "read_file",
        "list_dir",
        "web_search",
        "web_fetch",
        "system_time",
    ),
}

# Never given to nested specialist bots — Chief owns orchestration.
_BLOCKED_FOR_BOTS = frozenset(
    {
        "run_subagent",
        "run_code_mode",
        "self_modify",
        "restart_catcrew",
        "list_project",
        "list_bots",
        "delegate_to_bot",
        "update_project_state",
    }
)

_GLOBAL_WILDCARDS = frozenset({"*", "global", "all"})


def _schema_by_name() -> dict[str, dict[str, Any]]:
    from ..tools import TOOLS_SCHEMA

    out: dict[str, dict[str, Any]] = {}
    for item in TOOLS_SCHEMA:
        name = (item.get("function") or {}).get("name")
        if name:
            out[str(name)] = item
    return out


# Keep every pack a superset of its role's essentials, so the pack (what the role gets by default)
# and the essentials (what it can never lose) cannot drift apart.
for _role, _essential in ESSENTIAL_BUILTINS.items():
    ROLE_PACKS[_role] = tuple(dict.fromkeys([*_essential, *ROLE_PACKS.get(_role, ())]))


def builtin_names_for_role(role: str, explicit: list[str] | None = None) -> list[str]:
    if explicit:
        names = [str(n).strip() for n in explicit if str(n).strip()]
    else:
        pack = ROLE_PACKS.get((role or "").strip().lower()) or ROLE_PACKS["general"]
        names = list(pack)
    # Essential tools are always attached, whatever an agent's own ``tools`` list says: that
    # list can only choose among the optional ones (see platform/essentials.py).
    names = [*essential_builtins(role), *names]
    out: list[str] = []
    for name in names:
        if name in _BLOCKED_FOR_BOTS:
            continue
        if name not in out:
            out.append(name)
    for extra in ("list_members", "get_member", "list_roster"):
        if extra not in out:
            out.append(extra)
    return out


def mcp_selector_ids(
    bot_mcp: list[str],
    *,
    global_mcp: list[str],
    inherit_global: bool,
) -> tuple[list[str], set[str]]:
    """Return (connector ids to load, optional remote tool name filter).

    Empty filter set means all tools from those connectors.
    Selectors:
      ``*`` / ``global`` / ``all`` — every enabled connector
      ``github`` — that connector id
      ``github:list_issues`` or ``connector__github__list_issues`` — one tool
    """
    selectors = list(bot_mcp or [])
    if inherit_global:
        selectors = list(global_mcp or []) + selectors
    if any(s.strip().lower() in _GLOBAL_WILDCARDS for s in selectors):
        from ..connectors import enabled_connectors

        return [item.id for item in enabled_connectors()], set()

    connector_ids: list[str] = []
    tool_filter: set[str] = set()
    saw_specific = False
    for raw in selectors:
        text = str(raw or "").strip()
        if not text or text.lower() in {"none", "off"}:
            continue
        if text.startswith("connector__"):
            parts = text.split("__", 2)
            if len(parts) == 3:
                cid, remote = parts[1], parts[2]
                if cid not in connector_ids:
                    connector_ids.append(cid)
                tool_filter.add(text)
                tool_filter.add(remote.lower())
                saw_specific = True
            continue
        if ":" in text:
            cid, remote = text.split(":", 1)
            cid, remote = cid.strip(), remote.strip()
            if cid and cid not in connector_ids:
                connector_ids.append(cid)
            if remote:
                tool_filter.add(remote.lower())
                tool_filter.add(f"connector__{cid}__{remote}")
                saw_specific = True
            continue
        if text not in connector_ids:
            connector_ids.append(text)
    if not saw_specific:
        tool_filter = set()
    return connector_ids, tool_filter


def _filter_connector_schemas(
    schemas: list[dict[str, Any]],
    tool_filter: set[str],
) -> list[dict[str, Any]]:
    if not tool_filter:
        return list(schemas)
    lowered = {x.lower() for x in tool_filter}
    kept: list[dict[str, Any]] = []
    for item in schemas:
        name = str((item.get("function") or {}).get("name") or "")
        remote = name.split("__")[-1].lower() if "__" in name else name.lower()
        if name.lower() in lowered or remote in lowered or name in tool_filter:
            kept.append(item)
    return kept


def preload_bot_mcp(
    bot_mcp: list[str],
    *,
    global_mcp: list[str],
    inherit_global: bool,
) -> str:
    """Load assigned MCP/Python connectors into the deferred registry."""
    connector_ids, tool_filter = mcp_selector_ids(
        bot_mcp, global_mcp=global_mcp, inherit_global=inherit_global
    )
    if not connector_ids:
        return ""
    lines: list[str] = []
    for cid in connector_ids:
        summary = load_connector_tools_text(cid)
        lines.append(summary)
    if tool_filter:
        from ..harness.deferred import extra_tool_schemas, restore_loaded_tools, snapshot_loaded_tools

        schemas, executors = snapshot_loaded_tools()
        kept = _filter_connector_schemas(schemas, tool_filter)
        keep_names = {
            str((item.get("function") or {}).get("name") or "") for item in kept
        }
        slim_exec = {k: v for k, v in executors.items() if k in keep_names}
        restore_loaded_tools(kept, slim_exec)
        extra_tool_schemas()
    return "\n".join(lines)


def schema_for_names(names: list[str]) -> list[dict[str, Any]]:
    catalog = _schema_by_name()
    schema: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in names:
        item = catalog.get(name)
        if item is None or name in seen:
            continue
        schema.append(item)
        seen.add(name)
    for item in extra_tool_schemas():
        name = str((item.get("function") or {}).get("name") or "")
        if name and name not in seen:
            schema.append(item)
            seen.add(name)
    return schema


def allowed_names(schema: list[dict[str, Any]]) -> frozenset[str]:
    names = {
        str((item.get("function") or {}).get("name") or "")
        for item in schema
        if isinstance(item, dict)
    }
    names.discard("")
    return frozenset(names)
