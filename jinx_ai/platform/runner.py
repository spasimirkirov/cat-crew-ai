"""Run one specialist bot through the existing ``provider.run_agent`` loop."""

from __future__ import annotations

from typing import Any

from ..harness.deferred import (
    clear_loaded_tools,
    restore_loaded_tools,
    snapshot_loaded_tools,
)
from ..harness.events import emit_activity
from ..harness.runtime import current_abort, current_provider, nest_depth, push_depth, reset_depth
from ..settings_store import agent_settings
from ..tools import execute_tool, reset_tool_allowlist, set_tool_allowlist
from .models import Bot, Project, Registry
from .runtime import reset_bot_id, set_bot_id
from .toolset import (
    allowed_names,
    builtin_names_for_role,
    preload_bot_mcp,
    schema_for_names,
)

_SUMMARY_CAP = 8_000
_MAX_NEST = 1


def delegate_round_budget(requested: int = 0, *, bot: Bot | None = None) -> int:
    """Nested bot round cap. 0 = unlimited (Settings 0, or Stop ends the turn)."""
    try:
        settings = int(agent_settings().get("max_rounds") or 0)
    except (TypeError, ValueError):
        settings = 0
    if settings <= 0:
        return 0
    bot_n = int(getattr(bot, "max_rounds", 0) or 0)
    floor = max(settings, bot_n, 8)
    req = int(requested or 0)
    if req <= 0:
        return floor
    return max(req, floor)


def _project_context(project: Project, registry: Registry) -> str:
    from ..agent_context import load_workspace_docs
    from ..workspace import current_folders, current_workspace

    teams = registry.assigned_teams(project)
    folders = [str(path) for path in (project.folders or []) if path]
    if not folders:
        folders = [str(path) for path in current_folders()]
    lines = [
        f"Project: {project.name} ({project.id})",
        project.description or "",
        "Assigned teams: "
        + (
            ", ".join(f"{t.name} [{t.id}]" for t in teams) or "(none)"
        ),
        "Shared computer: " + (", ".join(folders) or f"(scratch {current_workspace()})"),
    ]
    kickoff = str(project.state.get("kickoff") or "").strip()
    if kickoff:
        lines.append("User kickoff:\n" + kickoff[:4000])
    if project.state:
        bits = [
            f"{k}={v}"
            for k, v in list(project.state.items())[:24]
            if str(k) != "kickoff"
        ]
        if bits:
            lines.append("Shared state: " + "; ".join(bits))
    recent = project.events[-16:]
    if recent:
        lines.append("Recent project memory (Everyone + delegated work):")
        for event in recent:
            who = event.bot_id or event.role
            snippet = event.text.replace("\n", " ")[:240]
            lines.append(f"- {who}: {snippet}")
    docs = load_workspace_docs()
    if docs:
        lines.append(docs)
    return "\n".join(line for line in lines if line).strip()


def run_bot(
    bot: Bot,
    task: str,
    *,
    project: Project,
    registry: Registry,
    extra_context: str = "",
    max_rounds: int = 0,
) -> str:
    """Execute ``task`` as ``bot``; return a summary for Chief."""
    goal = str(task or "").strip()
    if not goal:
        return f"Error: {bot.name} needs a task."
    if nest_depth() >= _MAX_NEST:
        return "Error: nested bots cannot spawn further bots."
    provider = current_provider()
    if provider is None:
        return "Error: no chat provider bound for this bot."

    names = builtin_names_for_role(bot.role, bot.tools or None)
    names = [name for name in names if name != "ask_user"]
    for extra in registry.global_tools:
        if extra not in names and extra != "ask_user":
            names.append(extra)

    saved_schema, saved_exec = snapshot_loaded_tools()
    clear_loaded_tools()
    preload_note = preload_bot_mcp(
        bot.mcp_tools,
        global_mcp=registry.global_mcp_tools,
        inherit_global=bot.inherit_global_mcp,
    )
    schema = schema_for_names(names)
    allow = allowed_names(schema)
    rounds = delegate_round_budget(max_rounds, bot=bot)

    system = bot.system_prompt.strip() or (
        f"You are {bot.name}, a {bot.role} bot. Complete the assigned task with tools. "
        "Do not chat with the user. Return a concise result for Chief."
    )
    system += (
        "\n\nYou are a specialist bot, not Jinx. The human never sees this turn. "
        "Do not address the user or call ask_user. Work in the shared computer folder. "
        "If that folder is empty, do not loop search_files or web_fetch — scaffold "
        "with run_command (npm create vite@latest . -- --template react) or write_file. "
        "Call update_todo to break the task into steps, then implement. "
        "Read existing files once, then write. "
        "Call list_teams, list_members, then get_member for bios and photos. "
        "Summarize what you did, key paths, and what remains.\n\n"
        + _project_context(project, registry)
    )
    kickoff = str(project.state.get("kickoff") or "").strip()
    extra = extra_context.strip()
    if kickoff and kickoff not in extra and kickoff not in goal:
        packed = "User brief:\n" + kickoff
        if extra:
            packed += "\n\nChief notes:\n" + extra
        extra = packed
    if extra:
        system += "\n\nAdditional context from Chief:\n" + extra
    if preload_note:
        system += "\n\nAssigned MCP tools are already loaded for this turn."
    role = (bot.role or "").strip().lower()
    if role in {"developer", "designer", "architect", "qa"}:
        from ..agent_context import read_skill

        playbook = read_skill("static-web")
        if playbook and not playbook.startswith("Unknown skill"):
            system += "\n\n" + playbook
        if role == "developer":
            system += (
                "\nIf package.json is missing, your first tool must be run_command "
                "with command `npm create vite@latest . -- --template react` and cwd "
                "the shared computer. Then npm install bootstrap @fortawesome/fontawesome-free. "
                "Then write_file. Never mark the task done without those files on disk."
            )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": goal},
    ]
    emit_activity(bot.name, goal[:88])
    depth_token = push_depth()
    bot_token = set_bot_id(bot.id)
    allow_token = set_tool_allowlist(allow)
    try:
        summary = provider.run_agent(
            messages,
            schema,
            execute_tool,
            max_rounds=rounds,
            abort=current_abort(),
            on_tools=lambda tool_names: emit_activity(
                bot.name, ", ".join(tool_names)[:88]
            ),
        )
    except Exception as exc:  # noqa: BLE001
        summary = f"{bot.name} failed: {exc}"
    finally:
        reset_tool_allowlist(allow_token)
        reset_bot_id(bot_token)
        reset_depth(depth_token)
        restore_loaded_tools(saved_schema, saved_exec)

    text = (summary or "").strip() or f"({bot.name} returned empty)"
    if len(text) > _SUMMARY_CAP:
        text = text[:_SUMMARY_CAP] + f"\n... [truncated, {len(summary)} chars]"
    return f"[{bot.name} / {bot.role}]\n{text}"
