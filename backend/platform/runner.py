"""Run one specialist bot through the existing ``provider.run_agent`` loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..harness.deferred import (
    clear_loaded_tools,
    restore_loaded_tools,
    snapshot_loaded_tools,
)
from ..activity import phase_for
from ..harness.events import emit_activity
from ..harness.runtime import current_abort, current_provider, nest_depth, push_depth, reset_depth
from backend.services.settings_store import agent_settings
from ..tools import execute_tool, reset_tool_allowlist, set_tool_allowlist
from . import activity_board
from .models import Bot, Project, Registry
from .runtime import reset_bot_id, reset_project_id, set_bot_id, set_project_id
from .toolset import (
    allowed_names,
    builtin_names_for_role,
    preload_bot_mcp,
    schema_for_names,
)

_SUMMARY_CAP = 8_000
_MAX_NEST = 1


def delegate_round_budget(requested: int = 0, *, bot: Bot | None = None) -> int:
    """Nested bot round cap.

    Settings ``max_rounds`` > 0 is the floor (as before). When it is 0 (unlimited, the default),
    delegated work still gets ``DELEGATE_MAX_ROUNDS`` (default 40, ``CATCREW_DELEGATE_MAX_ROUNDS``)
    so a drifting specialist ends as ``blocked`` instead of running until Stop. An explicit
    ``requested`` wins in that case; ``DELEGATE_MAX_ROUNDS=0`` restores unlimited.
    """
    try:
        settings = int(agent_settings().get("max_rounds") or 0)
    except (TypeError, ValueError):
        settings = 0
    if settings <= 0:
        from backend.config import DELEGATE_MAX_ROUNDS

        req = int(requested or 0)
        if req > 0:
            return req
        return max(0, int(DELEGATE_MAX_ROUNDS or 0))
    bot_n = int(getattr(bot, "max_rounds", 0) or 0)
    floor = max(settings, bot_n, 8)
    req = int(requested or 0)
    if req <= 0:
        return floor
    return max(req, floor)


def _project_context(project: Project, registry: Registry) -> str:
    """A specialist's ambient project context — deliberately narrow (CatCrewAI
    "Context Isolation": a specialist's runtime context is the base prompt +
    assigned skills + its own assigned task + relevant file paths/diffs, NOT
    the full project conversation tree).

    This used to also dump the last 16 project events (every bot's recent
    chat, unfiltered) and the raw ``project.state`` dict (which grew to
    include the whole ``tasks``/``phase_outputs``/``created_artifacts``
    JSON blobs from the SOP engine's own bookkeeping) into every single
    delegated turn — redundant with the explicit hand-off ``sop.py`` already
    threads through ``extra_context`` (each phase's task text literally
    contains "Original brief: ..." and the prior phase's output), and the
    exact "full conversation tree" the isolation requirement calls out.
    What's still legitimately ambient — not task-specific, not something a
    hand-off already carries — is: project identity, the shared computer
    folder (needed for every file tool call), workspace conventions
    (AGENTS.md/SYSTEM.md), and a short pointer to the most recent file
    changes (paths + who touched them, not full diff bodies)."""
    from ..agent_context import load_workspace_docs
    from backend.services.workspace import current_folders, current_workspace
    from backend.db import ledger as db

    folders = [str(path) for path in (project.folders or []) if path]
    if not folders:
        folders = [str(path) for path in current_folders()]
    lines = [
        f"Project: {project.name} ({project.id})",
        project.description or "",
        "Shared computer: " + (", ".join(folders) or f"(scratch {current_workspace()})"),
    ]
    try:
        changes = db.list_recent_file_changes(project.id, limit=10)
    except Exception:
        changes = []
    if changes:
        lines.append("Recent file changes (path — who — when; not the full diff):")
        for change in changes:
            lines.append(
                f"- {change.get('file_path')} — {change.get('agent_id')} "
                f"({change.get('created_at')})"
            )
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
    stall_rounds: int = 0,
    provider: Any = None,
    abort: Callable[[], bool] | None = None,
    approval_hook: Callable[[str, dict], str] | None = None,
    auto_build_bridge: bool = True,
    collect_tool_events: list[str] | None = None,
) -> str:
    """Execute ``task`` as ``bot``; return a summary for Cat Crew.

    ``provider``/``abort`` default to the ambient ``ContextVar``s
    (``current_provider()``/``current_abort()``) for full backward
    compatibility with any direct caller — but a caller crossing a thread
    boundary (``platform/chief.py``'s engine-routed delegation, see
    ``core/``) must pass them explicitly, since ``ContextVar`` state does
    not automatically follow a call onto a different thread/event loop.

    ``auto_build_bridge=False`` skips the one-shot ``_bridge_build`` check
    below; the engine-routed caller does this for developer bots so it can
    wrap the same check in a bounded verify/auto-heal retry loop
    (``core.verify.verify_and_heal``) instead of a single, un-retried
    attempt.

    ``collect_tool_events``, when given a list, gets one short line
    appended per tool call this bot makes (e.g. "Pulse executed write_file
    on src/App.jsx") — the Cat Crew SOP (``platform/sop.py``) surfaces
    these in the shared chat feed so a bot's work is visible as it
    happens, not hidden inside an opaque delegation result.
    """
    goal = str(task or "").strip()
    if not goal:
        return f"Error: {bot.name} needs a task."
    if nest_depth() >= _MAX_NEST:
        return "Error: nested bots cannot spawn further bots."
    if provider is None:
        provider = current_provider()
    if provider is None:
        return "Error: no chat provider bound for this bot."
    abort_fn = abort if abort is not None else current_abort()
    from backend.harness.turn_note import DATA_NOT_ORDERS
    from backend.harness.approvals import current_approval_hook, reset_approval_hook, set_approval_hook

    gate_hook = approval_hook if approval_hook is not None else current_approval_hook()

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
    from .tool_registry import register_agent_tools

    register_agent_tools(bot.id)
    allow = allowed_names(schema)
    rounds = delegate_round_budget(max_rounds, bot=bot)

    system = bot.system_prompt.strip() or (
        f"You are {bot.name}, a {bot.role} bot. Complete the assigned task with tools. "
        "Do not chat with the user. Return a concise result for Cat Crew."
    )
    system += (
        "\n\nYou are a specialist bot, not CatCrewAI. The human never sees this turn. "
        "Do not address the user or call ask_user. "
        "\n\nNON-NEGOTIABLE execution rules, no exceptions:\n"
        "1. Never dump a full code file, a multi-line CSS/JS block, or any other "
        "production code directly in your chat text. That text is not a file on disk. "
        "All code generation happens through write_file / replace_in_file. Your chat "
        "reply is a short status update — 1 to 3 sentences — naming what you changed "
        "and where, never the code itself.\n"
        "2. Never print a hypothetical terminal transcript, a fake command prompt, or "
        "invented command output in chat text to make it look like you ran something. "
        "Every real environment command (npm create, npm install, npm run build, "
        "npm run dev, pytest, git, etc.) runs through the run_command tool — only its "
        "actual output counts as evidence something happened.\n"
        "3. Never claim a file was written, a command succeeded, or a build passed "
        "unless a tool call this turn actually did it — no guessing, no assuming.\n"
        "4. NEVER run anything that waits on a prompt (a setup wizard, `npm init` "
        "without `-y`): it hangs forever in a non-interactive sub-shell. A project on a "
        "framework with an official skeleton (Laravel, Symfony, Vite, Next.js, Astro, "
        "Django...) is created with scaffold_project and the framework's non-interactive "
        "generator, its target folder written as {dir} -- it works even when the folder "
        "already has files. A hand-written composer.json + `composer install` is NOT a "
        "Laravel project (no artisan, config/, routes/), and running a generator straight "
        "into a folder that has files fails. Only a plain stack with no skeleton (a static "
        "page, a small Node or Python script) gets a manifest you write yourself "
        "(package.json, requirements.txt), then the plain install command.\n\n"
        "Work in the shared computer folder. "
        "If that folder is empty or partially set up, do not loop search_files or "
        "web_fetch -- scaffold it (rule 4), then install and build. "
        "Call update_todo to break the task into steps, then implement. Once you've "
        "called update_todo for this task, do not call it again with the same list — "
        "repeating an identical tool call does not make more progress. "
        "Read existing files once, then write. "
        "Call list_roster (or list_members → get_member) and paste bios "
        "into source. Never call those tools from React. "
        "Summarize what you did, key paths, and what remains.\n\n"
        + DATA_NOT_ORDERS
        + "\n\n"
        + _project_context(project, registry)
    )
    kickoff = str(project.state.get("kickoff") or "").strip()
    extra = extra_context.strip()
    if kickoff and kickoff not in extra and kickoff not in goal:
        packed = "User brief:\n" + kickoff
        if extra:
            packed += "\n\nCat Crew notes:\n" + extra
        extra = packed
    # Unified mode (platform/shared_context.py): this run shares one message history with the
    # other cats, so the system message stays the pure persona (the mask) and Cat Crew's notes ride
    # in the assignment instead.
    from .shared_context import active_thread

    shared = active_thread(project.id)
    if extra and shared is None:
        system += "\n\nAdditional context from Cat Crew:\n" + extra
    if preload_note:
        system += "\n\nAssigned MCP tools are already loaded for this turn."
    from backend.services import mcp_service

    mcp_note = mcp_service.prompt_note(bot.id)  # what this cat has from the MCP servers bound to it (or why not)
    if mcp_note:
        system += "\n\n" + mcp_note
    role = (bot.role or "").strip().lower()
    from backend.services.workspace import current_folders, current_workspace, set_folders

    raw_folder = (project.folders or [None])[0]
    computer = Path(raw_folder) if raw_folder else current_workspace()
    saved_folders = list(current_folders())
    if project.folders:
        set_folders([Path(path) for path in project.folders if path])
    if role in {"developer", "designer", "architect", "qa", "devops", "vanguard", "prompt_engineer"}:
        from ..agent_context import skills_prompt_index

        index = skills_prompt_index(bot.id)
        if index:
            system += (
                "\n\n" + index
                + "\nWhen a skill matches this task's stack, read_skill it before building."
            )
        if role == "devops":
            system += (
                "\nYou own environment bootstrapping -- you run BEFORE Developer/Designer "
                "touch this project, and they are told to wait for you. Start with list_dir. "
                "If the framework's skeleton is already there (artisan for Laravel; "
                "package.json with src/ for Vite), do not scaffold again: install and verify. "
                "Otherwise:\n"
                "- A framework with an official skeleton: call scaffold_project with its "
                "generator, target folder as {dir}. Laravel: `composer create-project "
                "laravel/laravel {dir} --prefer-dist --no-interaction`. Vite + React: "
                "`npm create vite@latest {dir} -- --template react`, then `npm install`. "
                "Next.js: `npx create-next-app@latest {dir} --yes`. A composer.json or "
                "package.json another cat wrote earlier is not a scaffold; scaffold_project "
                "replaces it with the generator's.\n"
                "- Packages the brief needs: add them with the package manager (`composer "
                "require laravel/breeze`, `npm install bootstrap`), never by editing version "
                "numbers in a manifest by hand.\n"
                "- A plain stack with no skeleton (static site, small Python script): write "
                "package.json / requirements.txt yourself, then install.\n"
                "Finish only when the framework's own check passes (`php artisan --version`, "
                "`npm run build`) and report its output as proof. Developer and Designer are "
                "blocked on this; do not report done until it is genuinely true."
            )
        elif role == "developer":
            system += (
                "\nBefore writing any component: list_dir the shared computer folder and "
                "confirm the project's skeleton is on disk (artisan for Laravel, "
                "package.json for Node, requirements.txt for Python). If it is missing, "
                "create it yourself with scaffold_project (rule 4) and go on -- do not stop "
                "as blocked for it. "
                "Once it's confirmed, you implement the work yourself on the shared "
                "computer — the harness writes no product code. Write real files with "
                "write_file/replace_in_file. Finish only when the work is on disk and, if "
                "the brief expects a runnable or deployable app, it builds/runs — report "
                "the artifact paths or build output as proof."
            )
        elif role == "designer":
            system += (
                "\nBefore styling anything, confirm via list_dir that DevOps's manifest "
                "and Developer's base components already exist on disk — if the computer "
                "is still empty, say so and hand the task back rather than scaffolding it "
                "yourself. "
                "Write the design to disk (theme, styles, or a spec file). "
                "Chat notes are not a design."
            )
        elif role == "architect":
            system += (
                "\nWrite the architecture to disk as a document (ARCHITECTURE.md or docs/). "
                "A chat outline is not enough. Do not create manifests (composer.json, "
                "package.json) or source folders: DevOps creates the project with the "
                "framework's generator, and files made before it only get in its way."
            )
        elif role == "vanguard":
            system += (
                "\nYou run two different kinds of phase under one persona — read which "
                "one this task is before replying:\n"
                "- Architecture phase: write the architecture to disk as a document "
                "(ARCHITECTURE.md or docs/). A chat outline is not enough. Do not create "
                "manifests (composer.json, package.json) or source folders: DevOps creates "
                "the project with the framework's generator next.\n"
                "- Distribution/coordination phase: break the plan into a strict todo "
                "list by role (DevOps, Developer, Designer, QA) and call update_todo so "
                "it's tracked, not just described in chat.\n"
                "Either way, call report_task_results with your task_id when you're done "
                "so the ledger and PROJECT_STRUCTURE.md stay accurate."
            )
        elif role == "prompt_engineer":
            system += (
                "\nYour output feeds directly into Vanguard's architecture phase next — "
                "be concrete: goals, constraints, target stack, and what each role "
                "specifically needs to know. Do not design the system yourself and do "
                "not write code. If the blueprint is substantial, write it to disk "
                "(e.g. BLUEPRINT.md) so it's not lost to chat scroll."
            )
        elif role == "qa":
            system += (
                "\nA successful build is not a pass — plenty of real bugs (broken "
                "module loading, a blank page, a JS exception on load) only show up "
                "once something actually serves the page or runs the app in front of "
                "you. Do all of this yourself before writing a verdict; do not approve "
                "based on chat history, another bot's summary, or assumption alone — "
                "only your own tool results this turn count as evidence:\n"
                "1. list_dir / read_file the project folder: confirm package.json (or "
                "the equivalent manifest for this stack) and the entry point "
                "(index.html, main file, etc.) exist and look right.\n"
                "2. Run the project's real build via run_command (e.g. `npm run "
                "build`, or the stack's build/test command).\n"
                "3. If this is a website or web app: find or start how it's actually "
                "served (a configured domain/vhost, or `npm run dev`/`npm run "
                "preview` — use run_smoke_test for anything that stays running so it "
                "gets killed for you afterward), then call browser_check against that "
                "real served URL (never a file:// path — opening a build's index.html "
                "directly is exactly what produces the classic 'Failed to load module "
                "script ... MIME type of \"\"' error, so that check only means "
                "something over an actual server). Its console-error / bad-response "
                "report is mandatory evidence for any site or web app; a build that "
                "succeeds but a browser_check that shows console errors, uncaught "
                "exceptions, or bad-MIME script responses is still a FAIL.\n"
                "4. If this is a non-web app or service instead, run_smoke_test its "
                "real entry point to confirm it stays up rather than crashing on "
                "start.\n"
                "If required files are missing, the build fails, or step 3/4 turns up "
                "problems, your verdict is FAIL: say exactly what is missing or what "
                "the error was (quote the browser_check/run_smoke_test output), and "
                "say the task must go back to the DevOps or Developer specialist on "
                "this team (Forge / Pulse on the CatCrewAI roster) to fix before QA can "
                "pass it. Only write PASS when the files are confirmed on disk, the "
                "build actually succeeded, and (for a website/app) the live check "
                "came back clean in front of you.\n"
                "Start your reply with a single PASS or FAIL word, then the detail."
            )

    if shared is not None:
        task_text = goal + ("\n\nContext from Cat Crew:\n" + extra if extra else "")
        messages: list[dict[str, Any]] = shared.open_turn(system, bot.name, bot.role, task_text)
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": goal},
        ]
    try:
        # One line per specialist run: how big the prompt is before the first model call. This is
        # what tells us which phase bloats the context (see docs/PI_EVALUATION_AND_AGENT_PLAN.md).
        from backend.services.compaction import estimate_tokens

        print(
            f"[ctx] {bot.name} ({bot.role}) starts with system ~{estimate_tokens(messages[:1])} "
            f"+ task ~{estimate_tokens(messages[1:])} tokens, {len(schema)} tools"
            + (f", shared history of {len(messages) - 2} message(s)" if shared is not None else ""),
            flush=True,
        )
    except Exception:  # noqa: BLE001 - diagnostics must never affect a run
        pass
    emit_activity(bot.name, goal[:88])
    activity_board.set_activity(bot.id, f"Working: {goal[:70]}")
    activity_board.set_status_word(bot.id, "thinking")
    activity_board.set_state(bot.id, "working")
    depth_token = push_depth()
    bot_token = set_bot_id(bot.id)
    project_token = set_project_id(project.id)  # role models (Vision, Memory) resolve per project
    allow_token = set_tool_allowlist(allow)
    hook_token = set_approval_hook(gate_hook)

    def _on_tools(tool_names: list[str]) -> None:
        label = ", ".join(tool_names)[:88]
        emit_activity(bot.name, label)
        activity_board.set_activity(bot.id, label or "Working…")

    # Repeats are handled once, by the loop itself (``run_agent`` + ``harness/loop_guard``): an
    # identical call with nothing changed since is answered from memory there and never reaches
    # this executor. A second throttle here used to cut a volatile read (read_terminal on a dev
    # server, allowed three times by the loop) off after two.
    def _exec_and_log(name: str, arguments: dict[str, Any]) -> str:
        # Live sidebar caption BEFORE the (possibly slow) tool runs: the row
        # shows "Editing src/App.jsx" while it happens, not after. Writes go
        # through activity_board, which pushes them onto the bus.
        _live_title, _live_detail = phase_for(name, arguments)
        activity_board.set_status_word(bot.id, activity_board.status_word_for_tool(name))
        activity_board.set_activity(
            bot.id, f"{_live_title}: {_live_detail}"[:96] if _live_detail else _live_title
        )
        result = execute_tool(name, arguments)
        if collect_tool_events is not None:
            _title, detail = phase_for(name, arguments)
            line = f"{bot.name} executed {name}"
            if detail:
                line += f" on {detail}" if not detail.startswith(name) else f" ({detail})"
            collect_tool_events.append(line)
        _record_artifact(name, arguments, result)
        activity_board.set_status_word(bot.id, "thinking")  # the tool is done; the model is next
        activity_board.set_activity(bot.id, "Thinking…")
        return result

    def _record_artifact(name: str, arguments: dict[str, Any], result: str) -> None:
        # CrewAI-style "shared artifact tracking": a phase is only a real
        # handoff when its tools actually touched disk (see platform/sop.py's
        # verified flag) — so every successful write_file/replace_in_file/
        # run_command this bot makes is registered into the project's own
        # persisted ProjectState, not just the transient chat-log line above.
        from backend.harness.tool_result import tool_failed

        if tool_failed(name, result or ""):  # a failed build is not an artifact of this phase
            return
        entry = ""
        if name in ("write_file", "replace_in_file"):
            path = str((arguments or {}).get("path") or "").strip()
            if path:
                entry = f"{bot.name} wrote {path}"
                try:
                    from backend.db import ledger as _db

                    diff_content = str(
                        (arguments or {}).get("content")
                        or (arguments or {}).get("new_string")
                        or ""
                    )
                    _db.log_file_change(project.id, "", bot.id, path, diff_content)
                except Exception:
                    pass
        elif name == "run_command":
            command = str((arguments or {}).get("command") or "").strip()
            if command:
                entry = f"{bot.name} ran: {command[:160]}"
        if not entry:
            return
        artifacts = project.state.setdefault("created_artifacts", [])
        if entry not in artifacts:
            artifacts.append(entry)
            if len(artifacts) > 300:
                del artifacts[: len(artifacts) - 300]

    try:
        summary = provider.run_agent(
            messages,
            schema,
            _exec_and_log,
            max_rounds=rounds,
            stall_rounds=stall_rounds,
            abort=abort_fn,
            on_tools=_on_tools,
        )
    except Exception as exc:  # noqa: BLE001
        summary = f"{bot.name} failed: {exc}"
    finally:
        reset_approval_hook(hook_token)
        reset_tool_allowlist(allow_token)
        reset_bot_id(bot_token)
        reset_project_id(project_token)
        reset_depth(depth_token)
        restore_loaded_tools(saved_schema, saved_exec)
        if project.folders:
            set_folders(saved_folders)
        activity_board.mark_idle(bot.id)

    text = (summary or "").strip() or f"({bot.name} returned empty)"
    if len(text) > _SUMMARY_CAP:
        text = text[:_SUMMARY_CAP] + f"\n... [truncated, {len(summary)} chars]"
    if shared is not None:
        shared.close_turn(messages, bot.name, bot.role, goal, text)
    folder = computer
    if auto_build_bridge and role == "developer":
        bridge = _bridge_build(folder)
        if bridge:
            text += (
                "\n\nHarness bridge (ran the project's own build script on your "
                "behalf):\n" + bridge[:2000]
            )
    snap = computer_listing(folder)
    return f"[{bot.name} / {bot.role}]\n{text}\n\n{snap}"


def _bridge_build(folder: Path) -> str:
    """Terminal bridge: run the project's own build when artifacts are missing.

    Stack-agnostic — fires only for a package.json project that declares
    ``scripts.build`` and has no ``dist/`` or ``build/`` output yet. The harness
    never authors product code; it only executes the project's own command.
    """
    import json

    pkg = folder / "package.json"
    if not pkg.is_file():
        return ""
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return ""
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict) or not str(scripts.get("build") or "").strip():
        return ""
    for out in ("dist", "build"):
        if (folder / out).is_dir():
            return ""
    from ..tools import run_command

    return run_command("npm run build", cwd=str(folder))


def computer_listing(folder: Path, *, limit: int = 40) -> str:
    """Ground-truth top-level listing so Cat Crew sees what is actually on disk."""
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return f"Computer after this turn: {folder} (unreadable)"
    if not entries:
        return f"Computer after this turn: {folder} — EMPTY. Nothing is on disk yet."
    names = [entry.name + ("/" if entry.is_dir() else "") for entry in entries[:limit]]
    more = f" (+{len(entries) - limit} more)" if len(entries) > limit else ""
    return f"Computer after this turn: {folder}\n  " + ", ".join(names) + more
