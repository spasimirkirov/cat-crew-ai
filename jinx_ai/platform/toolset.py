"""Builtin + MCP tool assignment: global catalog vs per-bot allowlists."""

from __future__ import annotations

from typing import Any

from ..harness.deferred import extra_tool_schemas, load_connector_tools_text

# Role packs — used when a bot does not list explicit builtin tools.
ROLE_PACKS: dict[str, tuple[str, ...]] = {
    "chief": (
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
        "list_teams",
        "list_members",
        "get_member",
        "list_roster",
        "delegate_to_bot",
        "delegate_to_team",
        "update_project_state",
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
    ),
    "designer": (
        "read_skill",
        "read_file",
        "write_file",
        "replace_in_file",
        "list_dir",
        "glob_files",
        "image_search",
        "generate_image",
        "comfy_info",
        "comfy_list",
        "comfy_run_workflow",
        "web_search",
        "web_fetch",
        "copy_path",
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
    ),
    "qa": (
        "read_skill",
        "update_todo",
        "read_file",
        "list_dir",
        "glob_files",
        "search_files",
        "run_command",
        "read_terminal",
        "web_search",
        "web_fetch",
        "git_status",
        "git_diff",
        "git_log",
        "system_time",
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
        "restart_jinx",
        "list_project",
        "list_bots",
        "delegate_to_bot",
        "delegate_to_team",
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


def builtin_names_for_role(role: str, explicit: list[str] | None = None) -> list[str]:
    if explicit:
        names = [str(n).strip() for n in explicit if str(n).strip()]
    else:
        pack = ROLE_PACKS.get((role or "").strip().lower()) or ROLE_PACKS["general"]
        names = list(pack)
    out: list[str] = []
    for name in names:
        if name in _BLOCKED_FOR_BOTS:
            continue
        if name not in out:
            out.append(name)
    for extra in ("list_teams", "list_members", "get_member", "list_roster"):
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
