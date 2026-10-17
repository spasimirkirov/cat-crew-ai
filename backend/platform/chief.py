"""Cat Crew — the orchestrator / system chat (module name kept for import stability).

Cat Crew is the unconstrained chat where the human talks to the primary model:
steer progress, analyse requirements, and trigger the specialist cats. It is not
an agent — it has no tool/skill assignments (``agent_tools`` / ``agent_skills``);
it uses built-in engine routing. Work messages are classified by
``platform/orchestrator.py`` and run by ``platform/sop.py`` (specialists get
scoped contexts, streamed live over the WebSocket); everything else is a normal
chat turn built from the prompt and read/lookup tool schema in this module. From
a chat turn the model can ask for delegation with ``hand_off_to_crew``. This
module also holds the shared building blocks ``sop.py`` reuses —
``_format_delegation_result`` (the QA verdict banner) and
``_run_delegated_logged`` (activity-board-aware ``run_delegated``)."""

from __future__ import annotations

from typing import Any

from . import activity_board
from .models import Bot, ChiefSpec, Project, Registry
from .registry import (
    find_bot,
    get_bot,
    load_registry,
    save_registry,
    staff_bot_on_project,
)
from .delegation import run_delegated
from .runtime import current_project_id
from .orchestrator import ROOM_ID, is_room_id
from .toolset import ROLE_PACKS, builtin_names_for_role, schema_for_names

CHIEF_TOOL_NAMES = frozenset(
    {
        "list_project",
        "list_bots",
        "delegate_to_bot",
        "update_project_state",
        "hand_off_to_crew",
    }
)


HANDOFF_ROLES = (
    "prompt_engineer", "vanguard", "developer", "designer", "qa", "legal", "marketing", "devops",
    "accountant",
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

DEFAULT_CHIEF_PROMPT = """You are Cat Crew — the orchestrator of this project and the chat where the human
talks to the primary model directly. Nothing here is scripted: converse freely, think out loud,
analyse requirements, challenge unclear ideas, plan, and answer questions about the project and
the crew.

The specialist cats (Cipher, Vanguard, Pixel, Pulse, Aegis, Lex, Blaze, Forge) do the hands-on
work with their own tools and skills — you do not have those and you never pretend to. When the
conversation turns into real work (build, fix, design, test, deploy, write, review…), call
hand_off_to_crew with a self-contained brief (and `roles` to involve only some cats; omit it
to run the whole pipeline). The specialists then start right after your reply and post their real
output in this chat. Explain in one or two sentences who you are handing what to. If the human
names a cat with @name, that cat takes the message directly.

Use your read-only tools (lookups, files, web) to ground your answers before you hand off. Never
claim you built, fixed or ran something yourself. Never print tool-call JSON.
"""


_LEGACY_PROMPT_PREFIX = "You are the assistant for this chat. No project is bound yet"


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


def describe_project(project: Project, registry: Registry, *, live: bool = True) -> str:
    """The project card. ``live=False`` leaves out the parts that change from message to message
    (shared state, recent memory): a system prompt carries the stable card, and the live parts go
    into the turn note (``turn_context``), so a provider's prompt cache survives between turns."""
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
    lines.append("Bots on this project:")
    staffed = registry.project_bots(project)
    if not staffed:
        lines.append("  (none staffed yet)")
        catalog = ", ".join(
            f"{bot.name} [{bot.id}]" for bot in registry.bots.values()
        ) or "(empty catalog)"
        lines.append(
            f"  Catalog: {catalog}. delegate_to_bot by name staffs them on this project."
        )
    else:
        for bot in staffed:
            lines.append(f"  - {bot.name} [{bot.id}] ({bot.role}): {bot.description}".rstrip())
        lines.append("      list_members, then get_member bot_id for a dossier.")
    if live:
        lines.extend(_live_project_lines(project))
    return "\n".join(lines)


_STATE_VALUE_CAP = 600


def _live_project_lines(project: Project) -> list[str]:
    lines: list[str] = []
    state = {key: value for key, value in project.state.items() if key != "kickoff"}
    if state:
        lines.append("Shared state:")
        for key, value in list(state.items())[:32]:
            text = str(value)
            if len(text) > _STATE_VALUE_CAP:
                # created_artifacts alone reaches 300 entries; the newest end is what matters.
                text = "… " + text[-_STATE_VALUE_CAP:]
            lines.append(f"  - {key}: {text}")
    recent = project.events[-16:]
    if recent:
        lines.append("Recent memory:")
        for event in recent:
            who = event.bot_id or event.role
            lines.append(f"  - {who}: {event.text.replace(chr(10), ' ')[:200]}")
    return lines


def turn_context(project_id: str = "") -> str:
    """What changes between messages, for the latest user turn rather than the system prompt:
    the clock and, inside a project, its shared state and recent memory."""
    from backend.services.here import here_and_now

    parts = [here_and_now()]
    try:
        resolved = _project(project_id)
    except Exception:  # noqa: BLE001 - a broken catalog must not break the turn
        resolved = ""
    if not isinstance(resolved, str):
        _reg, project = resolved
        live = _live_project_lines(project)
        if live:
            parts.append("\n".join(live))
    return "\n".join(parts)


def build_chief_prompt(project_id: str) -> str:
    from ..agent_context import load_workspace_docs, skills_prompt_index
    from backend.harness.turn_note import DATA_NOT_ORDERS
    from backend.services.workspace import current_folders, current_workspace

    resolved = _project(project_id)
    if isinstance(resolved, str):
        roster = resolved
        project_name = "(none)"
    else:
        registry, project = resolved
        roster = describe_project(project, registry, live=False)
        project_name = project.name

    chief = _registry().chief
    docs = load_workspace_docs()
    skills = skills_prompt_index(ROOM_ID)  # global skills only: Cat Crew has no assignments
    folders = ", ".join(str(p) for p in current_folders()) or "(none)"
    stored = (chief.system_prompt or "").strip()
    # Installs seeded before the Cat Crew rename stored the old "no project bound, no
    # delegate tool" default verbatim; that text is stale, so it is treated as unset.
    if stored.startswith(_LEGACY_PROMPT_PREFIX):
        stored = ""
    body = stored or DEFAULT_CHIEF_PROMPT
    parts = [
        body,
        "You run inside the CatCrewAI desktop app, in the Cat Crew chat of this project. You are "
        "the orchestrator, not a specialist. Work requests the human sends are also auto-routed "
        "to the right cats by the engine (platform/sop.py), so a plain question stays a "
        "conversation with you.",
        f"Active project: {project_name} ({project_id or 'unbound'}).",
        f"Shared computer folders: {folders}. Scratch: {current_workspace()}.",
        "Cat Crew + each cat's 1:1 thread share that folder. Project docs below (AGENTS.md / SYSTEM.md / "
        "README) are the user's project, not CatCrewAI source. Shared memory is project state and "
        "recent events. "
        "Call list_members, then get_member for a dossier. "
        "Lookups see the whole catalog, not only the bots staffed on this project. "
        "Do not invent people. list_roster dumps every card if you need the whole roster.",
        "You cannot run a specialist yourself — hand_off_to_crew(brief, roles?) asks the engine to "
        "start them after this reply. Use it when the human wants work done and it is clear enough "
        "to brief; ask one clarifying question first when it is not.",
        "Languages: reply in the human's language.\n"
        "Tone: first-person (I). After a hand-off, say briefly what you delegated and to whom.",
        "Never print tool-call JSON. Use native function calling only.",
        DATA_NOT_ORDERS,
        roster,
    ]
    if docs:
        parts.append(docs)
    if skills:
        parts.append(skills)
    return "\n\n".join(part for part in parts if part)


def is_chief_bot(bot_id: str) -> bool:
    """True for Cat Crew (the orchestrator), by its current or legacy id."""
    return is_room_id(bot_id)


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
    from backend.harness.turn_note import DATA_NOT_ORDERS
    from backend.services.workspace import current_folders, current_workspace

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
        roster = describe_project(project, registry, live=False)
        project_name = project.name

    docs = load_workspace_docs()
    skills = skills_prompt_index(bot.id)
    folders = ", ".join(str(p) for p in current_folders()) or "(none)"
    body = (bot.system_prompt or "").strip() or (
        f"You are {bot.name}, a {bot.role} teammate."
    )
    parts = [
        body,
        f"You are {bot.name} ({bot.role}). This is a 1:1 thread. You are not CatCrewAI.",
        "Talk to them directly. You cannot spawn other bots. Do the work with your tools. "
        "If another specialist is needed, tell the human to post in the Cat Crew "
        "(or @mention that specialist there).",
        "NON-NEGOTIABLE: never dump a full code file or a multi-line CSS/JS block "
        "directly in your reply — that is not a file on disk. Generate code only through "
        "write_file / replace_in_file, and keep your chat text a short status update. "
        "Never print a fake terminal transcript either — real commands run through "
        "run_command; only its actual output counts as evidence something happened.",
        f"Active project: {project_name} ({project_id or 'unbound'}).",
        f"Shared computer: {folders}. Scratch: {current_workspace()}.",
        "The Cat Crew + 1:1 share that folder. Read existing files before writing. "
        "Project docs below are the user's project. Shared memory is project state and recent events. "
        "Call list_members / get_member to look up teammates "
        "(name, title, role, bio, photo) — do not guess.",
        "Languages: reply in English.\nTone: first-person (I). Never print tool-call JSON.",
        DATA_NOT_ORDERS,
        roster,
    ]
    if docs:
        parts.append(docs)
    if skills:
        parts.append(skills)
    return "\n\n".join(part for part in parts if part)


def chief_tool_schema() -> list[dict[str, Any]]:
    names = list(ROLE_PACKS["orchestrator"])
    extra = _registry().chief.tools
    for name in extra:
        if name not in names:
            names.append(name)
    schema = schema_for_names([n for n in names if n not in CHIEF_TOOL_NAMES])
    schema.extend(
        [
            _fn(
                "list_project",
                "Show the active project's bots, MCP tools, and shared state. "
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
                "List bots available on the active project (optional role filter).",
                {
                    "role": {"type": "string", "description": "Limit to one role (developer, qa, …)."},
                },
                [],
            ),
            _fn(
                "hand_off_to_crew",
                "Ask the engine to run the specialist cats on a piece of work, right after this "
                "reply. Give a self-contained brief (goal, constraints, stack, files). Optionally "
                "limit `roles` to some specialists; omit it to run the whole pipeline.",
                {
                    "brief": {"type": "string", "description": "What the cats should do, self-contained."},
                    "roles": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(HANDOFF_ROLES)},
                        "description": "Only these roles (omit for the whole pipeline).",
                    },
                    "resume": {
                        "type": "boolean",
                        "description": "True when the human wants the crew to finish or retry the work "
                        "already planned for this project (\"continue\", \"try again\", \"finish the site\"): "
                        "the run picks up at the first unfinished phase instead of planning everything again.",
                    },
                },
                ["brief"],
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
        role = str(args.get("role") or "").strip().lower()
        bots = registry.project_bots(project)
        if role:
            bots = [b for b in bots if b.role == role]
        if not bots:
            bots = list(registry.bots.values())
            if role:
                bots = [b for b in bots if b.role == role]
            if not bots:
                return "No matching bots in the catalog."
            lines = [
                "No bots staffed on this project yet. Catalog "
                "(delegate_to_bot staffs them directly):"
            ]
        else:
            lines = []
        for bot in bots:
            lines.append(
                f"- {bot.name} [{bot.id}] role={bot.role} "
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
    if name == "hand_off_to_crew":
        return _hand_off(args)
    if name == "delegate_to_bot":
        return _delegate_bot(
            str(args.get("bot_id") or ""),
            str(args.get("task") or args.get("goal") or args.get("prompt") or ""),
            context=str(args.get("context") or ""),
        )
    return f"Error: unknown Chief tool '{name}'."


def _hand_off(args: dict[str, Any]) -> str:
    """Record a hand-off request; ``ChatService`` starts the specialists when this
    chat turn ends (they must stream to the UI, not run inside this tool call)."""
    from . import handoff

    brief = str(args.get("brief") or args.get("task") or "").strip()
    if not brief:
        return "Error: hand_off_to_crew needs a brief."
    pid = current_project_id()
    if not pid:
        return "Error: no project is bound to this chat."
    raw = args.get("roles") or []
    if isinstance(raw, str):
        raw = [raw]
    roles = tuple(dict.fromkeys(str(r).strip().lower() for r in raw if str(r).strip() in HANDOFF_ROLES))
    resume = args.get("resume") in (True, "true", "True", 1)
    handoff.request(pid, brief, roles or None, resume=bool(resume))
    who = ", ".join(roles) if roles else "the whole crew"
    return f"Hand-off recorded for {who}. The specialists start right after your reply — tell the human what you delegated."


def _format_delegation_result(bot: Bot, result: str) -> str:
    """QA's result gets an unmissable banner instead of blending in with
    everyone else's — the whole point being that Chief (and the human, via
    the tool-preview card) cannot skim past a pass/fail verdict."""
    if (bot.role or "").strip().lower() != "qa":
        return result
    return f"=== QA VERDICT — {bot.name} ===\n{result}\n=== END QA VERDICT ==="


def _run_delegated_logged(
    bot: Bot,
    task: str,
    *,
    project: Project,
    registry: Registry,
    extra_context: str = "",
    max_rounds: int = 0,
    collect_tool_events: list[str] | None = None,
    activity_owner: str = ROOM_ID,
) -> str:
    """``run_delegated`` plus the observability the UI needs: which bot is
    being addressed (sidebar activity line, live while the call blocks this
    thread) and a QA result that is impossible to miss once it returns.

    ``activity_owner`` is whose sidebar activity line shows "Delegating to
    X" while this call blocks — Cat Crew for the old LLM orchestrator tool
    path, or e.g. "jayce" when ``platform/sop.py``'s deterministic Project
    Room pipeline is the one making the call.
    """
    activity_board.set_activity(activity_owner, f"Delegating to {bot.name}: {task[:60]}")
    try:
        result = run_delegated(
            bot,
            task,
            project=project,
            registry=registry,
            extra_context=extra_context,
            max_rounds=max_rounds,
            collect_tool_events=collect_tool_events,
        )
    finally:
        activity_board.touch(activity_owner)
    return _format_delegation_result(bot, result)


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
    bot = find_bot(bot_id.strip())
    if bot is None:
        known = ", ".join(
            f"{row.name} [{row.id}]" for row in registry.bots.values()
        ) or "(empty catalog)"
        return f"Unknown bot {bot_id!r}. Catalog: {known}."
    note = staff_bot_on_project(registry, project, bot)
    result = _run_delegated_logged(
        bot,
        task,
        project=project,
        registry=registry,
        extra_context=context,
        max_rounds=max_rounds,
    )
    project.remember(result, role="bot", bot_id=bot.id)
    save_registry(registry)
    if note:
        result = note + "\n" + result
    follow = _follow_up_developer(registry, project, bot, result, context=context)
    if follow:
        result = result + "\n\n" + follow
    return result


_BUILD_ROLES = ("vanguard", "architect", "designer", "developer", "qa")


def _looks_like_build_brief(project: Project) -> bool:
    """True when the brief asks for software to be built, in any stack."""
    blob = " ".join(
        [
            str(project.state.get("kickoff") or ""),
            project.description or "",
            project.name or "",
        ]
    ).lower()
    return any(
        token in blob
        for token in (
            "build",
            "implement",
            "develop",
            "app",
            "site",
            "website",
            "page",
            "api",
            "dashboard",
            "software",
        )
    )


def _follow_up_developer(
    registry: Registry,
    project: Project,
    bot: Bot,
    result: str,
    *,
    context: str = "",
) -> str:
    """Architect/designer notes are not a product. A build brief still needs the developer."""
    if (bot.role or "").strip().lower() == "developer":
        return ""
    if "/ developer]" in (result or ""):
        return ""  # a developer already ran inside this delegation chain
    if not project.folders or not _looks_like_build_brief(project):
        return ""
    builder = next(
        (
            row
            for row in registry.project_bots(project)
            if (row.role or "").strip().lower() == "developer"
        ),
        None,
    )
    if builder is None:
        builder = find_bot("pulse")
    if builder is None or builder.id == bot.id:
        return ""
    staff_bot_on_project(registry, project, builder)
    built = _run_delegated_logged(
        builder,
        "Implement the user's brief on the shared computer, following the specialist notes "
        "in context. Inspect the folder first: if part of the product already exists, verify "
        "it against the brief and finish the missing parts instead of rebuilding. Use the "
        "stack the brief asks for, scaffold with real commands, write real files, and leave "
        "it built/runnable when the brief expects deployment.",
        project=project,
        registry=registry,
        extra_context=context,
    )
    project.remember(built, role="bot", bot_id=builder.id)
    save_registry(registry)
    return built


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
