"""Nested subagent — clean context, tool subset, summary back to parent."""

from __future__ import annotations

from typing import Any

from ..settings_store import agent_settings
from .events import emit_activity
from .runtime import (
    current_abort,
    current_provider,
    nest_depth,
    push_depth,
    reset_depth,
)

# Read/write coding tools — no recursive subagents, no self-modify/restart.
SUBAGENT_TOOL_NAMES = frozenset(
    {
        "read_file",
        "write_file",
        "replace_in_file",
        "list_dir",
        "get_file_info",
        "create_directory",
        "glob_files",
        "search_files",
        "run_command",
        "read_terminal",
        "web_search",
        "web_fetch",
        "read_skill",
        "update_todo",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "git_add",
        "git_branch",
        "list_connectors",
        "load_connector_tools",
        "docker_info",
        "hardware_info",
        "system_time",
    }
)

_MAX_DEPTH = 1
_SUMMARY_CAP = 6_000


def run_subagent(task: str, *, max_rounds: int = 0) -> str:
    """Spawn one nested agent loop; return a short summary for the parent."""
    goal = str(task or "").strip()
    if not goal:
        return "Error: run_subagent needs a task."
    if nest_depth() >= _MAX_DEPTH:
        return "Error: nested subagents are not allowed (depth limit)."
    provider = current_provider()
    if provider is None:
        return "Error: no chat provider bound for subagent."

    from ..tools import execute_tool, tools_schema_for

    rounds = int(max_rounds or 0)
    if rounds <= 0:
        try:
            settings = int(agent_settings().get("max_rounds") or 0)
        except (TypeError, ValueError):
            settings = 0
        rounds = 0 if settings <= 0 else max(4, min(settings // 2, 10))

    schema = [
        item
        for item in tools_schema_for("developer")
        if (item.get("function") or {}).get("name") in SUBAGENT_TOOL_NAMES
    ]
    # Drop meta tools that would re-enter subagent / code mode if present.
    schema = [
        item
        for item in schema
        if (item.get("function") or {}).get("name")
        not in {"run_subagent", "run_code_mode", "ask_user"}
    ]

    system = (
        "You are a Jinx subagent. Complete the task with tools, then reply with a "
        "concise summary for the parent agent: what you did, key paths, errors, "
        "and what remains. Do not chat with the user. No nested subagents."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": goal},
    ]
    emit_activity("Subagent", goal[:88])
    depth_token = push_depth()
    try:
        summary = provider.run_agent(
            messages,
            schema,
            execute_tool,
            max_rounds=rounds,
            abort=current_abort(),
            on_tools=lambda names: emit_activity(
                "Subagent tool", ", ".join(names)[:88]
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return f"Subagent failed: {exc}"
    finally:
        reset_depth(depth_token)

    text = (summary or "").strip() or "(subagent returned empty)"
    if len(text) > _SUMMARY_CAP:
        text = text[:_SUMMARY_CAP] + f"\n... [truncated, {len(summary)} chars]"
    return f"[subagent summary]\n{text}"
