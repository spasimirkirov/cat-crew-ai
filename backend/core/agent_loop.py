"""Headless agent turn runners (threads + callbacks, no GUI toolkit).

``TurnRunner`` is the 1:1 chat loop: build the system prompt, memory, clarify
step and checklist, then drive ``provider.run_agent`` with tool dispatch.
``RoomRunner`` drives the Cat Crew's deterministic SOP pipeline
(``platform/sop.py``), which calls real specialist bots one by one.

Both were the ``ChatWorker`` / ``ProjectRoomWorker`` QThreads of the desktop
app. The behaviour is the same; Qt signals became a ``TurnListener`` whose
methods are invoked on the worker thread (the chat service turns them into
event-bus events, which are thread-safe).

Result delivery: ``on_finished`` / ``on_failed`` fire on the worker thread
*after* the turn's ambient state (context vars, tool allow-list, the bot's
"working" flag) has been torn down, so a listener may start the next queued
turn from inside the callback without racing that cleanup.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any, Callable

from backend.activity import phase_for
from backend.agent_context import looks_like_web_task, read_skill
from backend.platform.orchestrator import ROOM_ID, is_room_id
from backend.providers import BaseProvider, Message
from backend.services.clarify import clarify_task, should_clarify
from backend.services.here import should_attach_forecast
from backend.services.memory import Mem0Memory, build_memory_context
from backend.services.settings_store import agent_settings
from backend.services.terminal import (
    CommandScope,
    abort_running_commands,
    bind_command_scope,
    reset_abort,
    reset_command_scope,
)
from backend.services.todo import prompt_appendix
from backend.services.trace import bind_session, log_event, write_markdown
from backend.tools import (
    TOOLS_SCHEMA,
    build_system_prompt,
    execute_tool,
    live_forecast,
    reset_tool_allowlist,
    set_tool_allowlist,
)


class TurnListener:
    """Callbacks a runner reports through. Every method is optional."""

    def on_phase(self, title: str, detail: str = "") -> None: ...

    def on_tool_used(self, message: str) -> None: ...

    def on_tool_preview(self, name: str, args_preview: str, result_preview: str) -> None: ...

    def on_steered(self, text: str) -> None: ...

    def request_approval(self, name: str, detail: str, abort: threading.Event) -> str:
        """Block until the user decides: ``allow`` / ``allow_always`` / ``deny``."""
        return "allow"

    def request_ask(self, prompt: str, choices: list[str], abort: threading.Event) -> str:
        """Block until the user answers; ``""`` means no answer."""
        return ""

    def on_finished(self, reply: str) -> None: ...

    def on_failed(self, error: str) -> None: ...

    # Room-only
    def on_bot_turn(self, bot_id: str, text: str, tool_events: list[str], phase: str) -> None: ...

    def on_phase_started(self, phase: str, bot_id: str) -> None: ...


_DETAIL_CAP = 4_000
_DETAIL_FIRST = ("command", "path", "cwd", "url", "query", "message")


def approval_detail(arguments: dict | None) -> str:
    """What the person sees before approving a call: every argument, the decisive ones first.

    A one-line, 120-character preview hid the tail of a long command (``… && curl x | sh``) and
    the whole body of a write; the approval card scrolls, so show it all up to a generous cap."""
    args = dict(arguments or {})
    order = [k for k in _DETAIL_FIRST if k in args] + [k for k in args if k not in _DETAIL_FIRST]
    lines: list[str] = []
    for key in order:
        value = args[key]
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                value = str(value)
        lines.append(f"{key}:\n{value}" if "\n" in value or len(value) > 80 else f"{key}: {value}")
    text = "\n".join(lines) or "(no arguments)"
    if len(text) > _DETAIL_CAP:
        text = text[:_DETAIL_CAP] + f"\n… [{len(text) - _DETAIL_CAP} more characters]"
    return text


# Tools whose "always allow" covers one program, not the whole shell: allowing ``npm run build``
# for the rest of the turn must not also allow ``curl … | sh``.
def _shell_tools() -> frozenset[str]:
    from backend.harness.tool_meta import flagged

    return flagged("shell")


_SHELL_TOOLS = _shell_tools()


def always_allow_key(name: str, arguments: dict | None) -> str:
    """What an "always allow" answer remembers: the tool, plus the program for shell tools."""
    if name not in _SHELL_TOOLS:
        return name
    command = str((arguments or {}).get("command") or "").strip()
    if not command or any(token in command for token in ("&&", "||", ";", "|", "`", "$(")):
        # A chained or piped command is several programs: never remember it, ask every time.
        return f"{name}:{command}"
    program = command.split()[0].strip("\"'").lower()
    for suffix in (".exe", ".cmd", ".bat"):
        program = program.removesuffix(suffix)
    return f"{name}:{program}"


def _jev_says_clear(text: str) -> bool:
    """Jev (when on) can spare the clarify rewrite of a request that is already clear."""
    try:
        from backend.middleware.jev_gatekeeper import request_is_clear

        return request_is_clear(text)
    except Exception:  # noqa: BLE001 - a gate must never fail the turn
        return False


class _Steerable:
    """Shared abort / steer / provider-swap plumbing for both runners."""

    def __init__(self, provider: BaseProvider, listener: TurnListener) -> None:
        self.provider = provider
        self.listener = listener
        self._abort = threading.Event()
        self._steer: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._pending_provider: BaseProvider | None = None
        self._thread: threading.Thread | None = None
        # Tools the person answered "always allow" for, for the rest of this turn.
        self._always_allow: set[str] = set()
        # This turn's own foreground commands: Stop ends these, not another project's.
        self._commands = CommandScope()
        #: The project's computer for this turn, bound on the turn's own thread (``_bind_turn``);
        #: ``None`` keeps whatever the process has.
        self.folders: list[str] | None = None
        #: A regular user's turn in remote mode: file tools stay inside ``folders``, and commands
        #: run in the user's bubblewrap sandbox (``services/sandbox.py``).
        self.confined = False
        self.sandbox: Any = None

    def _bind_turn(self) -> list[tuple[Callable[[Any], None], Any]]:
        """Bind the per-turn state on this thread; returns what ``_unbind_turn`` resets. Turns of
        different projects run side by side, so nothing here may be process-wide."""
        from backend.services.workspace import bind_confined, bind_folders, reset_confined, reset_folders

        tokens: list[tuple[Callable[[Any], None], Any]] = [(reset_command_scope, bind_command_scope(self._commands))]
        if self.folders is not None:
            tokens.append((reset_folders, bind_folders(list(self.folders))))
        tokens.append((reset_confined, bind_confined(self.confined)))
        from backend.services import sandbox

        tokens.append((sandbox.reset, sandbox.bind(self.sandbox)))
        return tokens

    @staticmethod
    def _unbind_turn(tokens: list[tuple[Callable[[Any], None], Any]]) -> None:
        for reset, token in reversed(tokens):
            try:
                reset(token)
            except (ValueError, LookupError):
                pass

    @property
    def aborted(self) -> bool:
        return self._abort.is_set()


    def start(self, name: str = "catcrew-turn") -> None:
        self._thread = threading.Thread(target=self.run, name=name, daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def steer(self, text: str) -> None:
        body = (text or "").strip()
        if body:
            self._steer.put(body)

    def _take_steer(self) -> str | None:
        chunks: list[str] = []
        while True:
            try:
                chunks.append(self._steer.get_nowait())
            except queue.Empty:
                break
        if not chunks:
            return None
        text = "\n".join(chunks)
        self._safe(self.listener.on_steered, text)
        return text

    def set_provider(self, provider: BaseProvider) -> None:
        with self._lock:
            self._pending_provider = provider

    def _approval_hook(self, name: str, arguments: dict) -> str:
        key = always_allow_key(name, arguments)
        if key in self._always_allow:
            return "allow"
        detail = approval_detail(arguments)
        try:
            decision = (
                str(self.listener.request_approval(name, detail, self._abort) or "deny")
                .strip()
                .lower()
            )
        except Exception:  # noqa: BLE001
            decision = "deny"
        if self._abort.is_set() and decision not in ("allow", "allow_always"):
            return "deny"
        if decision == "allow_always":
            self._always_allow.add(key)
            return "allow"
        return decision or "deny"

    def request_abort(self) -> None:
        raise NotImplementedError

    def run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    @staticmethod
    def _safe(fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a listener bug must never kill the turn
            pass


class TurnRunner(_Steerable):
    """One 1:1 agent turn (a cat's own thread)."""

    def __init__(
        self,
        provider: BaseProvider,
        history: list[Message],
        listener: TurnListener,
        *,
        memory: Mem0Memory | None = None,
        top_k: int = 5,
        tools_schema: list | None = None,
        prompt_mode: str = "developer",
        model_note: str = "",
        session_id: str = "",
        workspace: str = "",
        project_id: str = "",
        bot_id: str = "",
    ) -> None:
        super().__init__(provider, listener)
        self.history = history
        self.memory = memory
        self.top_k = top_k
        self.tools_schema = tools_schema if tools_schema is not None else TOOLS_SCHEMA
        self.prompt_mode = prompt_mode
        self.model_note = model_note or ""
        self.session_id = session_id or ""
        self.workspace = workspace or ""
        self.project_id = project_id or ""
        self.bot_id = bot_id or ""
        self._result: tuple[str, str] = ("error", "turn did not run")

    # -- abort / provider ------------------------------------------------------------
    def request_abort(self) -> None:
        self._abort.set()
        abort_running_commands(self._commands)
        with self._lock:
            self.provider.close_stream()
            if self._pending_provider is not None:
                self._pending_provider.close_stream()

    def _swap_provider(self) -> BaseProvider | None:
        with self._lock:
            if self._pending_provider is None:
                return None
            nxt = self._pending_provider
            self._pending_provider = None
            self.provider = nxt
            return nxt

    # -- hooks handed to the harness ----------------------------------------------------
    def _ask_user_hook(self, prompt: str, choices: list[str]) -> str:
        try:
            return str(self.listener.request_ask(prompt, list(choices or []), self._abort) or "")
        except Exception:  # noqa: BLE001
            return ""

    # -- tool dispatch ------------------------------------------------------------------------
    def _on_tools(self, names: list[str]) -> None:
        from backend.platform import activity_board

        for name in names:
            title, detail = phase_for(name)
            self._safe(self.listener.on_phase, title, detail)
            self._safe(self.listener.on_tool_used, f"Tool: {name}")
        label = ", ".join(names)[:88] or "Working…"
        activity_board.set_activity(self.bot_id or ROOM_ID, label)

    def _run_tool(self, name: str, arguments: dict) -> str:
        from backend.harness.deferred import extra_tool_schemas
        from backend.platform import activity_board

        allowed = {item.get("function", {}).get("name") for item in (self.tools_schema or [])}
        allowed.update(item.get("function", {}).get("name") for item in extra_tool_schemas())
        if name not in allowed and not str(name).startswith("connector__"):
            return f"Tool '{name}' is not available in {self.prompt_mode} mode."
        if name not in allowed and str(name).startswith("connector__"):
            # Deferred: must have been load_connector_tools'd this turn.
            if name not in {item.get("function", {}).get("name") for item in extra_tool_schemas()}:
                return f"Tool '{name}' is not loaded. Call load_connector_tools first this turn."

        title, detail = phase_for(name, arguments)
        self._safe(self.listener.on_phase, title, detail)
        activity_board.set_working(
            self.bot_id or ROOM_ID,
            activity_board.status_word_for_tool(name),
            (f"{title} — {detail}" if detail else title)[:88],
        )
        from backend.services.trace import redact

        try:
            shown = json.dumps(redact(arguments or {}), ensure_ascii=False)
        except (TypeError, ValueError):
            shown = "…"
        print(f"[tool] {name}({shown[:300]})", flush=True)
        result = execute_tool(name, arguments)
        # Delegated sub-agent turns can be long, and a QA verdict banner must
        # survive the preview truncation intact.
        cap = 900 if name == "delegate_to_bot" else 160
        preview = result.replace("\n", " ")[:cap]
        print(f"[tool] -> {redact(preview[:160])}", flush=True)
        # The trace row for this call is written by the loop (harness/events.emit_tool_result).
        try:
            args_preview = json.dumps(arguments or {}, ensure_ascii=False)[:400]
        except Exception:  # noqa: BLE001
            args_preview = str(arguments or "")[:400]
        self._safe(self.listener.on_tool_preview, name, args_preview, preview)
        # The tool is done: until the next one starts, the model is thinking. Without this the live
        # status kept showing the last tool for as long as the next model call took.
        activity_board.set_working(self.bot_id or ROOM_ID, "thinking", "Thinking…")
        return result

    # -- helpers ----------------------------------------------------------------------------------
    def _last_user_text(self) -> str:
        for msg in reversed(self.history):
            if msg.get("role") == "user":
                return str(msg.get("content", ""))
        return ""

    def _remember(self, user_text: str, reply: str) -> None:
        if self.memory is None:
            return
        try:
            self.memory.add_memory(user_text, reply)
        except Exception:  # noqa: BLE001
            pass

    def _prepare_bot_turn(self) -> None:
        from backend.harness.deferred import clear_loaded_tools

        clear_loaded_tools()
        bid = str(self.bot_id or "").strip()
        if is_room_id(bid):  # Cat Crew: no per-agent MCP / tool assignments to load
            return
        from backend.platform.chief import schema_for_direct_bot
        from backend.platform.registry import get_bot, load_registry
        from backend.platform.toolset import preload_bot_mcp

        bot = get_bot(bid)
        registry = load_registry()
        if bot is None:
            return
        preload_bot_mcp(
            bot.mcp_tools,
            global_mcp=registry.global_mcp_tools,
            inherit_global=bot.inherit_global_mcp,
        )
        self.tools_schema = schema_for_direct_bot(bid)

    # -- thread body --------------------------------------------------------------------------------
    def run(self) -> None:
        from backend.harness.approvals import reset_approval_hook, set_approval_hook
        from backend.harness.ask_user import reset_ask_user_hook, set_ask_user_hook
        from backend.harness.runtime import (
            reset_abort as reset_rt_abort,
            reset_provider,
            set_abort as set_rt_abort,
            set_provider,
        )
        from backend.platform import activity_board
        from backend.platform.runtime import (
            reset_bot_id,
            reset_project_id,
            set_bot_id,
            set_project_id,
        )

        who = self.bot_id or ROOM_ID
        turn_tokens = self._bind_turn()
        try:
            self._prepare_bot_turn()
        except Exception as exc:  # noqa: BLE001
            self._unbind_turn(turn_tokens)
            self._result = ("error", str(exc))
            self._deliver()
            return
        activity_board.set_working(who, "thinking", "Thinking…")
        allowed = {item.get("function", {}).get("name") for item in (self.tools_schema or [])}
        token = set_tool_allowlist(frozenset(str(n) for n in allowed if n))
        hook_token = set_approval_hook(self._approval_hook)
        ask_token = set_ask_user_hook(self._ask_user_hook)
        prov_token = set_provider(self.provider)
        abort_token = set_rt_abort(self._abort.is_set)
        proj_token = set_project_id(self.project_id)
        bot_token = set_bot_id(self.bot_id)
        try:
            self._run_turn()
        finally:
            activity_board.mark_idle(who)
            reset_bot_id(bot_token)
            reset_project_id(proj_token)
            reset_rt_abort(abort_token)
            reset_provider(prov_token)
            reset_ask_user_hook(ask_token)
            reset_approval_hook(hook_token)
            reset_tool_allowlist(token)
            try:
                from backend.services.comfyui import maybe_stop_comfy

                maybe_stop_comfy()
            except Exception:  # noqa: BLE001
                pass
            self._unbind_turn(turn_tokens)
        self._deliver()

    def _deliver(self) -> None:
        kind, payload = self._result
        if kind == "ok":
            self._safe(self.listener.on_finished, payload)
        else:
            self._safe(self.listener.on_failed, payload)

    def _run_turn(self) -> None:
        listener = self.listener
        reset_abort()
        bind_session(self.session_id)
        try:
            user_text = self._last_user_text()
            log_event(
                "turn_start",
                user=user_text,
                model=self.model_note,
                workspace=self.workspace,
                mode=self.prompt_mode,
            )
            system_content = build_system_prompt(
                self.prompt_mode, project_id=self.project_id, bot_id=self.bot_id
            )
            if self.model_note:
                system_content += "\n\n" + self.model_note
            from backend.services import mcp_service

            mcp_note = mcp_service.prompt_note(self.bot_id) if self.bot_id else ""
            if mcp_note:  # 1:1 chat with a cat: the same MCP tools (and the same note) as in a work run
                system_content += "\n\n" + mcp_note
            # Everything below changes from message to message, so it goes into the turn note on the
            # newest user message, never into the system prompt (harness/turn_note.py: a stable
            # system prompt is what lets the host's prompt cache carry the conversation).
            from backend.harness.turn_note import attach_turn_note
            from backend.platform.chief import turn_context

            note: list[str] = [turn_context(self.project_id)]
            if should_attach_forecast(user_text, self.history):
                self._safe(listener.on_phase, "Searching the web", "weather")
                snapshot = live_forecast(user_text)
                if snapshot:
                    note.append(
                        "Forecast already fetched for this turn:\n"
                        f"{snapshot}\n"
                        "Answer with these numbers now. Do not list websites. "
                        "Do not say you will search — the search is done. "
                        "Do not ask whether to search."
                    )
            if self.memory is not None:
                memories = self.memory.get_relevant_memories(user_text, limit=self.top_k)
                if memories:
                    note.append(build_memory_context(memories))
            spec = ""
            if should_clarify(user_text, mode=self.prompt_mode) and not _jev_says_clear(user_text):
                self._safe(listener.on_phase, "Clarifying", "")
                spec = clarify_task(
                    self.provider, user_text, self.history, abort=self._abort.is_set
                )
                if self._abort.is_set():
                    log_event("turn_end", reply="[stopped]")
                    if self.session_id:
                        write_markdown(self.session_id)
                    self._result = ("ok", "[stopped]")
                    return
            if spec:
                note.append(
                    "Clarified task for this turn (the user typed loosely; "
                    "follow this spec, not the typos):\n" + spec
                )
            checklist = prompt_appendix(
                mode=self.prompt_mode,
                spec=spec,
                always_surface=(self.prompt_mode == "developer"),
            )
            if checklist:
                note.append(checklist.strip())
            if self.prompt_mode == "developer" and looks_like_web_task(user_text, spec):
                for skill_name in ("static-web", "coding-companion"):
                    skill = read_skill(skill_name)
                    if skill and not skill.startswith("Unknown skill"):
                        note.append(skill)
            messages: list[Message] = attach_turn_note(
                [{"role": "system", "content": system_content}, *self.history],
                "\n\n".join(part for part in note if part),
            )
            if spec:
                self._safe(listener.on_phase, "Working from", spec.replace("\n", " ")[:88])
            else:
                self._safe(listener.on_phase, "Thinking", "")
            rounds = int(agent_settings()["max_rounds"] or 0)
            reply = self.provider.run_agent(
                messages,
                self.tools_schema,
                self._run_tool,
                max_rounds=rounds,
                on_text=None,
                abort=self._abort.is_set,
                take_steer=self._take_steer,
                on_tools=self._on_tools,
                swap_provider=self._swap_provider,
                on_compressed=lambda before, after, model: self._safe(
                    listener.on_phase, "Compressed context", f"~{before} → ~{after} tokens via {model}"
                ),
            )
            self._remember(user_text, reply)
            log_event("turn_end", reply=reply or "")
            if self.session_id:
                write_markdown(self.session_id)
            self._result = ("ok", reply or "")
        except Exception as exc:  # noqa: BLE001
            log_event("turn_end", error=str(exc))
            if self.session_id:
                write_markdown(self.session_id)
            self._result = ("error", str(exc))
        finally:
            bind_session("")


class RoomRunner(_Steerable):
    """The Cat Crew work turn: ``platform/sop.py`` runs real specialist bots.

    Drives no LLM loop of its own: it hands the message to the SOP engine,
    which delegates each phase to a specialist and reports their real output.
    Steering text and a provider swap take effect at the next phase boundary
    (each phase is a whole separate delegated bot run)."""

    def __init__(
        self,
        provider: BaseProvider,
        project_id: str,
        message: str,
        listener: TurnListener,
        *,
        session_id: str = "",
        roles: tuple[str, ...] | None = None,
        bug: bool = False,
        reporter: str = "",
    ) -> None:
        super().__init__(provider, listener)
        self.project_id = project_id
        self.message = message
        self.roles = tuple(roles) if roles else None
        self.session_id = session_id or ""
        self.bug = bool(bug)
        self.reporter = reporter or ""
        self._result: tuple[str, str] = ("error", "turn did not run")

    def request_abort(self) -> None:
        self._abort.set()
        abort_running_commands(self._commands)
        # The active specialist streams through this provider (delegation captures it), so closing
        # the stream ends its model call now instead of after the whole reply has been generated.
        with self._lock:
            self.provider.close_stream()

    def _should_stop(self) -> bool:
        """Stop between phases when the person pressed Stop, or when a model the run needs was
        turned away (rate limit, no credit): every later phase would hit the same wall."""
        from backend.harness import model_block

        return self._abort.is_set() or model_block.pending(self.project_id) is not None

    def _apply_pending_provider(self) -> None:
        with self._lock:
            nxt = self._pending_provider
            self._pending_provider = None
        if nxt is None:
            return
        self.provider = nxt
        from backend.harness.runtime import set_provider as set_rt_provider

        set_rt_provider(nxt)

    def run(self) -> None:
        from backend.harness.runtime import (
            reset_abort as reset_rt_abort,
            reset_provider,
            set_abort as set_rt_abort,
            set_provider,
        )
        from backend.platform.registry import load_registry
        from backend.platform.sop import run_project_room_turn

        from backend.platform.shared_context import begin_run, end_run

        from backend.harness.approvals import reset_approval_hook, set_approval_hook

        turn_tokens = self._bind_turn()
        prov_token = set_provider(self.provider)
        abort_token = set_rt_abort(self._abort.is_set)
        # The specialists of a work run go through the same approval gate as a 1:1 chat
        # (platform/delegation carries the hook over to the engine thread).
        hook_token = set_approval_hook(self._approval_hook)
        project_token = None
        bind_session(self.session_id)
        try:
            registry = load_registry()
            project = registry.projects.get(self.project_id)
            if project is None:
                raise LookupError(f"Unknown project {self.project_id!r}.")
            # One shared message history for this whole run when unified mode is on (else None).
            begin_run(project.id)
            # Role models (Vision, Memory, Images) resolve per project from here on.
            from backend.platform.runtime import set_project_id

            project_token = set_project_id(project.id)

            def _on_turn(turn) -> None:
                self._safe(
                    self.listener.on_bot_turn,
                    turn.bot.id,
                    turn.text,
                    list(turn.tool_events),
                    str(turn.phase or ""),
                )

            def _on_phase_start(phase: str, bot) -> None:
                self._apply_pending_provider()
                self._safe(self.listener.on_phase_started, str(phase or ""), bot.id)

            if self.bug:
                from backend.platform.bugflow import run_bug_turn

                run_bug_turn(
                    self.message,
                    project=project,
                    registry=registry,
                    reporter_id=self.reporter,
                    on_turn=_on_turn,
                    on_phase_start=_on_phase_start,
                    should_stop=self._should_stop,
                    take_steer=self._take_steer,
                )
            else:
                run_project_room_turn(
                    self.message,
                    project=project,
                    registry=registry,
                    on_turn=_on_turn,
                    on_phase_start=_on_phase_start,
                    should_stop=self._should_stop,
                    take_steer=self._take_steer,
                    roles=self.roles,
                )
            self._result = ("ok", "")
        except Exception as exc:  # noqa: BLE001
            self._result = ("error", str(exc))
        finally:
            end_run(self.project_id)
            if project_token is not None:
                from backend.platform.runtime import reset_project_id

                reset_project_id(project_token)
            bind_session("")
            reset_approval_hook(hook_token)
            reset_rt_abort(abort_token)
            reset_provider(prov_token)
            self._unbind_turn(turn_tokens)
        self._deliver()

    def _deliver(self) -> None:
        kind, payload = self._result
        if kind == "ok":
            self._safe(self.listener.on_finished, payload)
        else:
            self._safe(self.listener.on_failed, payload)
