"""Chief of Staff — coordinates teams. The user may also message a bot 1:1."""

from __future__ import annotations

from typing import Any

from .models import Bot, ChiefSpec, Project, Registry, Team
from .registry import get_bot, get_team, load_registry, save_registry
from .runtime import current_project_id
from .runner import run_bot
from .toolset import ROLE_PACKS, builtin_names_for_role, schema_for_names

CHIEF_TOOL_NAMES = frozenset(
    {
        "list_project",
        "list_bots",
        "delegate_to_bot",
        "delegate_to_team",
        "update_project_state",
    }
)


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }

DEFAULT_CHIEF_PROMPT = """You are Chief of Staff — the coordinator for this project.
You are not Jinx and not a team member. The human is talking to you in Everyone.

For the active project:
1. If a User kickoff / latest request already describes the work, that IS the brief.
   Do not ask whether they are ready, what is next, or Continue/Stop.
2. Call update_todo first: break the request into a short checklist, then work it.
3. Catalog only as needed: list_teams → list_members(team_id) → get_member(bot_id).
   Do not invent teammates. list_roster dumps every dossier if you need the whole page.
4. Delegate with delegate_to_bot (prefer specific people named in the brief).
   delegate_to_team runs architect → designer → developer → QA on that team,
   not the lead alone (the lead often cannot write files).
   Put the FULL user brief in `task`. Pass other bots' findings in `context`.
   Do not pass max_rounds — the harness runs until the work is done (Stop ends the turn).
5. Specialists write the files on the shared computer. You do not implement the site yourself.
6. After they return, if work is still in flight, delegate again. Then report: who did what,
   which files, what remains. Never quiz the human to continue.

ask_user is only for a real blocking decision (which team, a legal/money call, a missing folder).
Never use it for greetings, confirmations, or Continue/Stop.

Typical website order: architect (structure) → designer (look) → developer (files).
Never print tool-call JSON. Never claim a bot finished unless you called it.
Never speak as Jinx the product mascot.
"""


def _registry() -> Registry:
    return load_registry()


def _project(project_id: str = "") -> tuple[Registry, Project] | str:
    rid = (project_id or current_project_id()).strip()
    registry = _registry()
    if not rid:
        names = ", ".join(f"{p.name} [{p.id}]" for p in registry.projects.values()) or "(none)"
        return (
            "No project is bound to this chat. The Bots roster should auto-bind "
            f"the default project. Known projects: {names}."
        )
    project = registry.projects.get(rid)
    if project is None:
        return f"Unknown project {rid!r}."
    return registry, project


def describe_project(project: Project, registry: Registry) -> str:
    lines = [
        f"Project: {project.name} ({project.id})",
        project.description or "(no description)",
    ]
    if project.folders:
        lines.append("Shared computer:")
        for folder in project.folders:
            lines.append(f"  - {folder}")
    else:
        lines.append(
            "Shared computer: (none — attach a folder on the project or in Files; "
            "otherwise tools use session scratch)"
        )
    kickoff = str(project.state.get("kickoff") or "").strip()
    if kickoff:
        lines.append("User kickoff:")
        lines.append(kickoff[:4000])
    lines.append("Assigned teams:")
    teams = registry.assigned_teams(project)
    if not teams:
        lines.append("  (none — bind teams to this project first)")
    for team in teams:
        lead = ""
        if team.lead_bot_id and team.lead_bot_id in registry.bots:
            lead_bot = registry.bots[team.lead_bot_id]
            lead = f" — lead {lead_bot.name} [{lead_bot.id}]"
        lines.append(f"  - {team.name} [{team.id}]{lead}: {team.description}".rstrip())
        n = len(registry.bot_members(team))
        lines.append(
            f"      {n} member{'s' if n != 1 else ''} — "
            "list_members team_id, then get_member bot_id."
        )
    if project.state:
        lines.append("Shared state:")
        for key, value in list(project.state.items())[:32]:
            lines.append(f"  - {key}: {value}")
    recent = project.events[-16:]
    if recent:
        lines.append("Recent memory:")
        for event in recent:
            who = event.bot_id or event.role
            lines.append(f"  - {who}: {event.text.replace(chr(10), ' ')[:200]}")
    return "\n".join(lines)


def build_chief_prompt(project_id: str) -> str:
    from ..agent_context import load_workspace_docs, skills_prompt_index
    from ..here import here_and_now
    from ..workspace import current_folders, current_workspace

    resolved = _project(project_id)
    if isinstance(resolved, str):
        roster = resolved
        project_name = "(none)"
    else:
        registry, project = resolved
        roster = describe_project(project, registry)
        project_name = project.name

    chief = _registry().chief
    docs = load_workspace_docs()
    skills = skills_prompt_index()
    folders = ", ".join(str(p) for p in current_folders()) or "(none)"
    body = (chief.system_prompt or "").strip() or DEFAULT_CHIEF_PROMPT
    parts = [
        body,
        here_and_now(),
        "You run inside the Jinx AI desktop app. You are Chief of Staff, not Jinx.",
        f"Active project: {project_name} ({project_id or 'unbound'}).",
        f"Shared computer folders: {folders}. Scratch: {current_workspace()}.",
        "Everyone + 1:1 share that folder. Project docs below (AGENTS.md / SYSTEM.md / README) "
        "are the user's project, not Jinx source. Shared memory is project state and recent events. "
        "Call list_teams, then list_members, then get_member for a dossier. "
        "Do not invent people. list_roster dumps every card if you need the whole team page.",
        "When they message you, coordinate. They may also open a specialist 1:1.",
        "If User kickoff or the latest message already states the work, start: update_todo, then delegate. "
        "Do not ask_user for ready / continue / what next. Re-delegate if a bot hits the round cap.",
        "Languages: reply in English.\n"
        "Tone: first-person (I). After delegations, a real report — what each bot did and which files.",
        "Never print tool-call JSON. Use native function calling only.",
        roster,
    ]
    if docs:
        parts.append(docs)
    if skills:
        parts.append(skills)
    return "\n\n".join(part for part in parts if part)


def is_chief_bot(bot_id: str) -> bool:
    return str(bot_id or "").strip().lower() in {"", "chief"}


def schema_for_direct_bot(bot_id: str) -> list[dict[str, Any]]:
    """Tools for a 1:1 roster chat: Chief orchestrates; specialists use their pack."""
    if is_chief_bot(bot_id):
        return chief_tool_schema()
    bot = get_bot(str(bot_id).strip())
    if bot is None:
        return chief_tool_schema()
    registry = _registry()
    names = builtin_names_for_role(bot.role, bot.tools or None)
    for extra in registry.global_tools:
        if extra not in names:
            names.append(extra)
    return schema_for_names(names)


def build_direct_bot_prompt(bot_id: str, project_id: str = "") -> str:
    """System prompt when the human messages a named Bot from the roster."""
    if is_chief_bot(bot_id):
        return build_chief_prompt(project_id)

    from ..agent_context import load_workspace_docs, skills_prompt_index
    from ..here import here_and_now
    from ..workspace import current_folders, current_workspace

    registry = _registry()
    bot = registry.bots.get(str(bot_id).strip())
    if bot is None:
        return build_chief_prompt(project_id)

    resolved = _project(project_id)
    if isinstance(resolved, str):
        roster = resolved
        project_name = "(none)"
    else:
        _reg, project = resolved
        roster = describe_project(project, registry)
        project_name = project.name

    docs = load_workspace_docs()
    skills = skills_prompt_index()
    folders = ", ".join(str(p) for p in current_folders()) or "(none)"
    body = (bot.system_prompt or "").strip() or (
        f"You are {bot.name}, a {bot.role} teammate."
    )
    parts = [
        body,
        here_and_now(),
        f"You are {bot.name} ({bot.role}). This is a 1:1 thread. You are not Jinx.",
        "Talk to them directly. You cannot spawn other bots. Do the work with your tools. "
        "If another specialist is needed, tell the human to message Chief of Staff (Everyone).",
        f"Active project: {project_name} ({project_id or 'unbound'}).",
        f"Shared computer: {folders}. Scratch: {current_workspace()}.",
        "Everyone + 1:1 share that folder. Read existing files before writing. "
        "Project docs below are the user's project. Shared memory is project state and recent events. "
        "Call list_teams / list_members / get_member to look up teammates "
        "(name, title, role, bio, photo) — do not guess.",
        "Languages: reply in English.\nTone: first-person (I). Never print tool-call JSON.",
        roster,
    ]
    if docs:
        parts.append(docs)
    if skills:
        parts.append(skills)
    return "\n\n".join(part for part in parts if part)


def chief_tool_schema() -> list[dict[str, Any]]:
    names = list(ROLE_PACKS["chief"])
    extra = _registry().chief.tools
    for name in extra:
        if name not in names:
            names.append(name)
    schema = schema_for_names([n for n in names if n not in CHIEF_TOOL_NAMES])
    schema.extend(
        [
            _fn(
                "list_project",
                "Show the active project's assigned teams, bots, MCP tools, and shared state. "
                "Call this before delegating.",
                {
                    "project_id": {
                        "type": "string",
                        "description": "Optional project id; default is the chat-bound project.",
                    }
                },
                [],
            ),
            _fn(
                "list_bots",
                "List bots available on the active project (optional team or role filter).",
                {
                    "team_id": {"type": "string", "description": "Limit to one team id."},
                    "role": {"type": "string", "description": "Limit to one role (developer, qa, …)."},
                },
                [],
            ),
            _fn(
                "delegate_to_bot",
                "Run one specialist bot on the project. Returns that bot's summary. "
                "The bot uses only its assigned builtin + MCP tools. "
                "The harness runs until the work is done (Stop ends the turn) — do not pass max_rounds.",
                {
                    "bot_id": {"type": "string", "description": "Bot id from list_project / list_bots."},
                    "task": {"type": "string", "description": "What this bot must accomplish."},
                    "context": {
                        "type": "string",
                        "description": "Optional extra brief (other bots' findings, constraints).",
                    },
                },
                ["bot_id", "task"],
            ),
            _fn(
                "delegate_to_team",
                "Route a task to a project team. If bot_id is set, that member runs. "
                "Otherwise the team lead runs, or every member in sequence. "
                "The harness runs until the work is done (Stop ends the turn) — do not pass max_rounds.",
                {
                    "team_id": {"type": "string", "description": "Team id from list_project."},
                    "task": {"type": "string", "description": "Work for the team."},
                    "bot_id": {
                        "type": "string",
                        "description": "Optional specific member. Empty = lead, else all members.",
                    },
                    "context": {"type": "string", "description": "Optional extra brief."},
                },
                ["team_id", "task"],
            ),
            _fn(
                "update_project_state",
                "Write a key into the project's shared memory (visible to later bots and Chief).",
                {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                ["key", "value"],
            ),
        ]
    )
    from ..harness.deferred import deferred_tool_schemas, extra_tool_schemas

    have = {str((item.get("function") or {}).get("name") or "") for item in schema}
    for item in deferred_tool_schemas() + extra_tool_schemas():
        name = str((item.get("function") or {}).get("name") or "")
        if name and name not in have:
            schema.append(item)
            have.add(name)
    return schema


def _bot_on_project(registry: Registry, project: Project, bot: Bot) -> bool:
    return bot.id in {item.id for item in registry.project_bots(project)}


def execute_chief_tool(name: str, arguments: dict[str, Any] | None = None) -> str:
    args = dict(arguments or {})
    if name == "list_project":
        resolved = _project(str(args.get("project_id") or ""))
        if isinstance(resolved, str):
            return resolved
        registry, project = resolved
        return describe_project(project, registry)
    if name == "list_bots":
        resolved = _project()
        if isinstance(resolved, str):
            return resolved
        registry, project = resolved
        team_id = str(args.get("team_id") or "").strip()
        role = str(args.get("role") or "").strip().lower()
        bots = registry.project_bots(project)
        if team_id:
            bots = [b for b in bots if b.team_id == team_id]
        if role:
            bots = [b for b in bots if b.role == role]
        if not bots:
            return "No matching bots on this project."
        lines = []
        for bot in bots:
            lines.append(
                f"- {bot.name} [{bot.id}] role={bot.role} team={bot.team_id or '-'} "
                f"mcp={', '.join(bot.mcp_tools) or 'none'}"
            )
        return "\n".join(lines)
    if name == "update_project_state":
        resolved = _project()
        if isinstance(resolved, str):
            return resolved
        registry, project = resolved
        key = str(args.get("key") or "").strip()
        if not key:
            return "Error: update_project_state needs a key."
        project.state[key] = args.get("value")
        save_registry(registry)
        return f"Saved {key} on {project.name}."
    if name == "delegate_to_bot":
        return _delegate_bot(
            str(args.get("bot_id") or ""),
            str(args.get("task") or args.get("goal") or args.get("prompt") or ""),
            context=str(args.get("context") or ""),
        )
    if name == "delegate_to_team":
        return _delegate_team(
            str(args.get("team_id") or ""),
            str(args.get("task") or args.get("goal") or args.get("prompt") or ""),
            bot_id=str(args.get("bot_id") or ""),
            context=str(args.get("context") or ""),
        )
    return f"Error: unknown Chief tool '{name}'."


def _delegate_bot(
    bot_id: str,
    task: str,
    *,
    context: str = "",
    max_rounds: int = 0,
) -> str:
    resolved = _project()
    if isinstance(resolved, str):
        return resolved
    registry, project = resolved
    bot = get_bot(bot_id.strip())
    if bot is None:
        return f"Unknown bot {bot_id!r}. Call list_bots."
    if not _bot_on_project(registry, project, bot):
        return (
            f"{bot.name} is not assigned to project {project.name}. "
            "Only bots on assigned teams may run."
        )
    result = run_bot(
        bot,
        task,
        project=project,
        registry=registry,
        extra_context=context,
        max_rounds=max_rounds,
    )
    project.remember(result, role="bot", bot_id=bot.id, team_id=bot.team_id or "")
    save_registry(registry)
    return result


_BUILD_ROLES = ("architect", "designer", "developer", "qa")


def bots_for_team_task(team: Team, members: list[Bot], registry: Registry) -> tuple[list[Bot], str]:
    """Who actually runs a team-wide task.

    Team lead is often a coordinator without write_file / run_command.
    Prefer architect → designer → developer → QA so files land on disk.
    """
    by_role: dict[str, list[Bot]] = {role: [] for role in _BUILD_ROLES}
    for bot in members:
        role = (bot.role or "").strip().lower()
        if role in by_role:
            by_role[role].append(bot)
    chosen: list[Bot] = []
    for role in _BUILD_ROLES:
        chosen.extend(by_role[role])
    if chosen:
        names = ", ".join(bot.name for bot in chosen)
        return chosen, f"(routed to {names} in sequence — not only the lead)"
    if team.lead_bot_id and team.lead_bot_id in registry.bots:
        lead = registry.bots[team.lead_bot_id]
        return [lead], f"(routed to team lead {lead.name})"
    return list(members), f"(routed to {len(members)} member(s) in sequence)"


def _delegate_team(
    team_id: str,
    task: str,
    *,
    bot_id: str = "",
    context: str = "",
    max_rounds: int = 0,
) -> str:
    resolved = _project()
    if isinstance(resolved, str):
        return resolved
    registry, project = resolved
    team = get_team(team_id.strip())
    if team is None:
        return f"Unknown team {team_id!r}. Call list_project."
    if team.id not in project.team_ids:
        return f"Team {team.name} is not assigned to {project.name}."
    members = registry.bot_members(team)
    if not members:
        return f"Team {team.name} has no bots."
    if bot_id.strip():
        return _delegate_bot(bot_id, task, context=context, max_rounds=max_rounds)
    chosen, note = bots_for_team_task(team, members, registry)
    chunks = [f"Team {team.name} {note}"]
    prior = context
    for bot in chosen:
        brief = prior
        result = run_bot(
            bot,
            task,
            project=project,
            registry=registry,
            extra_context=brief,
            max_rounds=max_rounds,
        )
        project.remember(result, role="bot", bot_id=bot.id, team_id=team.id)
        chunks.append(result)
        prior = (prior + "\n\n" + result).strip()
    save_registry(registry)
    return "\n\n".join(chunks)


class ChiefBot:
    """Singleton orchestrator. Not a team member."""

    _instance: ChiefBot | None = None

    def __init__(self, spec: ChiefSpec | None = None) -> None:
        self.spec = spec or load_registry().chief

    @classmethod
    def instance(cls) -> ChiefBot:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def name(self) -> str:
        return self.spec.name

    def as_bot(self) -> Bot:
        return self.spec.as_bot()
