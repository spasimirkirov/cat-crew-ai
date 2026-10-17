"""Main window: chat and settings."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import requests
from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtMultimedia import QMediaDevices
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .config import (
    AVATAR_ENABLED,
    CHARACTER_SIZE_PRESETS,
    CHAT_WINDOW_SIZE,
    FRAMELESS,
    MEMORY_ENABLED,
    MEMORY_TOP_K,
    LLAMA_SERVER_URL,
    STT_ENABLED,
    TRANSLUCENT,
    TTS_ENABLED,
    TTS_VOICE_EN,
    WINDOW_TITLE,
)
from .activity import next_reveal, phase_for, visible_chat_reply
from .agent_context import expand_slash_command, looks_like_web_task, read_skill
from .chat_transcript import ChatTranscript, parse_when
from .composer import Composer
from .clarify import clarify_task, should_clarify
from .compaction import compact_messages, estimate_chars
from .db_panel import DatabasePanel
from .editor_pane import EditorPane
from .fa import IconToggle, icon_button
from .file_explorer import FileExplorerPanel
from .git_panel import GitPanel
from .heartbeat import Heartbeat
from .here import infer_home_city, should_attach_forecast
from .icon import create_activity_icon, create_jinx_icon
from .api_access import is_cloud_host, make_cloud_provider
from .api_access_page import ApiAccessPage
from .connectors_page import ConnectorsPage
from .skills_page import SkillsPage
from .memory import Mem0Memory, build_memory_context
from .lm_panel import LmPanel
from .download_flyout import DownloadFlyout
from .lm_runtime import format_capabilities, host_label, loaded_model_note
from .platform_page import PlatformPage
from .home_page import HomePage
from .members_page import MembersPage
from .providers import (
    BaseProvider,
    LocalLlamaProvider,
    Message,
)
from .providers.openai_compat import friendly_provider_error
from .sessions import (
    delete_session,
    ensure_active_session,
    fork_session,
    list_sessions,
    new_session,
    open_project_thread,
    save_session,
    set_active_session,
)
from .trace import bind_session, export_copy, log_event, write_markdown
from .settings_store import (
    agent_settings,
    audio_settings,
    brains_settings,
    character_window,
    chat_pane_for,
    enabled_hosts,
    load_settings,
    locale_settings,
    look_settings,
    life_settings,
    resolve_chat_brain,
    resolve_role_brain,
    save_agent_settings,
    save_audio_settings,
    save_brains_settings,
    save_life_settings,
    save_character_window,
    save_locale_settings,
    save_look_settings,
    save_settings,
)
from .session_tree import (
    append_turn,
    ensure_tree,
    provider_history,
    set_head,
    tree_outline,
    visible_history,
)
from .stt import SpeechToText
from .todo import prompt_appendix
from .tools import (
    TOOLS_SCHEMA,
    build_system_prompt,
    execute_tool,
    live_forecast,
    reset_tool_allowlist,
    set_tool_allowlist,
    tools_schema_for,
)
from .terminal import TerminalPane, abort_running_commands, reset_abort
from .titlebar import TitleBar
from .ui_style import APP_QSS
from .voice import JinxVoice, spoken_vs_chat
from .widgets import FlowLayout, PageHeader, SwitchRow
from .workspace import (
    add_folder,
    current_folders,
    current_workspace,
    folder_picker_start,
    remove_folder,
    set_folders,
)


class ChatWorker(QThread):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)
    tool_used = pyqtSignal(str)
    tool_preview = pyqtSignal(str, str, str)  # name, args, result
    token = pyqtSignal(str)
    compacted = pyqtSignal(int)
    tools_starting = pyqtSignal()
    steered = pyqtSignal(str)
    phase = pyqtSignal(str, str)
    approval_needed = pyqtSignal(str, str)  # tool name, detail
    ask_user_needed = pyqtSignal(str, list)  # prompt, choices

    def __init__(
        self,
        provider: BaseProvider,
        history: list[Message],
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
        super().__init__()
        self.provider = provider
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
        self._abort = threading.Event()
        self._steer: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._pending_provider: BaseProvider | None = None
        self._approval_event = threading.Event()
        self._approval_decision = "deny"
        self._always_allow: set[str] = set()
        self._ask_event = threading.Event()
        self._ask_answer = ""

    def resolve_approval(self, decision: str) -> None:
        self._approval_decision = (decision or "deny").strip().lower() or "deny"
        self._approval_event.set()

    def _approval_hook(self, name: str, arguments: dict) -> str:
        if name in self._always_allow:
            return "allow"
        detail = ""
        for key in ("command", "path", "query", "url", "message"):
            value = str((arguments or {}).get(key) or "").strip()
            if value:
                detail = value.replace("\n", " ")[:120]
                break
        if not detail:
            detail = str(arguments or "")[:120]
        self._approval_decision = "deny"
        self._approval_event.clear()
        self.approval_needed.emit(name, detail)
        # Wait until UI decides or the turn is aborted.
        while not self._abort.is_set():
            if self._approval_event.wait(timeout=0.25):
                break
        if self._abort.is_set() and not self._approval_event.is_set():
            return "deny"
        decision = self._approval_decision
        if decision == "allow_always":
            self._always_allow.add(name)
            return "allow"
        return decision

    def resolve_ask_user(self, answer: str) -> None:
        self._ask_answer = answer or ""
        self._ask_event.set()

    def _ask_user_hook(self, prompt: str, choices: list[str]) -> str:
        self._ask_answer = ""
        self._ask_event.clear()
        self.ask_user_needed.emit(prompt, list(choices or []))
        while not self._abort.is_set():
            if self._ask_event.wait(timeout=0.25):
                break
        if self._abort.is_set() and not self._ask_event.is_set():
            return ""
        return self._ask_answer

    def request_abort(self) -> None:
        self._abort.set()
        self._approval_event.set()
        self._ask_event.set()
        abort_running_commands()
        with self._lock:
            self.provider.close_stream()
            if self._pending_provider is not None:
                self._pending_provider.close_stream()

    def set_provider(self, provider: BaseProvider) -> None:
        with self._lock:
            self._pending_provider = provider

    def _swap_provider(self) -> BaseProvider | None:
        with self._lock:
            if self._pending_provider is None:
                return None
            nxt = self._pending_provider
            self._pending_provider = None
            self.provider = nxt
            return nxt

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
        self.steered.emit(text)
        return text

    def _on_tools(self, names: list[str]) -> None:
        self.tools_starting.emit()
        for name in names:
            title, detail = phase_for(name)
            self.phase.emit(title, detail)
            self.tool_used.emit(f"Tool: {name}")

    def _run_tool(self, name: str, arguments: dict) -> str:
        from .harness.deferred import extra_tool_schemas

        allowed = {
            item.get("function", {}).get("name") for item in (self.tools_schema or [])
        }
        allowed.update(
            item.get("function", {}).get("name") for item in extra_tool_schemas()
        )
        if name not in allowed and not str(name).startswith("connector__"):
            return f"Tool '{name}' is not available in {self.prompt_mode} mode."
        if name not in allowed and str(name).startswith("connector__"):
            # Deferred: must have been load_connector_tools'd this turn.
            if name not in {
                item.get("function", {}).get("name") for item in extra_tool_schemas()
            }:
                return (
                    f"Tool '{name}' is not loaded. "
                    "Call load_connector_tools first this turn."
                )
        title, detail = phase_for(name, arguments)
        self.phase.emit(title, detail)
        print(f"[tool] {name}({arguments})", flush=True)
        result = execute_tool(name, arguments)
        preview = result.replace("\n", " ")[:160]
        print(f"[tool] -> {preview}", flush=True)
        log_event("tool", name=name, arguments=arguments, result=preview)
        args_preview = ""
        try:
            import json as _json

            args_preview = _json.dumps(arguments or {}, ensure_ascii=False)[:400]
        except Exception:  # noqa: BLE001
            args_preview = str(arguments or "")[:400]
        self.tool_preview.emit(name, args_preview, preview)
        return result

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
        from .harness.deferred import clear_loaded_tools

        clear_loaded_tools()
        bid = str(self.bot_id or "").strip()
        if not bid or bid.lower() == "chief":
            return
        from .platform.chief import schema_for_direct_bot
        from .platform.registry import get_bot, load_registry
        from .platform.toolset import preload_bot_mcp

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

    def run(self) -> None:
        self._prepare_bot_turn()
        allowed = {
            item.get("function", {}).get("name") for item in (self.tools_schema or [])
        }
        token = set_tool_allowlist(frozenset(str(n) for n in allowed if n))
        from .harness.approvals import reset_approval_hook, set_approval_hook
        from .harness.ask_user import reset_ask_user_hook, set_ask_user_hook
        from .harness.runtime import (
            reset_abort as reset_rt_abort,
            reset_provider,
            set_abort as set_rt_abort,
            set_provider,
        )

        hook_token = set_approval_hook(self._approval_hook)
        ask_token = set_ask_user_hook(self._ask_user_hook)
        prov_token = set_provider(self.provider)
        abort_token = set_rt_abort(self._abort.is_set)
        from .platform.runtime import (
            reset_bot_id,
            reset_project_id,
            set_bot_id,
            set_project_id,
        )

        proj_token = set_project_id(self.project_id)
        bot_token = set_bot_id(self.bot_id)
        try:
            self._run_turn()
        finally:
            reset_bot_id(bot_token)
            reset_project_id(proj_token)
            reset_rt_abort(abort_token)
            reset_provider(prov_token)
            reset_ask_user_hook(ask_token)
            reset_approval_hook(hook_token)
            reset_tool_allowlist(token)
            try:
                from .comfyui import maybe_stop_comfy

                maybe_stop_comfy()
            except Exception:  # noqa: BLE001
                pass

    def _run_turn(self) -> None:
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
            if getattr(self.provider, "display_name", "") == "Local":
                from .llama_server import ping_server

                if not ping_server():
                    self.phase.emit("Starting llama-server", "")
            system_content = build_system_prompt(
                self.prompt_mode,
                project_id=self.project_id,
                bot_id=self.bot_id,
            )
            if self.model_note:
                system_content += "\n\n" + self.model_note
            if should_attach_forecast(user_text, self.history):
                self.phase.emit("Searching the web", "weather")
                snapshot = live_forecast(user_text)
                if snapshot:
                    system_content += (
                        "\n\nForecast already fetched for this turn:\n"
                        f"{snapshot}\n"
                        "Answer with these numbers now. Do not list websites. "
                        "Do not say you will search — the search is done. "
                        "Do not ask whether to search."
                    )
            if self.memory is not None:
                memories = self.memory.get_relevant_memories(user_text, limit=self.top_k)
                if memories:
                    system_content += "\n\n" + build_memory_context(memories)
            spec = ""
            if should_clarify(user_text, mode=self.prompt_mode):
                self.phase.emit("Clarifying", "")
                spec = clarify_task(
                    self.provider,
                    user_text,
                    self.history,
                    abort=self._abort.is_set,
                )
                if self._abort.is_set():
                    log_event("turn_end", reply="[stopped]")
                    if self.session_id:
                        write_markdown(self.session_id)
                    self.finished.emit("[stopped]")
                    return
            if spec:
                system_content += (
                    "\n\nClarified task for this turn (the user typed loosely; "
                    "follow this spec, not the typos):\n" + spec
                )
            checklist = prompt_appendix(
                mode=self.prompt_mode,
                spec=spec,
                always_surface=(self.prompt_mode == "developer"),
            )
            if checklist:
                system_content += checklist
            if self.prompt_mode == "developer" and looks_like_web_task(user_text, spec):
                for skill_name in ("static-web", "coding-companion"):
                    skill = read_skill(skill_name)
                    if skill and not skill.startswith("Unknown skill"):
                        system_content += "\n\n" + skill
            messages: list[Message] = [
                {"role": "system", "content": system_content},
                *self.history,
            ]
            compacted = compact_messages(self.provider, messages)
            if compacted is not None:
                messages = compacted
                self.compacted.emit(estimate_chars(messages))
            if spec:
                self.phase.emit("Working from", spec.replace("\n", " ")[:88])
            else:
                self.phase.emit("Thinking", "")
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
            )
            self._remember(user_text, reply)
            log_event("turn_end", reply=reply or "")
            if self.session_id:
                write_markdown(self.session_id)
            self.finished.emit(reply or "")
        except Exception as exc:  # noqa: BLE001
            log_event("turn_end", error=str(exc))
            if self.session_id:
                write_markdown(self.session_id)
            self.failed.emit(str(exc))
        finally:
            bind_session("")


class CommitWorker(QThread):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, provider: BaseProvider, diff: str) -> None:
        super().__init__()
        self.provider = provider
        self.diff = diff

    def run(self) -> None:
        try:
            reply = self.provider.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Write one conventional git commit message for this diff. "
                            "Output only the message, no quotes, no explanation."
                        ),
                    },
                    {"role": "user", "content": self.diff[:8000]},
                ]
            )
            text = (reply.content or "").strip().splitlines()[0] if reply.content else ""
            self.finished.emit(text or "Update files")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def _ping_url(url: str) -> bool:
    try:
        from .comfyui import _http

        return _http.get(url, timeout=0.35).status_code == 200
    except Exception:  # noqa: BLE001
        return False


class HealthProbe(QThread):
    """llama-server / ComfyUI pings must not run on the UI thread."""

    probed = pyqtSignal(object)

    def run(self) -> None:
        from .comfyui import ping_comfyui
        from .llama_server import server_v1
        from .searxng import ping_searxng, searxng_urls

        local_ok = _ping_url(f"{server_v1(LLAMA_SERVER_URL)}/models")
        origins = searxng_urls()
        self.probed.emit(
            {
                "local": local_ok,
                "searxng": ping_searxng(origins[0]) if origins else None,
                "comfyui": ping_comfyui(),
            }
        )


def _dot(ok: bool | None) -> str:
    if ok is True:
        return '<span style="color:#34d399;">●</span>'
    if ok is False:
        return '<span style="color:#f87171;">●</span>'
        return '<span style="color:#94a3b8;">●</span>'


class LocalWarmup(QThread):
    """Start llama-server off the UI thread when Local is on and a GGUF exists."""

    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, model: str = "", parent=None) -> None:
        super().__init__(parent)
        self._model = model

    def run(self) -> None:
        try:
            from .llama_server import ensure_ready, ping_server

            if ping_server():
                self.done.emit("ready")
                return
            self.done.emit(ensure_ready(self._model))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def strip_wake_boilerplate(text: str) -> str:
    """Drop leftover Hey Jinx / goodbye-to-sleep lines from old greetings."""
    out: list[str] = []
    for line in (text or "").splitlines():
        low = line.lower()
        if "hey jinx" in low or "goodbye puts me back" in low or "goodbye / ok bye" in low:
            continue
        out.append(line)
    return "\n".join(out).strip()


class ChatWindow(QMainWindow):
    def __init__(self, avatar=None, host=None) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(create_jinx_icon(32))
        self.resize(*CHAT_WINDOW_SIZE)

        self.host = host
        self.history: list[Message] = []
        self._worker: ChatWorker | None = None
        self._listen_gen = 0
        self._tools_this_turn: list[str] = []
        self._turn_started = 0.0
        self.memory = self._create_memory()
        self.avatar = avatar
        if avatar is not None:
            self._bind_avatar(avatar)
        self.voice = JinxVoice(self) if TTS_ENABLED else None
        self.stt = SpeechToText(self) if STT_ENABLED else None
        if self.voice is not None:
            self.voice.speech_started.connect(self._on_speech_started)
            self.voice.speech_progress.connect(self._on_speech_progress)
            self.voice.speech_finished.connect(self._on_speech_finished)
        self._settings = load_settings()
        self._cw = character_window(self._settings)
        self._look = look_settings(self._settings)
        self._audio = audio_settings(self._settings)
        self._app_mode = "developer"
        self._activity = "home"
        self.session = None
        self._follow_ups: list[str] = []
        self._stopped = False
        self._terminal_open = True
        self._live_buf = ""
        self._live_shown = ""
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(16)
        self._live_timer.timeout.connect(self._flush_live)

        self._build_ui()
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self._apply_window_style()
        self._apply_window_mode()
        self._apply_saved_settings()
        self._restore_session()
        if self.stt is not None:
            self.stt.transcribed.connect(self._on_stt_text)
            self.stt.failed.connect(self._on_stt_error)
            self.stt.recording_changed.connect(self._on_stt_recording)
            self.stt.status.connect(self._on_stt_status)
            self.stt.wake_heard.connect(self._on_wake_heard)
            self.stt.slept.connect(self._on_slept)
            self.stt.call_changed.connect(self._on_call_changed)
            self.stt.level.connect(self._on_stt_level)
        self._probe: HealthProbe | None = None
        self._local_warm: LocalWarmup | None = None
        self._local_ok: bool | None = None
        self._searx_ok: bool | None = None
        self._comfy_ok: bool | None = None
        QTimer.singleShot(400, self.refresh_status)
        QTimer.singleShot(800, self._warm_local)
        QTimer.singleShot(1800, self._maybe_resume_self_modify)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(20000)
        self._status_timer.timeout.connect(self.refresh_status)
        self._status_timer.start()
        self.life = Heartbeat(self)
        self.life.configure(lambda: self._provider_for_role("companion"))
        self.life.set_mode(self._app_mode)
        self.life.wrap_ready.connect(self._on_life_wrap)
        self.life.slept.connect(self._on_life_slept)
        self.life.woke.connect(self._on_life_woke)
        self.life.status.connect(self._on_life_status)
        QTimer.singleShot(1500, self.life.start)

    def _create_memory(self):
        if not MEMORY_ENABLED:
            return None
        try:
            memory = Mem0Memory()
            memory.start_background()
            return memory
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] Failed to start mem0: {exc}")
            return None

    def _computer_note(self) -> str:
        folders = current_folders()
        if folders:
            return f"Shared folder: {folders[0]}."
        return (
            "No folder yet — pick Computer on the project, or Files → Add folder. "
            "Otherwise tools use session scratch."
        )

    def _greet(self) -> None:
        pid = str((getattr(self, "session", None) or {}).get("project_id") or "")
        bid = str((getattr(self, "session", None) or {}).get("bot_id") or "chief")
        voice = (
            "Type a message, or start a call from the phone button. "
            "Leave the call when you are done."
        )
        if not pid:
            self._append_message(
                "assistant",
                "Hey — I'm Jinx. Home is Projects and Teams. Team Members is the bot "
                f"catalog. Open a project for Everyone (Chief) or a 1:1 chat.\n\n{voice}",
            )
            return
        computer = self._computer_note()
        if bid.lower() != "chief":
            from .platform.registry import get_bot

            bot = get_bot(bid)
            name = bot.name if bot else bid
            self._append_message(
                "assistant",
                f"Hey — this is a 1:1 with {name}. Computer (Files / Terminal) is the "
                f"shared machine for this project. {computer}\n\n{voice}",
            )
            return
        self._append_message(
            "assistant",
            "Hey — this is Everyone, the group thread. Chief of Staff coordinates; "
            f"click a bot on the left for 1:1. {computer}\n\n{voice}",
        )

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.titlebar = TitleBar()
        self.titlebar.close_requested.connect(self.close)
        self.titlebar.minimize_requested.connect(self.showMinimized)
        self.titlebar.maximize_requested.connect(self._toggle_max)
        self.titlebar.downloads_clicked.connect(self._toggle_download_flyout)
        outer.addWidget(self.titlebar)

        row = QWidget()
        root = QHBoxLayout(row)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        activity = QFrame()
        activity.setObjectName("activityBar")
        activity.setFixedWidth(52)
        act = QVBoxLayout(activity)
        act.setContentsMargins(8, 10, 8, 10)
        act.setSpacing(6)

        def _act(kind: str, tip: str) -> QPushButton:
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setObjectName("activity")
            btn.setToolTip(tip)
            btn.setFixedSize(36, 36)
            btn.setIcon(create_activity_icon(kind, 20, "#e8e8ed"))
            btn.setIconSize(QSize(20, 20))
            act.addWidget(btn)
            return btn

        self.act_home = _act("home", "Home — projects and teams")
        self.act_members = _act("id-card", "Team Members")
        self.act_teams = _act("users", "Project explorer")
        self.act_files = _act("files", "Computer — Files / Terminal")
        self.act_git = _act("git", "Source Control")
        self.act_db = _act("db", "Database")
        self.act_models = _act("robot", "AI Models")
        self.act_settings = _act("settings", "Settings")
        act.addStretch(1)

        self.nav_quit = QPushButton()
        self.nav_quit.setObjectName("activity")
        self.nav_quit.setProperty("role", "quit")
        self.nav_quit.setFixedSize(36, 36)
        self.nav_quit.setToolTip("Exit")
        self.nav_quit.setIcon(create_activity_icon("power", 20, "#ff8a84"))
        self.nav_quit.setIconSize(QSize(20, 20))
        act.addWidget(self.nav_quit)

        self.file_explorer = FileExplorerPanel()
        self.git_panel = GitPanel()
        self.db_panel = DatabasePanel()
        self.file_explorer.status.connect(self._set_status)
        self.git_panel.status.connect(self._set_status)
        self.db_panel.status.connect(self._set_status)
        self.file_explorer.workspace_open_requested.connect(self._open_workspace)
        self.file_explorer.add_folder_requested.connect(self._add_workspace_folder)
        self.file_explorer.add_this_folder_requested.connect(self._add_workspace_folder)
        self.file_explorer.sqlite_requested.connect(self._open_sqlite_from_tree)
        self.file_explorer.file_open_requested.connect(self._open_editor_file)
        self.file_explorer.remove_folder_requested.connect(self._remove_workspace_folder)
        self.file_explorer.terminal_toggle_requested.connect(self._toggle_terminal)
        self.git_panel.generate_commit_requested.connect(self._ai_commit_message)
        self.git_panel.file_open_requested.connect(self._open_editor_file)

        self.side_stack = QStackedWidget()
        self.side_stack.setMinimumWidth(240)
        self.platform_page = PlatformPage()
        self.platform_page.bot_opened.connect(self._on_bot_opened)
        self.platform_page.computer_requested.connect(self._open_computer)
        self.platform_page.add_team_requested.connect(self._project_add_team)
        self.platform_page.add_bot_requested.connect(self._project_add_bot)
        self.platform_page.leave_project_requested.connect(lambda: self._set_activity("home"))
        self.side_stack.addWidget(self.file_explorer)
        self.side_stack.addWidget(self.git_panel)
        self.side_stack.addWidget(self.db_panel)
        self.side_stack.addWidget(self.platform_page)

        self.editor_pane = EditorPane()
        self.editor_pane.status.connect(self._set_status)
        self.lm_panel = LmPanel()
        self.lm_panel.status.connect(self._set_status)
        self.lm_panel.use_chat.connect(self._on_use_chat_model)
        self.lm_panel.use_image.connect(self._on_use_image_model)
        self.lm_panel.host_enabled.connect(self._on_host_enabled)
        self.lm_panel.snapshots_ready.connect(self._on_lm_snapshots)
        self.lm_panel.api_changed.connect(self._on_api_access_changed)
        self.lm_panel.roles_changed.connect(self._on_roles_changed)
        self.lm_panel.pull_changed.connect(self._on_pull_changed)
        self._dl_flyout = DownloadFlyout(self)
        self._dl_flyout.history_requested.connect(self._open_download_history)
        self._dl_flyout.folder_requested.connect(self._open_models_folder)
        self._dl_flyout.cancel_requested.connect(lambda: self.lm_panel._cancel_pull("local"))
        self._dl_flyout.retry_requested.connect(lambda name: self.lm_panel._start_pull("local", name))
        self.home_page = HomePage()
        self.home_page.project_opened.connect(self._open_project)
        self.home_page.project_new.connect(self._new_project)
        self.home_page.project_edit.connect(self._edit_project)
        self.home_page.project_delete.connect(self._delete_project)
        self.home_page.team_opened.connect(self._edit_team)
        self.home_page.team_new.connect(self._new_team)
        self.home_page.team_edit.connect(self._edit_team)
        self.home_page.team_delete.connect(self._delete_team)
        self.members_page = MembersPage()
        self.members_page.member_new.connect(self._new_member)
        self.members_page.member_edit.connect(self._edit_member)
        self.members_page.member_delete.connect(self._delete_member)
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.editor_pane)
        self.center_stack.addWidget(self._build_settings_page())
        self.center_stack.addWidget(self.lm_panel)
        self.center_stack.addWidget(self.home_page)
        self.center_stack.addWidget(self.members_page)

        self.terminal_pane = TerminalPane()
        self.terminal_pane.last_closed.connect(self._on_terminal_last_closed)
        self.terminal_pane.session_opened.connect(self._on_terminal_session)
        self.main_split = QSplitter(Qt.Orientation.Vertical)
        self.main_split.addWidget(self.center_stack)
        self.main_split.addWidget(self.terminal_pane)
        self.main_split.setStretchFactor(0, 4)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([720, 200])

        self.chat_column = self._build_chat_column()
        self.chat_column.setMinimumWidth(320)

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.body_splitter.addWidget(self.side_stack)
        self.body_splitter.addWidget(self.main_split)
        self.body_splitter.addWidget(self.chat_column)
        self.body_splitter.setStretchFactor(0, 1)
        self.body_splitter.setStretchFactor(1, 3)
        self.body_splitter.setStretchFactor(2, 2)
        self.body_splitter.setSizes([280, 780, 420])

        root.addWidget(activity)
        root.addWidget(self.body_splitter, stretch=1)
        outer.addWidget(row, stretch=1)

        self.act_home.clicked.connect(lambda: self._set_activity("home"))
        self.act_members.clicked.connect(lambda: self._set_activity("members"))
        self.act_files.clicked.connect(lambda: self._set_activity("files"))
        self.act_git.clicked.connect(lambda: self._set_activity("git"))
        self.act_db.clicked.connect(lambda: self._set_activity("db"))
        self.act_teams.clicked.connect(lambda: self._set_activity("teams"))
        self.act_models.clicked.connect(lambda: self._set_activity("models"))
        self.act_settings.clicked.connect(lambda: self._set_activity("settings"))
        self.nav_quit.clicked.connect(self._on_quit)
        self._sync_chrome()
        self._set_activity("home")
        self._on_pull_changed(self.lm_panel.pull_snapshot())

        save_sc = QShortcut(QKeySequence.StandardKey.Save, self)
        save_sc.activated.connect(self.editor_pane.save_current)
        term_sc = QShortcut(QKeySequence("Ctrl+`"), self)
        term_sc.activated.connect(self._toggle_terminal)

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.titlebar.set_maximized(self.isMaximized())

    def _on_pull_changed(self, payload: object) -> None:
        snap = payload if isinstance(payload, dict) else {}
        self.titlebar.set_downloads(
            visible=bool(snap.get("active") or snap.get("recent")),
            active=bool(snap.get("active")),
        )
        if self._dl_flyout.isVisible():
            self._dl_flyout.set_snapshot(snap)

    def _toggle_download_flyout(self) -> None:
        if self._dl_flyout.isVisible():
            self._dl_flyout.hide()
            return
        self._dl_flyout.set_snapshot(self.lm_panel.pull_snapshot())
        self._dl_flyout.show()
        self._dl_flyout.place_below(self.titlebar.dl_btn)

    def _open_download_history(self) -> None:
        self._dl_flyout.hide()
        self._set_activity("models")
        self.lm_panel.show_downloads()

    def _open_models_folder(self) -> None:
        from .context_menu import reveal_in_explorer
        from .local_models import models_dir

        reveal_in_explorer(models_dir())

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if hasattr(self, "titlebar"):
            self.titlebar.set_maximized(self.isMaximized())

    def _sync_chrome(self) -> None:
        self._app_mode = "developer"
        self.act_home.setVisible(True)
        self.act_members.setVisible(True)
        in_project = bool(str((getattr(self, "session", None) or {}).get("project_id") or ""))
        self.act_teams.setVisible(in_project)
        for btn in (self.act_files, self.act_git, self.act_db):
            btn.setVisible(in_project)
        self.act_models.setVisible(True)
        if hasattr(self, "titlebar"):
            from .platform.registry import get_project

            pid = str((getattr(self, "session", None) or {}).get("project_id") or "")
            project = get_project(pid) if pid else None
            label = project.name if project else "Home"
            self.titlebar.set_context(label)
        self._sync_terminal_visibility()

    def _update_input_placeholder(self) -> None:
        if not hasattr(self, "input"):
            return
        if self._worker is not None:
            return
        bot_id = str((self.session or {}).get("bot_id") or "chief").strip() or "chief"
        project_id = str((self.session or {}).get("project_id") or "")
        if bot_id.lower() != "chief":
            from .platform.registry import get_bot

            bot = get_bot(bot_id)
            label = bot.name if bot else bot_id
            self.input.setPlaceholderText(f"Message {label}…")
            return
        if project_id:
            from .platform.registry import get_project

            project = get_project(project_id)
            label = project.name if project else "the team"
            self.input.setPlaceholderText(
                f"Message Chief of Staff…  {label}  ·  /review  /commit"
            )
        else:
            self.input.setPlaceholderText("Open a project from Home to chat.")

    def _set_activity(self, name: str) -> None:
        section = ""
        if name in {"look", "appearance"}:
            section = "appearance"
            name = "settings"
        name, chat_full, show_chat = chat_pane_for(self._app_mode, name)
        if name == "teams" and not str(
            (getattr(self, "session", None) or {}).get("project_id") or ""
        ):
            name = "home"
            chat_full, show_chat = False, False
        self._activity = name
        mapping = {
            "chat": (self.act_home, 0, 3, False),
            "home": (self.act_home, 0, 3, False),
            "members": (self.act_members, 0, 4, False),
            "files": (self.act_files, 0, 0, True),
            "git": (self.act_git, 1, 0, True),
            "db": (self.act_db, 2, 0, True),
            "teams": (self.act_teams, 3, 0, True),
            "models": (self.act_models, 0, 2, False),
            "settings": (self.act_settings, 0, 1, False),
        }
        btn, side_i, center_i, show_side = mapping[name]
        for other in (
            self.act_home,
            self.act_members,
            self.act_files,
            self.act_git,
            self.act_db,
            self.act_teams,
            self.act_models,
            self.act_settings,
        ):
            other.blockSignals(True)
            other.setChecked(other is btn)
            other.blockSignals(False)
        if name in {"home", "members"}:
            self.side_stack.hide()
            self.center_stack.show()
            self.center_stack.setCurrentIndex(center_i)
            self.chat_column.hide()
            if name == "home" and hasattr(self, "home_page"):
                self.home_page.reload()
            if name == "members" and hasattr(self, "members_page"):
                self.members_page.reload()
        elif name == "teams":
            self.side_stack.show()
            self.side_stack.setCurrentIndex(3)
            self.center_stack.hide()
            self.chat_column.show()
            total = sum(self.body_splitter.sizes()) or 1480
            self.body_splitter.setSizes(
                [int(total * 0.28), 0, int(total * 0.72)]
            )
        elif chat_full:
            self.side_stack.hide()
            self.center_stack.hide()
            self.chat_column.show()
        else:
            self.side_stack.setVisible(show_side)
            self.center_stack.show()
            if show_side:
                self.side_stack.setCurrentIndex(side_i)
            self.center_stack.setCurrentIndex(center_i)
            self.chat_column.setVisible(show_chat)
            sizes = self.body_splitter.sizes()
            if len(sizes) == 3 and sizes[1] < 80:
                total = sum(sizes) or 1480
                if show_chat:
                    self.body_splitter.setSizes(
                        [int(total * 0.2), int(total * 0.52), int(total * 0.28)]
                    )
                else:
                    self.body_splitter.setSizes(
                        [int(total * 0.22), int(total * 0.78), 0]
                    )
            elif show_chat:
                sizes = self.body_splitter.sizes()
                if len(sizes) == 3 and sizes[2] < 120:
                    total = sum(sizes) or 1480
                    self.body_splitter.setSizes(
                        [int(total * 0.2), int(total * 0.52), int(total * 0.28)]
                    )
        if name == "git":
            self.git_panel.refresh()
        if name == "teams" and hasattr(self, "platform_page"):
            self._sync_project_ui()
        if name == "settings":
            self._set_settings_section(section or getattr(self, "_settings_section", "general"))
        if hasattr(self, "lm_panel"):
            self.lm_panel.set_polling(name == "models")
        self._sync_terminal_visibility()

    def _set_settings_section(self, name: str) -> None:
        key = (
            name
            if name in {"general", "api", "connectors", "skills", "appearance"}
            else "general"
        )
        self._settings_section = key
        index = {
            "general": 0,
            "api": 1,
            "connectors": 2,
            "skills": 3,
            "appearance": 4,
        }[key]
        if hasattr(self, "settings_stack"):
            self.settings_stack.setCurrentIndex(index)
        for nav_key, btn in getattr(self, "_settings_nav", {}).items():
            btn.blockSignals(True)
            btn.setChecked(nav_key == key)
            btn.blockSignals(False)

    def _toggle_terminal(self) -> None:
        if not str((getattr(self, "session", None) or {}).get("project_id") or ""):
            return
        if self._activity in {"settings", "models", "teams", "home", "members"}:
            self._terminal_open = True
            self._set_activity("files")
        else:
            self._terminal_open = not bool(getattr(self, "_terminal_open", True))
            self._sync_terminal_visibility()
        if self._terminal_open and hasattr(self, "main_split"):
            sizes = self.main_split.sizes()
            if len(sizes) == 2 and sizes[1] < 80:
                self.main_split.setSizes([max(400, sizes[0]), 200])
            self.terminal_pane.ensure_session()

    def _on_terminal_last_closed(self) -> None:
        self._terminal_open = False
        self._sync_terminal_visibility()

    def _on_terminal_session(self) -> None:
        if not str((getattr(self, "session", None) or {}).get("project_id") or ""):
            return
        self._terminal_open = True
        if self._activity in {"settings", "models", "teams", "home", "members"}:
            self._set_activity("files")
            return
        self._sync_terminal_visibility()
        if hasattr(self, "main_split"):
            sizes = self.main_split.sizes()
            if len(sizes) == 2 and sizes[1] < 80:
                self.main_split.setSizes([max(400, sizes[0]), 200])

    def _sync_terminal_visibility(self) -> None:
        if not hasattr(self, "terminal_pane"):
            return
        allowed = self._activity not in {
            "settings",
            "models",
            "teams",
            "home",
            "members",
        }
        visible = allowed and bool(getattr(self, "_terminal_open", True))
        was_visible = self.terminal_pane.isVisible()
        self.terminal_pane.setVisible(visible)
        if visible and not was_visible:
            self.terminal_pane.ensure_session()

    def _open_editor_file(self, path: str) -> None:
        self._set_activity("files")
        self.editor_pane.open_file(path)

    def bind_avatar(self, avatar) -> None:
        self.avatar = avatar
        if avatar is None:
            return
        try:
            avatar.mute_toggled.disconnect(self._on_avatar_mute)
        except TypeError:
            pass
        try:
            avatar.call_toggled.disconnect(self._on_avatar_call)
        except TypeError:
            pass
        avatar.mute_toggled.connect(self._on_avatar_mute)
        avatar.call_toggled.connect(self._on_avatar_call)
        muted = bool(audio_settings().get("mic_muted", False))
        avatar.set_muted(muted)
        in_call = bool(self.stt is not None and self.stt.in_call)
        avatar.set_in_call(in_call)

    def _bind_avatar(self, avatar) -> None:
        self.bind_avatar(avatar)

    def _on_avatar_mute(self, muted: bool) -> None:
        self._set_mic_muted(bool(muted))

    def _set_mic_muted(self, muted: bool) -> None:
        self._audio = save_audio_settings({"mic_muted": bool(muted)})
        if self.stt is None:
            if self.avatar:
                self.avatar.set_muted(muted)
            return
        if muted:
            self.stt.set_muted(True)
        else:
            self.stt.set_muted(False)
        if self.avatar:
            self.avatar.set_muted(muted)

    def _on_avatar_call(self, on: bool) -> None:
        self._set_in_call(bool(on))

    def _set_in_call(self, on: bool) -> None:
        if self.stt is None:
            self._sync_call_chrome(False)
            return
        if bool(on) == bool(self.stt.in_call):
            self._sync_call_chrome(bool(on))
            return
        if on:
            self.stt.join_call()
        else:
            self.stt.leave_call()
        self._sync_call_chrome(bool(self.stt.in_call))

    def _sync_call_chrome(self, on: bool) -> None:
        if hasattr(self, "composer"):
            self.composer.set_in_call(bool(on))
        if self.avatar:
            self.avatar.set_in_call(bool(on))

    def _on_call_changed(self, on: bool) -> None:
        self._sync_call_chrome(bool(on))

    def _voice_idle_caption(self) -> str:
        if self.stt is not None and self.stt.in_call:
            if audio_settings().get("mic_muted"):
                return "In a call (muted)"
            return "In a call"
        return "Ready"

    def sync_avatar_checked(self, checked: bool) -> None:
        if hasattr(self, "char_visible"):
            self.char_visible.blockSignals(True)
            self.char_visible.setChecked(bool(checked))
            self.char_visible.blockSignals(False)
        if hasattr(self, "avatar_status"):
            self.avatar_status.setText("on screen" if checked else "hidden")

    def sync_character_controls(self, cfg: dict | None = None) -> None:
        cfg = cfg or character_window()
        self._cw = cfg
        if hasattr(self, "size_group"):
            preset = str(cfg.get("size_preset") or "default")
            btn = self.size_group.button({"compact": 0, "default": 1, "large": 2}.get(preset, 1))
            if btn is not None:
                self.size_group.blockSignals(True)
                btn.setChecked(True)
                self.size_group.blockSignals(False)
        self.sync_avatar_checked(bool(cfg.get("visible", True)))

    def show_character_page(self) -> None:
        self._set_activity("appearance")

    def _on_quit(self) -> None:
        if self.host is not None:
            self.host.quit()
        else:
            self.close()

    def _section_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("sectionLabel")
        return lab

    def _build_look_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 28, 24)
        layout.setSpacing(10)
        layout.addWidget(
            PageHeader(
                "Appearance",
                "Desktop overlay. Drag the orb to move it. Double-click for Call, Mute, and Close.",
            )
        )

        self.char_visible = SwitchRow(
            "Show on screen",
            "Always-on-top acrylic overlay. Click anywhere on the window.",
            checked=bool(self._cw.get("visible", True)),
        )
        self.char_visible.toggled.connect(self._on_character_visible)
        layout.addWidget(self.char_visible)

        layout.addWidget(self._section_label("Window size"))
        self.size_group = QButtonGroup(self)
        labels = [
            ("compact", "Compact — 200×300"),
            ("default", "Default — 240×360"),
            ("large", "Large — 300×460"),
        ]
        current = str(self._cw.get("size_preset") or "default")
        for i, (key, label) in enumerate(labels):
            radio = QRadioButton(label)
            radio.setProperty("preset", key)
            self.size_group.addButton(radio, i)
            radio.setChecked(key == current)
            layout.addWidget(radio)
        self.size_group.buttonClicked.connect(self._on_size_preset)

        self.look_transparent = SwitchRow(
            "Transparent background",
            "Drop the navy glass. The orb floats on the desktop.",
            checked=bool(self._look.get("transparent_bg", False)),
        )
        self.look_transparent.toggled.connect(self._on_look_transparent)
        layout.addWidget(self.look_transparent)

        self.look_hide_border = SwitchRow(
            "Hide outline",
            "No cyan window stroke. Useful with a transparent background.",
            checked=bool(self._look.get("hide_border", False)),
        )
        self.look_hide_border.toggled.connect(self._on_look_hide_border)
        layout.addWidget(self.look_hide_border)

        layout.addWidget(self._section_label("Body"))
        self.orb_style_group = QButtonGroup(self)
        styles = [
            ("orb", "Orb — energy ball that becomes a smile, heart, brain, star…"),
            ("humanoid", "Figure — female particle body with hair"),
        ]
        current_style = str(self._look.get("orb_style") or "orb")
        for i, (key, label) in enumerate(styles):
            radio = QRadioButton(label)
            radio.setProperty("orb_style", key)
            self.orb_style_group.addButton(radio, i)
            radio.setChecked(key == current_style)
            layout.addWidget(radio)
        self.orb_style_group.buttonClicked.connect(self._on_orb_style)

        self.look_hologram = SwitchRow(
            "Hologram",
            "Scanline flicker and cyan/pink chromatic drift on the overlay.",
            checked=bool(self._look.get("hologram", False)),
        )
        self.look_hologram.toggled.connect(lambda on: self._apply_look({"hologram": bool(on)}))
        layout.addWidget(self.look_hologram)

        self.look_debug = SwitchRow(
            "Body debug",
            "Draw the hidden skeleton, IK targets, FPS, and particle count.",
            checked=bool(self._look.get("body_debug", False)),
        )
        self.look_debug.toggled.connect(lambda on: self._apply_look({"body_debug": bool(on)}))
        layout.addWidget(self.look_debug)

        restart_btn = QPushButton("Restart Jinx")
        restart_btn.setObjectName("secondary")
        restart_btn.setToolTip("Restart the app so Appearance and code changes apply")
        restart_btn.clicked.connect(self._restart)
        layout.addWidget(restart_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

        if not AVATAR_ENABLED:
            self.char_visible.setEnabled(False)
            self.look_transparent.setEnabled(False)
            self.look_hide_border.setEnabled(False)
            for btn in self.size_group.buttons() + self.orb_style_group.buttons():
                btn.setEnabled(False)
            self.look_hologram.setEnabled(False)
            self.look_debug.setEnabled(False)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        wrap = QWidget()
        wrap.setObjectName("root")
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return wrap

    def _on_character_visible(self, on: bool) -> None:
        save_character_window({"visible": bool(on)})
        if self.host is not None:
            self.host.toggle_avatar(bool(on))

    def _on_size_preset(self, _btn=None) -> None:
        btn = self.size_group.checkedButton()
        if btn is None:
            return
        preset = str(btn.property("preset") or "default")
        width, height = CHARACTER_SIZE_PRESETS.get(preset, CHARACTER_SIZE_PRESETS["default"])
        cfg = save_character_window({
            "size_preset": preset,
            "width": width,
            "height": height,
        })
        if self.host is not None:
            self.host.apply_character_window(cfg)

    def _apply_look(self, update: dict) -> None:
        self._look = save_look_settings(update)
        if self.host is not None:
            self.host.apply_look(self._look)
        elif self.avatar is not None:
            self.avatar.apply_look(self._look)

    def _on_look_transparent(self, on: bool) -> None:
        self._apply_look({"transparent_bg": bool(on)})

    def _on_look_hide_border(self, on: bool) -> None:
        self._apply_look({"hide_border": bool(on)})

    def _on_orb_style(self, _btn=None) -> None:
        btn = self.orb_style_group.checkedButton()
        if btn is None:
            return
        self._apply_look({"orb_style": str(btn.property("orb_style") or "orb")})

    def _build_chat_column(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        page.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        bar = FlowLayout(spacing=6)
        self.session_combo = QComboBox(page)
        self.session_combo.hide()
        self.session_combo.currentIndexChanged.connect(self._on_session_combo)
        bar.addWidget(
            icon_button(
                "fa5s.sitemap", "Jump to an earlier turn", self._open_tree, size=28
            )
        )
        bar.addWidget(
            icon_button("fa5s.pen", "Rename session", self._rename_session, size=28)
        )
        bar.addWidget(
            icon_button(
                "fa5s.download",
                "Export activity log (share this when debugging Jinx)",
                self._export_activity,
                size=28,
            )
        )
        bar.addWidget(
            icon_button(
                "fa5s.list",
                "View session activity timeline",
                self._open_activity_timeline,
                size=28,
            )
        )
        bar.addWidget(
            icon_button(
                "fa5s.desktop",
                "Open the Bots' computer (Files / Terminal)",
                self._open_computer,
                size=28,
            )
        )
        bar.addWidget(
            icon_button(
                "fa5s.trash-alt",
                "Delete session",
                self._delete_session,
                danger=True,
                size=28,
            )
        )
        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("toolbarCombo")
        self.provider_combo.setToolTip("Language model for this session")
        self.provider_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.provider_combo.setMinimumContentsLength(9)
        self.provider_combo.setMinimumWidth(96)
        self.provider_combo.setMaximumWidth(132)
        self.provider_combo.setFixedHeight(28)
        self.provider_combo.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.provider_combo.currentIndexChanged.connect(self._on_provider_combo)
        bar.addWidget(self.provider_combo)
        self.speak_toggle = IconToggle(
            "volume-up",
            "volume-mute",
            "Speech on — read replies out loud",
            "Speech off",
            size=28,
            checked=True,
        )
        self.speak_toggle.setEnabled(bool(self.voice and self.voice.available))
        bar.addWidget(self.speak_toggle)
        layout.addLayout(bar)
        self.caps_label = QLabel()
        self.caps_label.setObjectName("muted")
        self.caps_label.setWordWrap(True)
        layout.addWidget(self.caps_label)

        self.transcript = ChatTranscript()
        layout.addWidget(self.transcript, stretch=1)

        from .approval_bar import ToolApprovalBar

        self.approval_bar = ToolApprovalBar()
        self.approval_bar.decided.connect(self._on_approval_decided)
        layout.addWidget(self.approval_bar)

        from .ask_user_bar import AskUserBar

        self.ask_user_bar = AskUserBar()
        self.ask_user_bar.answered.connect(self._on_ask_user_answered)
        layout.addWidget(self.ask_user_bar)

        self.composer = Composer()
        self.input = self.composer
        self.send_button = self.composer.send_button
        self.stop_button = self.composer.stop_button
        self.composer.submitted.connect(self._on_composer_submit)
        self.composer.send_now.connect(self._on_composer_now)
        self.composer.stop_requested.connect(self._stop_agent)
        self.composer.call_toggled.connect(self._on_composer_call)
        self.composer.queue_removed.connect(self._drop_follow_up)
        self.composer.call_toggle.setEnabled(self.stt is not None)
        if self.stt is not None:
            self.composer.set_in_call(self.stt.in_call)
        layout.addWidget(self.composer)
        self._refresh_composer_harness()

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setMaximumHeight(36)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.status_label)

        self.speak_toggle.toggled.connect(lambda on: save_settings({"speak": on}))
        self._update_input_placeholder()
        follow_sc = QShortcut(QKeySequence("Alt+Return"), self)
        follow_sc.activated.connect(self._queue_follow_up)
        follow_sc2 = QShortcut(QKeySequence("Alt+Enter"), self)
        follow_sc2.activated.connect(self._queue_follow_up)
        return page

    def _restore_session(self) -> None:
        self.session = ensure_tree(ensure_active_session())
        self._ensure_session_project()
        self._bind_session_files()
        self._sync_history_from_tree()
        self._refresh_session_combo(self.session.get("id"))
        self._reload_transcript(self.history)
        self._apply_workspace_ui()
        self._sync_project_ui()

    def _bind_session_files(self) -> None:
        sid = str((self.session or {}).get("id") or "")
        bind_session(sid)
        try:
            from .session_fs import ensure_session_work_dir

            ensure_session_work_dir(sid)
        except OSError:
            pass

    def _sync_history_from_tree(self) -> None:
        if not getattr(self, "session", None):
            self.history = []
            return
        ensure_tree(self.session)
        self.history = visible_history(self.session)

    def _refresh_composer_harness(self) -> None:
        if not hasattr(self, "composer"):
            return
        try:
            from .connectors import enabled_connectors
            from .tools import tools_schema_for

            n_conn = len(enabled_connectors())
            project_id = str((getattr(self, "session", None) or {}).get("project_id") or "")
            bot_id = str((getattr(self, "session", None) or {}).get("bot_id") or "chief")
            n_tools = len(
                tools_schema_for(self._app_mode, project_id=project_id, bot_id=bot_id)
            )
            if bot_id and bot_id.lower() != "chief":
                from .platform.registry import get_bot

                bot = get_bot(bot_id)
                who = bot.name if bot else bot_id
                line = f"{who} · {n_tools} tools"
            elif project_id:
                from .platform.registry import get_project, load_registry

                project = get_project(project_id)
                registry = load_registry()
                n_teams = len(project.team_ids) if project else 0
                n_bots = len(registry.project_bots(project)) if project else 0
                line = (
                    f"Chief of Staff · {n_teams} team{'s' if n_teams != 1 else ''} · "
                    f"{n_bots} bot{'s' if n_bots != 1 else ''} · {n_tools} tools"
                )
            else:
                line = f"{n_conn} connector{'s' if n_conn != 1 else ''} · {n_tools} tools"
            self.composer.set_harness_status(line)
        except Exception:  # noqa: BLE001
            self.composer.set_harness_status("")

    def _refresh_session_combo(self, keep_id: str | None = None) -> None:
        keep_id = keep_id or (self.session.get("id") if getattr(self, "session", None) else None)
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for item in list_sessions():
            title = str(item.get("title") or "Untitled")
            self.session_combo.addItem(title, item.get("id"))
        if keep_id:
            idx = self.session_combo.findData(keep_id)
            if idx >= 0:
                self.session_combo.setCurrentIndex(idx)
        self.session_combo.blockSignals(False)

    def _apply_workspace_ui(self) -> None:
        folders = current_folders()
        text = "\n".join(str(f) for f in folders) if folders else "(no workspace folder)"
        if hasattr(self, "workspace_label"):
            self.workspace_label.setText(text)
        self.setWindowTitle(WINDOW_TITLE)
        if hasattr(self, "file_explorer"):
            self.file_explorer.set_folders(folders)
        if hasattr(self, "git_panel"):
            self.git_panel.refresh()
        if hasattr(self, "terminal_pane"):
            self.terminal_pane.sync_workspace()
            if self.terminal_pane.isVisible():
                self.terminal_pane.ensure_session()
        host, _model = resolve_chat_brain(self.session)
        self._fill_provider_combo(host)
        self._sync_provider_ui(host)
        self._update_caps_label()

    def _persist_session(self) -> None:
        if not getattr(self, "session", None):
            return
        ensure_tree(self.session)
        folders = current_folders()
        self.session["folders"] = [str(f) for f in folders]
        # Never stamp the app install/source tree as the session workspace.
        self.session["workspace"] = str(folders[0]) if folders else ""
        if hasattr(self, "provider_combo"):
            self.session["provider"] = str(self.provider_combo.currentData() or "local")
        brains = brains_settings()
        if getattr(self, "session", None) and not self.session.get("model"):
            self.session["model"] = str(brains.get("chat_model") or "")
        save_session(self.session, activate=True)
        self._persist_project_computer()
        self._refresh_session_combo(self.session.get("id"))

    def _on_session_combo(self, _index: int = 0) -> None:
        if self._worker is not None:
            self._refresh_session_combo(self.session.get("id"))
            return
        session_id = str(self.session_combo.currentData() or "")
        if not session_id or session_id == (self.session or {}).get("id"):
            return
        self._persist_session()
        loaded = set_active_session(session_id)
        if loaded is None:
            return
        self.session = ensure_tree(loaded)
        self._ensure_session_project()
        self._bind_session_files()
        self._sync_history_from_tree()
        self._reload_transcript(self.history)
        self._apply_workspace_ui()
        self._sync_project_ui()

    def _new_session(self) -> None:
        if self._worker is not None:
            return
        pid = str((self.session or {}).get("project_id") or "")
        bid = str((self.session or {}).get("bot_id") or "chief")
        self._persist_session()
        if pid:
            self.session = open_project_thread(pid, bid)
        else:
            self.session = new_session(mode="developer")
        self._ensure_session_project()
        self._bind_session_files()
        self._sync_history_from_tree()
        self._reload_transcript(self.history)
        self._refresh_session_combo(self.session.get("id"))
        self._apply_workspace_ui()
        self._sync_project_ui()

    def _fork_session(self) -> None:
        if self._worker is not None or not getattr(self, "session", None):
            return
        self._persist_session()
        self.session = ensure_tree(fork_session(self.session))
        self._ensure_session_project()
        self._bind_session_files()
        self._sync_history_from_tree()
        self._reload_transcript(self.history)
        self._refresh_session_combo(self.session.get("id"))
        self._apply_workspace_ui()
        self._sync_project_ui()
        self._set_status("Forked session.")

    def _export_activity(self) -> None:
        sid = str((getattr(self, "session", None) or {}).get("id") or "")
        if not sid:
            self._set_status("No session to export.")
            return
        title = str((self.session or {}).get("title") or "")
        suggested = str(Path.home() / f"jinx-activity-{sid[:8]}.md")
        path, _ok = QFileDialog.getSaveFileName(
            self,
            "Export activity",
            suggested,
            "Markdown (*.md);;JSON Lines (*.jsonl)",
        )
        if not path:
            return
        dest = export_copy(sid, Path(path), title=title)
        self._set_status(f"Saved activity: {dest}")

    def _open_activity_timeline(self) -> None:
        sid = str((getattr(self, "session", None) or {}).get("id") or "")
        if not sid:
            self._set_status("No session yet.")
            return
        from .activity_timeline import ActivityTimelineDialog

        title = str((self.session or {}).get("title") or "Session activity")
        ActivityTimelineDialog(sid, title=title, parent=self).exec()

    def _open_tree(self) -> None:
        if self._worker is not None or not getattr(self, "session", None):
            self._set_status("Wait for the current turn to finish.")
            return
        ensure_tree(self.session)
        rows = tree_outline(self.session)
        if not rows:
            self._set_status("No turns to jump to yet.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Session tree")
        dialog.resize(460, 420)
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setIconSize(QSize(14, 14))
        selected: QTreeWidgetItem | None = None
        stack: list[tuple[int, QTreeWidgetItem | None]] = []
        for row in rows:
            depth = int(row["depth"])
            label = str(row["role"] or "msg")
            preview = " ".join(str(row["content"]).split())[:72]
            item = QTreeWidgetItem([f"{label}: {preview}"])
            item.setData(0, Qt.ItemDataRole.UserRole, row["id"])
            kind = "chat" if row["role"] == "assistant" else "character"
            if row["is_head"]:
                color = "#28c840"
            elif row["on_path"]:
                color = "#22d3ee"
            else:
                color = "#8e8e93"
            item.setIcon(0, create_activity_icon(kind, 14, color))
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1] if stack else None
            if parent is None:
                tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            stack.append((depth, item))
            if row["is_head"]:
                selected = item
        tree.expandAll()
        if selected is not None:
            tree.setCurrentItem(selected)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        hint = QLabel("Select a turn. New messages continue from that point (other branches stay saved).")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        layout.addWidget(tree, stretch=1)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        item = tree.currentItem()
        if item is None:
            return
        node_id = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if not set_head(self.session, node_id):
            return
        self._sync_history_from_tree()
        self._reload_transcript(self.history)
        self._persist_session()
        self._set_status("Jumped to that turn.")

    def _rename_session(self) -> None:
        if not getattr(self, "session", None):
            return
        current = str(self.session.get("title") or "")
        title, ok = QInputDialog.getText(self, "Rename session", "Title:", text=current)
        if not ok or not title.strip():
            return
        self.session["title"] = title.strip()[:80]
        self._persist_session()
        self._apply_workspace_ui()

    def _delete_session(self) -> None:
        if self._worker is not None or not getattr(self, "session", None):
            return
        sid = self.session.get("id")
        if QMessageBox.question(
            self,
            "Delete session",
            "Delete this session and its chat history?",
        ) != QMessageBox.StandardButton.Yes:
            return
        delete_session(sid)
        self.session = ensure_tree(ensure_active_session())
        self._ensure_session_project()
        self._bind_session_files()
        self._sync_history_from_tree()
        self._reload_transcript(self.history)
        self._refresh_session_combo(self.session.get("id"))
        self._apply_workspace_ui()
        self._sync_project_ui()

    def _browse_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose workspace folder", str(folder_picker_start())
        )
        if not folder:
            return
        try:
            set_folders([folder])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Workspace", str(exc))
            return
        try:
            from .session_fs import ensure_workspace_gitignore

            ensure_workspace_gitignore([folder])
        except OSError:
            pass
        if getattr(self, "session", None):
            self._persist_session()
        self._apply_workspace_ui()

    def _add_workspace_folder(self, path: str = "") -> None:
        if isinstance(path, bool):
            path = ""
        folder = str(path or "").strip()
        if not folder:
            folder = QFileDialog.getExistingDirectory(
                self, "Add workspace folder", str(folder_picker_start())
            )
        if not folder:
            return
        try:
            add_folder(folder)
        except OSError as exc:
            QMessageBox.warning(self, "Workspace", str(exc))
            return
        try:
            from .session_fs import ensure_workspace_gitignore

            ensure_workspace_gitignore([folder])
        except OSError:
            pass
        if getattr(self, "session", None):
            self._persist_session()
        self._apply_workspace_ui()

    def _remove_workspace_folder(self, path: str = "") -> None:
        if isinstance(path, bool):
            path = ""
        target = str(path or "").strip()
        if not target:
            self._set_status("Right-click a workspace root in the explorer to remove it.")
            return
        try:
            remove_folder(target)
        except ValueError as exc:
            QMessageBox.warning(self, "Workspace", str(exc))
            return
        if getattr(self, "session", None):
            self._persist_session()
        self._apply_workspace_ui()

    def _open_sqlite_from_tree(self, path: str) -> None:
        self.db_panel.open_sqlite_file(path)
        self._set_activity("db")

    def _build_settings_page(self) -> QWidget:
        shell = QWidget()
        shell.setObjectName("root")
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(208)
        nav_l = QVBoxLayout(nav)
        nav_l.setContentsMargins(12, 18, 12, 16)
        nav_l.setSpacing(4)
        heading = QLabel("Settings")
        heading.setObjectName("switchTitle")
        nav_l.addWidget(heading)
        nav_l.addSpacing(12)

        self._settings_nav: dict[str, QPushButton] = {}
        self._settings_nav_group = QButtonGroup(self)
        self._settings_nav_group.setExclusive(True)

        def _nav_btn(key: str, label: str, tip: str, icon: str) -> QPushButton:
            btn = QPushButton(f"  {label}")
            btn.setCheckable(True)
            btn.setObjectName("settingsNavBtn")
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(create_activity_icon(icon, 16, "#c7c7cc"))
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(lambda _checked=False, k=key: self._set_settings_section(k))
            self._settings_nav_group.addButton(btn)
            self._settings_nav[key] = btn
            nav_l.addWidget(btn)
            return btn

        _nav_btn("general", "General", "Workspace, search, voice", "settings")
        _nav_btn("api", "API Access", "Optional search API keys", "key")
        _nav_btn("connectors", "Connectors", "GitHub, Linear, Notion, MCP", "plug")
        _nav_btn("skills", "Skills", "Playbook catalog (read_skill)", "book")
        appear = _nav_btn("appearance", "Appearance", "Desktop overlay and orb", "look")
        appear.setEnabled(AVATAR_ENABLED)
        nav_l.addStretch(1)

        self.settings_stack = QStackedWidget()
        self.settings_stack.addWidget(self._build_general_settings())
        self.api_page = ApiAccessPage(kinds=("search",))
        self.api_page.changed.connect(self._on_api_access_changed)
        self.settings_stack.addWidget(self.api_page)
        self.connectors_page = ConnectorsPage()
        self.connectors_page.changed.connect(self._refresh_composer_harness)
        self.settings_stack.addWidget(self.connectors_page)
        self.skills_page = SkillsPage()
        self.settings_stack.addWidget(self.skills_page)
        self.settings_stack.addWidget(self._build_look_page())

        row.addWidget(nav)
        row.addWidget(self.settings_stack, 1)
        self._settings_section = "general"
        self._settings_nav["general"].setChecked(True)
        return shell

    def _build_general_settings(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 28, 24)
        layout.setSpacing(10)
        layout.addWidget(
            PageHeader(
                "General",
                "Workspace, search, voice, home city, and how long the agent may work.",
            )
        )

        layout.addWidget(self._section_label("Status"))
        self.brain_status = QLabel()
        self.brain_status.setObjectName("muted")
        self.brain_status.setWordWrap(True)
        self.brain_status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.brain_status)

        layout.addWidget(self._section_label("Search"))
        search_hint = QLabel(
            "Web search uses DuckDuckGo (ddgs) — no Docker. "
            "Brave keys live in Settings → API Access. Optional SearXNG URL if you already run one."
        )
        search_hint.setObjectName("muted")
        search_hint.setWordWrap(True)
        layout.addWidget(search_hint)
        search_card = QFrame()
        search_card.setObjectName("settingsCard")
        search_l = QVBoxLayout(search_card)
        search_l.setContentsMargins(14, 12, 14, 12)
        search_l.setSpacing(8)
        self.searx_url = QLineEdit()
        self.searx_url.setPlaceholderText("optional — http://127.0.0.1:8080")
        self.searx_url.setText(str(brains_settings(self._settings).get("searxng_url") or ""))
        self.searx_url.setToolTip("Leave empty. DuckDuckGo is the default search.")
        self.searx_url.editingFinished.connect(self._on_searx_url)
        search_form = QFormLayout()
        search_form.setContentsMargins(0, 0, 0, 0)
        search_form.addRow("SearXNG URL", self.searx_url)
        search_l.addLayout(search_form)
        layout.addWidget(search_card)

        layout.addWidget(self._section_label("Environment"))
        env = QFrame()
        env.setObjectName("settingsCard")
        form = QFormLayout(env)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(10)
        self.workspace_label = QLabel("(no workspace folder)")
        self.workspace_label.setWordWrap(True)
        open_ws = QPushButton("Open folder")
        open_ws.setObjectName("secondary")
        open_ws.setToolTip("Open the current workspace in File Explorer")
        open_ws.clicked.connect(self._open_workspace)
        choose_ws = QPushButton("Choose folder")
        choose_ws.setObjectName("secondary")
        choose_ws.setToolTip("Replace the primary workspace folder")
        choose_ws.clicked.connect(self._browse_workspace)
        ws_row = QHBoxLayout()
        ws_row.addWidget(self.workspace_label, stretch=1)
        ws_row.addWidget(choose_ws)
        ws_row.addWidget(open_ws)
        wrap_ws = QWidget()
        wrap_ws.setLayout(ws_row)
        form.addRow("Workspace", wrap_ws)
        self.memory_status = QLabel("loading…")
        form.addRow("Memory (mem0)", self.memory_status)
        self.home_city = QLineEdit()
        self.home_city.setPlaceholderText(infer_home_city() or "Sofia")
        self.home_city.setText(str(locale_settings(self._settings).get("home_city") or ""))
        self.home_city.setToolTip("Used for weather and “here”. Empty = infer from Windows timezone.")
        self.home_city.editingFinished.connect(
            lambda: save_locale_settings({"home_city": self.home_city.text().strip()})
        )
        form.addRow("Home city", self.home_city)
        layout.addWidget(env)

        layout.addWidget(self._section_label("Agent"))
        agent_hint = QLabel(
            "How long a shell command may run. Tool rounds: 0 is unlimited "
            "(Stop ends the turn). Applies on the next message — no restart."
        )
        agent_hint.setObjectName("muted")
        agent_hint.setWordWrap(True)
        layout.addWidget(agent_hint)
        agent_card = QFrame()
        agent_card.setObjectName("settingsCard")
        agent_form = QFormLayout(agent_card)
        agent_form.setContentsMargins(14, 12, 14, 12)
        agent_form.setSpacing(10)
        agent_cfg = agent_settings(self._settings)
        self.command_timeout = QSpinBox()
        self.command_timeout.setRange(15, 900)
        self.command_timeout.setSingleStep(15)
        self.command_timeout.setSuffix(" s")
        self.command_timeout.setValue(int(agent_cfg["command_timeout"]))
        self.command_timeout.setToolTip(
            "composer create-project and npm install often need 2–5 minutes."
        )
        self.command_timeout.valueChanged.connect(self._on_agent_timeout)
        agent_form.addRow("Command timeout", self.command_timeout)
        self.agent_rounds = QSpinBox()
        self.agent_rounds.setRange(0, 80)
        self.agent_rounds.setSpecialValueText(" unlimited")
        self.agent_rounds.setValue(int(agent_cfg["max_rounds"] or 0))
        self.agent_rounds.setToolTip(
            "Each round is one model reply plus tools. 0 = keep going until "
            "the work is done or you press Stop."
        )
        self.agent_rounds.valueChanged.connect(self._on_agent_rounds)
        agent_form.addRow("Tool rounds per turn", self.agent_rounds)
        layout.addWidget(agent_card)
        self.tool_approval = SwitchRow(
            "Tool approvals",
            "When on, destructive tools (shell, git push/commit, writes, connectors) "
            "pause for Allow once / Always allow / Deny above the composer.",
            checked=bool(agent_cfg.get("tool_approval")),
        )
        self.tool_approval.toggled.connect(self._on_tool_approval)
        layout.addWidget(self.tool_approval)

        layout.addWidget(self._section_label("Life"))
        life_hint = QLabel(
            "Heartbeat keeps a private daily journal. Evening idle wraps the day "
            "and she sleeps so the log stays quiet. Morning or a new message wakes her. "
            "Heartbeat thoughts stay out of the chat."
        )
        life_hint.setObjectName("muted")
        life_hint.setWordWrap(True)
        layout.addWidget(life_hint)
        life_cfg = life_settings(self._settings)
        self.life_enabled = SwitchRow(
            "Alive",
            "Heartbeat + daily journal under data/life/. Off = reactive chat only.",
            checked=bool(life_cfg["enabled"]),
        )
        self.life_enabled.toggled.connect(self._on_life_enabled)
        layout.addWidget(self.life_enabled)
        life_card = QFrame()
        life_card.setObjectName("settingsCard")
        life_form = QFormLayout(life_card)
        life_form.setContentsMargins(14, 12, 14, 12)
        life_form.setSpacing(10)
        self.life_interval = QSpinBox()
        self.life_interval.setRange(30, 600)
        self.life_interval.setSingleStep(30)
        self.life_interval.setSuffix(" s")
        self.life_interval.setValue(int(life_cfg["interval_sec"]))
        self.life_interval.setToolTip("How often she checks the clock, hosts, and idle state.")
        self.life_interval.valueChanged.connect(self._on_life_knobs)
        life_form.addRow("Heartbeat", self.life_interval)
        self.life_sleep_hour = QSpinBox()
        self.life_sleep_hour.setRange(0, 23)
        self.life_sleep_hour.setValue(int(life_cfg["sleep_hour"]))
        self.life_sleep_hour.valueChanged.connect(self._on_life_knobs)
        life_form.addRow("Sleep after", self.life_sleep_hour)
        self.life_wake_hour = QSpinBox()
        self.life_wake_hour.setRange(0, 23)
        self.life_wake_hour.setValue(int(life_cfg["wake_hour"]))
        self.life_wake_hour.valueChanged.connect(self._on_life_knobs)
        life_form.addRow("Wake from", self.life_wake_hour)
        self.life_idle_min = QSpinBox()
        self.life_idle_min.setRange(5, 120)
        self.life_idle_min.setSuffix(" min")
        self.life_idle_min.setValue(int(life_cfg["idle_sleep_min"]))
        self.life_idle_min.valueChanged.connect(self._on_life_knobs)
        life_form.addRow("Idle before sleep", self.life_idle_min)
        layout.addWidget(life_card)

        layout.addWidget(self._section_label("Audio — STT / TTS"))
        audio = QFrame()
        audio.setObjectName("settingsCard")
        audio_form = QFormLayout(audio)
        audio_form.setContentsMargins(14, 12, 14, 12)
        audio_form.setSpacing(10)
        voice_hint = QLabel(
            f"Female neural voice for Jinx: {TTS_VOICE_EN} (English). "
            "Listening is Whisper (local, English). "
            "Start a call from the phone button in chat or the overlay HUD. "
            "Leave the call to hang up. Mute during a call from the overlay. "
            "There is no Hey Jinx wake word."
        )
        voice_hint.setWordWrap(True)
        voice_hint.setObjectName("muted")
        audio_form.addRow(voice_hint)
        self.input_combo = QComboBox()
        self.output_combo = QComboBox()
        self._fill_audio_devices()
        self.input_combo.currentIndexChanged.connect(self._on_audio_devices)
        self.output_combo.currentIndexChanged.connect(self._on_audio_devices)
        audio_form.addRow("Microphone (STT)", self.input_combo)
        audio_form.addRow("Output (TTS)", self.output_combo)
        vol_row = QHBoxLayout()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(self._audio.get("output_volume", 80)))
        self.volume_label = QLabel(f"{self.volume_slider.value()}%")
        self.volume_slider.valueChanged.connect(self._on_volume)
        vol_row.addWidget(self.volume_slider, stretch=1)
        vol_row.addWidget(self.volume_label)
        vol_wrap = QWidget()
        vol_wrap.setLayout(vol_row)
        audio_form.addRow("Voice volume", vol_wrap)
        self.tts_status = QLabel("Jenny")
        audio_form.addRow("TTS", self.tts_status)
        self.stt_status = QLabel(
            "Call: phone button starts, leave hangs up" if STT_ENABLED else "disabled"
        )
        audio_form.addRow("STT", self.stt_status)
        layout.addWidget(audio)

        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        wrap = QWidget()
        wrap.setObjectName("root")
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return wrap

    def _apply_saved_settings(self) -> None:
        speak = self._settings.get("speak", True)
        if self.speak_toggle.isEnabled():
            self.speak_toggle.setChecked(bool(speak))
        kind, _model = resolve_chat_brain(getattr(self, "session", None), self._settings)
        self._fill_provider_combo(kind)
        self._sync_provider_ui(kind)

    def _on_provider_combo(self, _index: int = 0) -> None:
        kind = str(self.provider_combo.currentData() or "local")
        self._sync_provider_ui(kind)
        save_brains_settings({"chat_host": kind})
        if getattr(self, "session", None):
            self.session["provider"] = kind
            self._persist_session()
        if self._worker is not None:
            self._worker.set_provider(self._build_provider(kind))
            self._set_status("Brain switches after this model round.")

    def _fill_audio_devices(self) -> None:
        self.input_combo.blockSignals(True)
        self.output_combo.blockSignals(True)
        self.input_combo.clear()
        self.output_combo.clear()
        self.input_combo.addItem("System default", "")
        self.output_combo.addItem("System default", "")
        for device in QMediaDevices.audioInputs():
            self.input_combo.addItem(device.description(), device.description())
        for device in QMediaDevices.audioOutputs():
            self.output_combo.addItem(device.description(), device.description())
        in_saved = self._audio.get("input_device") or ""
        out_saved = self._audio.get("output_device") or ""
        in_idx = self.input_combo.findData(in_saved)
        out_idx = self.output_combo.findData(out_saved)
        self.input_combo.setCurrentIndex(in_idx if in_idx >= 0 else 0)
        self.output_combo.setCurrentIndex(out_idx if out_idx >= 0 else 0)
        self.input_combo.blockSignals(False)
        self.output_combo.blockSignals(False)

    def _on_audio_devices(self) -> None:
        cfg = save_audio_settings({
            "input_device": str(self.input_combo.currentData() or ""),
            "output_device": str(self.output_combo.currentData() or ""),
        })
        self._audio = cfg
        if self.voice is not None:
            self.voice.apply_audio_settings()

    def _on_volume(self, value: int) -> None:
        self.volume_label.setText(f"{int(value)}%")
        self._audio = save_audio_settings({"output_volume": int(value)})
        if self.voice is not None:
            self.voice.apply_audio_settings()

    def _on_agent_timeout(self, value: int) -> None:
        save_agent_settings({"command_timeout": int(value)})

    def _on_agent_rounds(self, value: int) -> None:
        save_agent_settings({"max_rounds": int(value)})

    def _on_tool_approval(self, on: bool) -> None:
        save_agent_settings({"tool_approval": bool(on)})

    def _on_life_enabled(self, on: bool) -> None:
        save_life_settings({"enabled": bool(on)})
        if hasattr(self, "life"):
            if on:
                self.life.restart()
            else:
                self.life.stop()
                self._set_status("Life off")

    def _on_life_knobs(self, _value: int = 0) -> None:
        save_life_settings(
            {
                "interval_sec": int(self.life_interval.value()),
                "sleep_hour": int(self.life_sleep_hour.value()),
                "wake_hour": int(self.life_wake_hour.value()),
                "idle_sleep_min": int(self.life_idle_min.value()),
            }
        )
        if hasattr(self, "life") and life_settings()["enabled"]:
            self.life.restart()

    def _on_life_status(self, text: str) -> None:
        if text:
            self._set_status(text)

    def _on_life_wrap(self, text: str) -> None:
        body = (text or "").strip()
        if not body:
            return
        card = f"Day's journal\n\n{body}"
        if getattr(self, "session", None):
            append_turn(self.session, "assistant", card)
            self._sync_history_from_tree()
        else:
            self.history.append({"role": "assistant", "content": card})
        self._append_message("assistant", card)
        self._persist_session()

    def _on_life_slept(self) -> None:
        if self.avatar:
            self.avatar.set_activity("sleeping", "Sleeping")
        self._paint_health()

    def _on_life_woke(self, reason: str) -> None:
        del reason
        if self.avatar and self._worker is None:
            self.avatar.set_activity("idle", "Hey.")
        self._paint_health()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        label = getattr(self, "status_label", None)
        if label is None:
            return
        short = " ".join((text or "").split())
        if len(short) > 88:
            short = short[:85] + "…"
        try:
            label.setText(short)
            label.setToolTip(text if error and text else "")
        except RuntimeError:
            return

    def _on_stt_status(self, text: str) -> None:
        self._set_status(text)
        if self.avatar and self._worker is None:
            low = (text or "").lower()
            if "muted" in low:
                return
            if "hearing" in low or "listening" in low or "in a call" in low:
                self.avatar.set_activity("listening", text)
            elif "transcrib" in low:
                self.avatar.set_activity("listening", text)
            elif "ready" in low or "call ended" in low:
                self.avatar.set_activity("idle", self._voice_idle_caption())

    def _pause_listen(self) -> None:
        self._listen_gen = getattr(self, "_listen_gen", 0) + 1
        if self.stt is not None and self.stt.listening:
            self.stt.set_paused(True)

    def _resume_listen(self, token: int | None = None) -> None:
        if token is not None and token != getattr(self, "_listen_gen", 0):
            return
        if self.stt is not None and self.stt.listening:
            self.stt.set_paused(False)

    def _on_wake_heard(self) -> None:
        self._set_status("In a call")
        if self.avatar:
            self.avatar.play_animation("wave")
            self.avatar.set_activity("listening", "In a call")

    def _on_slept(self, reason: str) -> None:
        del reason
        self._set_status("Ready")
        if self.avatar:
            self.avatar.set_expression("happy", 1.0)
            self.avatar.set_activity("idle", "Ready")

    def _on_stt_level(self, value: float) -> None:
        if self.avatar:
            self.avatar.set_level(value)

    def _on_stt_recording(self, on: bool) -> None:
        if on:
            self._set_status(self._voice_idle_caption())
            if self.avatar:
                self.avatar.set_activity(
                    "listening" if self.stt is not None and self.stt.in_call else "idle",
                    self._voice_idle_caption(),
                )
        else:
            self._set_status("Microphone muted" if audio_settings().get("mic_muted") else "Ready")
            if self.avatar and audio_settings().get("mic_muted"):
                self.avatar.set_muted(True)

    def _on_stt_text(self, text: str) -> None:
        if not text:
            return
        self.send_message(text)

    def _on_stt_error(self, error: str) -> None:
        print(f"[stt] {error}", flush=True)
        self._set_status(error, error=True)
        if self.avatar:
            self.avatar.set_activity("error", error[:48])
        fatal = any(
            token in error.lower()
            for token in ("no microphone", "did not start", "whisper failed to load")
        )
        if fatal:
            self._set_mic_muted(True)
            self._set_in_call(False)

    def _open_workspace(self) -> None:
        folder = current_workspace()
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(["explorer", str(folder)])

    def _restart(self) -> None:
        from .self_modifier import restart_app

        self.status_label.setText("Restarting…")
        QTimer.singleShot(400, restart_app)

    def refresh_status(self) -> None:
        self._paint_health()
        if self._probe is not None and self._probe.isRunning():
            return
        probe = HealthProbe(self)
        self._probe = probe
        probe.probed.connect(self._on_health_probed)
        probe.finished.connect(lambda: self._clear_probe(probe))
        probe.start()

    def _warm_local(self) -> None:
        from .settings_store import brains_settings, resolve_chat_brain

        if not bool(brains_settings().get("local_enabled", True)):
            return
        host, model = resolve_chat_brain(getattr(self, "session", None))
        if host != "local":
            return
        if self._local_ok is True:
            return
        if self._local_warm is not None and self._local_warm.isRunning():
            return
        job = LocalWarmup(model, self)
        self._local_warm = job
        job.done.connect(self._on_local_warm)
        job.failed.connect(self._on_local_warm_failed)
        job.finished.connect(lambda: self._clear_local_warm(job))
        job.start()

    def _clear_local_warm(self, job: LocalWarmup) -> None:
        if self._local_warm is job:
            self._local_warm = None
        job.deleteLater()

    def _on_local_warm(self, msg: str) -> None:
        if msg and msg != "ready":
            self._set_status(msg)
        self.refresh_status()
        if hasattr(self, "lm_panel"):
            self.lm_panel.refresh()
        QTimer.singleShot(200, self._maybe_resume_self_modify)

    def _on_local_warm_failed(self, msg: str) -> None:
        text = str(msg or "")
        if "No GGUF" in text or "Local is off" in text:
            return
        self._set_status(text)

    def _maybe_resume_self_modify(self) -> None:
        """If we rebooted after editing Jinx source, auto-continue verification."""
        if getattr(self, "_self_modify_resume_done", False):
            return
        if self._worker is not None and self._worker.isRunning():
            QTimer.singleShot(800, self._maybe_resume_self_modify)
            return
        from .pending_restart import begin_verify_pass, format_resume_message, take_pending

        pending = take_pending()
        if not pending:
            return
        self._self_modify_resume_done = True
        begin_verify_pass(pending)
        message = format_resume_message(pending)
        self._set_status("Verifying self-modify…")
        self._start_turn(message, hidden=True)

    def _clear_probe(self, probe: HealthProbe) -> None:
        if self._probe is probe:
            self._probe = None
        probe.deleteLater()

    def _on_health_probed(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        self._local_ok = bool(data.get("local")) if "local" in data else None
        if "searxng" in data:
            raw_searx = data.get("searxng")
            self._searx_ok = None if raw_searx is None else bool(raw_searx)
        self._comfy_ok = bool(data.get("comfyui")) if "comfyui" in data else None
        if hasattr(self, "life"):
            self.life.set_hosts(self._local_ok, self._comfy_ok)
        self._paint_health()

    def _paint_health(self, local_ok: bool | None = None) -> None:
        if local_ok is not None:
            self._local_ok = local_ok
        mem_txt = "off"
        if self.memory is not None:
            if getattr(self.memory, "_failed", False):
                mem_txt = f"error: {getattr(self.memory, '_error', '')[:80]}"
            elif getattr(self.memory, "_ready", False):
                mem_txt = "ready"
            else:
                mem_txt = "loading…"
        if hasattr(self, "memory_status"):
            self.memory_status.setText(mem_txt)

        if not hasattr(self, "brain_status"):
            return
        from .comfyui import comfy_display_origin
        from .llama_server import server_origin
        from .searxng import searxng_urls

        lines = [
            f"{_dot(self._local_ok)} llama-server {server_origin()}",
            f"{_dot(True)} Search DuckDuckGo",
        ]
        origins = searxng_urls()
        if origins:
            lines.append(f"{_dot(self._searx_ok)} SearXNG {origins[0]}")
        lines.append(f"{_dot(self._comfy_ok)} ComfyUI {comfy_display_origin()}")
        if life_settings().get("enabled"):
            from .life import event_count, load_state

            st = load_state()
            n = event_count(str(st.get("day") or ""))
            asleep = st.get("phase") == "sleeping"
            lines.append(
                f"{_dot(True)} Life {'sleeping' if asleep else 'awake'} · {n} events today"
            )
        self.brain_status.setText("<br>".join(lines))

    def _apply_window_style(self) -> None:
        self.setStyleSheet(APP_QSS)

    def _apply_window_mode(self) -> None:
        flags = self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if FRAMELESS:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if TRANSLUCENT:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _fill_provider_combo(self, keep: str | None = None) -> None:
        if not hasattr(self, "provider_combo"):
            return
        keep = keep or str(self.provider_combo.currentData() or "")
        hosts = enabled_hosts()
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for host in hosts:
            self.provider_combo.addItem(host_label(host), host)
        idx = self.provider_combo.findData(keep)
        if idx < 0:
            idx = 0
        if self.provider_combo.count():
            self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.blockSignals(False)

    def _sync_provider_ui(self, kind: str) -> None:
        kind = kind if kind in set(enabled_hosts()) else (enabled_hosts()[0] if enabled_hosts() else "local")
        if hasattr(self, "provider_combo"):
            self.provider_combo.blockSignals(True)
            idx = self.provider_combo.findData(kind)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            self.provider_combo.blockSignals(False)
        host, model = resolve_chat_brain(getattr(self, "session", None))
        if kind != host:
            host, model = kind, model
        if hasattr(self, "provider_combo"):
            self.provider_combo.setToolTip(f"{host_label(kind)} ({model or 'pick a model'})")
        self._update_caps_label()

    def _caps_for_current(self) -> tuple[str, str, list[str], bool]:
        host, model = resolve_role_brain(
            self._agent_role(), getattr(self, "session", None)
        )
        caps: list[str] = []
        loaded = False
        snaps = {}
        if hasattr(self, "lm_panel"):
            snaps = self.lm_panel.current_snapshots()
        snap = snaps.get(host)
        if snap is not None:
            for item in snap.models:
                if item.name == model:
                    caps = list(item.capabilities)
                    loaded = bool(item.loaded)
                    break
            if not loaded and model in (snap.loaded_names or []):
                loaded = True
        return host, model, caps, loaded

    def _update_caps_label(self) -> None:
        if not hasattr(self, "caps_label"):
            return
        host, model, caps, loaded = self._caps_for_current()
        bits = format_capabilities(caps)
        if not model:
            self.caps_label.setText("No chat model yet — open Models and pick one.")
            return
        state = "loaded" if loaded else "selected"
        extra = f" · {bits}" if bits else ""
        self.caps_label.setText(
            f"{host_label(host)} / {model} · {state}{extra}"
        )

    def _on_lm_snapshots(self, _payload: object) -> None:
        self._update_caps_label()

    def _on_use_chat_model(self, host: str, model: str) -> None:
        save_brains_settings({"chat_host": host, "chat_model": model})
        if getattr(self, "session", None):
            self.session["provider"] = host
            self.session["model"] = model
            self._persist_session()
        self._fill_provider_combo(host)
        self._sync_provider_ui(host)
        if self._worker is not None:
            self._worker.set_provider(self._build_provider(host))
            self._set_status("Brain switches after this model round.")
        else:
            self._set_status(f"Chat model: {model}")

    def _on_use_image_model(self, host: str, model: str) -> None:
        del host, model
        self._update_caps_label()

    def _on_roles_changed(self) -> None:
        self._update_caps_label()
        if self._worker is not None:
            self._worker.set_provider(self._build_provider())
            self._set_status("Brain switches after this model round.")

    def _on_host_enabled(self, host: str, on: bool) -> None:
        del on
        current, _model = resolve_chat_brain(getattr(self, "session", None))
        if current == host and host not in enabled_hosts():
            fallback = enabled_hosts()[0]
            if getattr(self, "session", None):
                self.session["provider"] = fallback
                self._persist_session()
            current = fallback
        self._fill_provider_combo(current)
        self._sync_provider_ui(current)

    def _on_api_access_changed(self) -> None:
        if hasattr(self, "lm_panel"):
            self.lm_panel.refresh()
        current, _model = resolve_chat_brain(getattr(self, "session", None))
        self._fill_provider_combo(current)
        self._sync_provider_ui(current)

    def _agent_role(self) -> str:
        return "coding" if getattr(self, "_app_mode", "developer") == "developer" else "companion"

    def _coding_explicit(self) -> bool:
        from .settings_store import saved_role_brain

        host, model = saved_role_brain("coding")
        return bool(host and model)

    def _provider_from_brain(self, host: str, model: str) -> BaseProvider:
        if is_cloud_host(host):
            return make_cloud_provider(host, model)
        return LocalLlamaProvider(model=model, timeout=180)

    def _provider_for_role(self, role: str, kind: str | None = None) -> BaseProvider:
        session = getattr(self, "session", None)
        if kind and not (role == "coding" and self._coding_explicit()):
            session = dict(session or {})
            session["provider"] = kind
        host, model = resolve_role_brain(role, session)
        return self._provider_from_brain(host, model)

    def _build_provider(self, kind: str | None = None) -> BaseProvider:
        return self._provider_for_role(self._agent_role(), kind)

    def send_message(self, text: str | None = None) -> None:
        if isinstance(text, bool):
            text = None
        from_input = text is None
        body = (self.composer.text() if from_input else text or "").strip()
        if not body:
            return
        if from_input:
            self.composer.clear()
        if self._worker is not None:
            self._queue_text(body)
            return
        self._start_turn(body)

    def _on_composer_submit(self, body: str) -> None:
        if self._worker is not None:
            self._queue_text(body)
            return
        self._start_turn(body)

    def _on_composer_now(self, body: str) -> None:
        if self._worker is not None:
            self._steer_now(body)
            return
        self._start_turn(body)

    def _on_composer_call(self, on: bool) -> None:
        self._set_in_call(bool(on))

    def _queue_text(self, body: str) -> None:
        text = (body or "").strip()
        if not text:
            return
        self._follow_ups.append(text)
        self.composer.set_queue(self._follow_ups)
        n = len(self._follow_ups)
        self._set_status(f"Queued ({n}) — runs after this turn. Bolt / Ctrl+Enter sends now.")

    def _drop_follow_up(self, index: int) -> None:
        if 0 <= index < len(self._follow_ups):
            self._follow_ups.pop(index)
            self.composer.set_queue(self._follow_ups)

    def _start_turn(self, body: str, *, hidden: bool = False) -> None:
        body, template = expand_slash_command(body)
        if template:
            self._set_status(f"Expanded /{template}")
        if getattr(self, "session", None):
            append_turn(self.session, "user", body, hidden=bool(hidden))
            self._sync_history_from_tree()
        else:
            self.history.append({"role": "user", "content": body, "hidden": bool(hidden)})
        if not hidden:
            self._append_message("user", body)
            self._share_everyone_turn("user", body)
        self._persist_session()
        if hasattr(self, "transcript"):
            self.transcript.set_assistant_name(self._speaker_name())
        if hasattr(self, "life") and not hidden:
            self.life.touch_user()
            self.life.note("user", body)

        self._tools_this_turn = []
        self._stopped = False
        self._discard_live()
        self._pause_listen()
        self._set_busy(True)
        self._turn_started = time.monotonic()
        self._on_phase("Thinking", "")
        if not template:
            self._set_status("Verifying…" if hidden else "Thinking")
        if self.voice:
            self.voice.stop()
        if self.avatar:
            self.avatar.set_activity("thinking", "Thinking…")

        provider = self._build_provider()
        host, model, caps, _loaded = self._caps_for_current()
        if getattr(self, "session", None):
            worker_history = provider_history(self.session)
        else:
            worker_history = [
                {"role": msg["role"], "content": msg.get("content", "")}
                for msg in self.history
            ]
        project_id = str((self.session or {}).get("project_id") or "")
        bot_id = str((self.session or {}).get("bot_id") or "chief")
        if hasattr(self, "platform_page"):
            self.platform_page.set_presence(bot_id or "chief", "working")
        self._worker = ChatWorker(
            provider,
            worker_history,
            self.memory,
            MEMORY_TOP_K,
            tools_schema=tools_schema_for(
                self._app_mode, project_id=project_id, bot_id=bot_id
            ),
            prompt_mode=self._app_mode,
            model_note=loaded_model_note(host, model, caps),
            session_id=str((self.session or {}).get("id") or ""),
            workspace=str(current_workspace()),
            project_id=project_id,
            bot_id=bot_id,
        )
        self._worker.finished.connect(self._on_reply)
        self._worker.failed.connect(self._on_error)
        self._worker.tool_used.connect(self._on_tool_used)
        self._worker.tool_preview.connect(self._on_tool_preview)
        self._worker.token.connect(self._on_token)
        self._worker.phase.connect(self._on_phase)
        self._worker.tools_starting.connect(self._discard_live)
        self._worker.compacted.connect(
            lambda n: self._set_status(f"Compacted context (~{n} chars kept).")
        )
        self._worker.steered.connect(self._on_steered)
        self._worker.approval_needed.connect(self._on_approval_needed)
        self._worker.ask_user_needed.connect(self._on_ask_user_needed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_approval_needed(self, name: str, detail: str) -> None:
        if hasattr(self, "approval_bar"):
            self.approval_bar.ask(name, detail)
            self._on_phase("Waiting for approval", name)

    def _on_approval_decided(self, decision: str) -> None:
        if hasattr(self, "approval_bar"):
            self.approval_bar.clear()
        if self._worker is not None:
            self._worker.resolve_approval(decision)

    def _on_ask_user_needed(self, prompt: str, choices: list) -> None:
        if hasattr(self, "ask_user_bar"):
            self.ask_user_bar.ask(prompt, list(choices or []))
            self._on_phase("Waiting for you", (prompt or "")[:88])

    def _on_ask_user_answered(self, answer: str) -> None:
        if hasattr(self, "ask_user_bar"):
            self.ask_user_bar.clear()
        if self._worker is not None:
            self._worker.resolve_ask_user(answer)

    def _steer_now(self, body: str) -> None:
        if self._worker is None:
            return
        self._append_message("user", f"(steer) {body}")
        self._worker.steer(body)
        self._set_status("Steer queued — after the current tool.")

    def _on_steered(self, text: str) -> None:
        if getattr(self, "session", None):
            append_turn(self.session, "user", text)
            self._sync_history_from_tree()
        else:
            self.history.append({"role": "user", "content": text})
        self._persist_session()

    def _queue_follow_up(self) -> None:
        body = self.composer.text()
        if not body:
            return
        self.composer.clear()
        if self._worker is None:
            self._start_turn(body)
            return
        self._queue_text(body)

    def _stop_agent(self) -> None:
        if self._worker is None:
            abort_running_commands()
            reset_abort()
            return
        self._stopped = True
        self._follow_ups.clear()
        self.composer.set_queue([])
        self._worker.request_abort()
        self._set_status("Stopping…")

    def _on_token(self, delta: str) -> None:
        # Activity card only while she works. Do not type model tokens
        # (tool JSON, source dumps) into the transcript.
        return

    def _flush_live(self) -> None:
        if not hasattr(self, "transcript"):
            return
        nxt = next_reveal(self._live_shown, self._live_buf)
        if nxt == self._live_shown:
            if self._live_shown == self._live_buf and self._live_buf:
                self._live_timer.stop()
            return
        self._live_shown = nxt
        if len(self._live_shown) > 12:
            self.transcript.clear_activity()
        self.transcript.update_live(self._live_shown)

    def _discard_live(self) -> None:
        self._live_timer.stop()
        self._live_buf = ""
        self._live_shown = ""
        if hasattr(self, "transcript"):
            self.transcript.discard_live()

    def _finish_activity_row(self) -> dict:
        if not hasattr(self, "transcript"):
            return {}
        started = float(getattr(self, "_turn_started", 0) or 0)
        elapsed = (time.monotonic() - started) if started else 0.0
        return self.transcript.finish_activity(elapsed)

    def _on_phase(self, title: str, detail: str = "") -> None:
        if hasattr(self, "transcript"):
            self.transcript.set_activity(title, detail)
        label = title if not detail else f"{title} — {detail}"
        self._set_status(label)
        if self.avatar:
            kind = "thinking"
            low = (title or "").lower()
            if "search" in low or "reading" in low:
                kind = "tool"
            elif "image" in low:
                kind = "tool"
            self.avatar.set_activity(kind, title)

    def _reload_transcript(self, messages: list[Message]) -> None:
        self._discard_live()
        self.transcript.clear()
        visible = []
        for msg in messages:
            if msg.get("role") not in ("user", "assistant"):
                continue
            body = str(msg.get("content") or "")
            if msg.get("role") == "assistant":
                body = strip_wake_boilerplate(body)
            if not body:
                continue
            visible.append({**msg, "content": body})
        if not visible:
            if getattr(self, "_pending_kickoff", ""):
                return
            self._greet()
            return
        for msg in visible:
            if msg.get("role") == "assistant" and isinstance(msg.get("activity"), dict):
                self.transcript.add_frozen_activity(msg["activity"])
            self._append_message(
                str(msg["role"]),
                str(msg.get("content", "")),
                when=parse_when(msg.get("created_at")),
            )

    def _on_reply(self, reply: str) -> None:
        if hasattr(self, "approval_bar"):
            self.approval_bar.clear()
        if hasattr(self, "ask_user_bar"):
            self.ask_user_bar.clear()
        stopped = self._stopped
        self._stopped = False
        self._live_timer.stop()
        self._live_buf = ""
        self._live_shown = ""
        tools_used = bool(self._tools_this_turn)
        display, spoken = spoken_vs_chat(reply, tools_used=tools_used)
        display = visible_chat_reply(display, tools_used=tools_used)
        if stopped and not (display or "").strip():
            display = "[stopped]"
        if getattr(self, "session", None):
            node = append_turn(self.session, "assistant", display)
            snap = self._finish_activity_row()
            if snap and isinstance(node, dict):
                node["activity"] = snap
            self._sync_history_from_tree()
        else:
            self.history.append({"role": "assistant", "content": display})
            self._finish_activity_row()
        if (display or "").strip():
            self.transcript.finish_live(display)
        else:
            self.transcript.clear_activity()
        self._persist_session()
        if hasattr(self, "life"):
            self.life.note("assistant", display)
        self._share_everyone_turn("assistant", display)
        follow = None if stopped else (self._follow_ups.pop(0) if self._follow_ups else None)
        if hasattr(self, "composer"):
            self.composer.set_queue(self._follow_ups)
        if not stopped and self.voice and self.speak_toggle.isChecked() and spoken:
            self.voice.speak(spoken)
        else:
            if self.avatar:
                self.avatar.end_speech()
            QTimer.singleShot(300, lambda n=self._listen_gen: self._resume_listen(n))
        self._finish_request("Stopped" if stopped else "Ready")
        self._tools_this_turn = []
        if follow:
            QTimer.singleShot(50, lambda t=follow: self.send_message(t))

    def _on_error(self, error: str) -> None:
        if hasattr(self, "approval_bar"):
            self.approval_bar.clear()
        if hasattr(self, "ask_user_bar"):
            self.ask_user_bar.clear()
        self._discard_live()
        self._finish_activity_row()
        msg = friendly_provider_error(error, display_name="Local")
        if not msg.lower().startswith("error"):
            msg = f"Error: {msg}"
        self._append_message("error", msg)
        if self.avatar:
            self.avatar.set_activity("error", "Request failed")
            self.avatar.set_expression("chaotic", 1.5)
        self._finish_request("Request failed")
        self._tools_this_turn = []
        QTimer.singleShot(200, lambda n=self._listen_gen: self._resume_listen(n))

    def _on_searx_url(self) -> None:
        save_brains_settings({"searxng_url": self.searx_url.text().strip()})
        QTimer.singleShot(200, self.refresh_status)

    def _on_speech_started(self, events: list) -> None:
        if self.avatar:
            self.avatar.start_speech(events)

    def _on_speech_progress(self, t: float) -> None:
        if self.avatar:
            self.avatar.set_speech_time(t)

    def _on_speech_finished(self) -> None:
        if self.avatar:
            self.avatar.end_speech()
            if self.stt is not None and self.stt.awake:
                self.avatar.set_activity("listening", "In a call")
            else:
                self.avatar.set_activity("idle", "Ready")
        from .life import load_state

        if self.avatar and load_state().get("phase") == "sleeping":
            self.avatar.set_activity("sleeping", "Sleeping")
        QTimer.singleShot(450, lambda n=self._listen_gen: self._resume_listen(n))

    def _ai_commit_message(self, diff: str) -> None:
        self._commit_worker = CommitWorker(self._build_provider(), diff)
        self._commit_worker.finished.connect(self.git_panel.set_commit_message)
        self._commit_worker.finished.connect(lambda: self._set_status("Commit message filled."))
        self._commit_worker.failed.connect(lambda e: self._set_status(str(e), error=True))
        self._commit_worker.start()

    def _on_tool_used(self, message: str) -> None:
        self._discard_live()
        self._tools_this_turn.append(message)
        if hasattr(self, "life"):
            self.life.note("tool", message)
        if self.avatar:
            self.avatar.set_activity("tool", message)

    def _on_tool_preview(self, name: str, args_preview: str, result_preview: str) -> None:
        if hasattr(self, "transcript"):
            self.transcript.set_tool_preview(name, args_preview, result_preview)

    def _ensure_session_project(self) -> None:
        if not getattr(self, "session", None):
            return
        pid = str(self.session.get("project_id") or "").strip()
        if pid:
            from .sessions import bind_project_computer

            bind_project_computer(self.session, pid)
        if pid and not str(self.session.get("bot_id") or "").strip():
            self.session["bot_id"] = "chief"
            self._persist_session()

    def _persist_project_computer(self) -> None:
        pid = str((getattr(self, "session", None) or {}).get("project_id") or "").strip()
        if not pid:
            return
        from .platform.registry import get_project, save_project

        project = get_project(pid)
        if project is None:
            return
        folders = [str(path) for path in current_folders()]
        if list(project.folders) == folders:
            return
        project.folders = folders
        save_project(project)

    def _on_bot_opened(self, bot_id: str) -> None:
        if not getattr(self, "session", None):
            return
        pid = str(self.session.get("project_id") or "").strip()
        if not pid:
            return
        self._persist_session()
        self.session = open_project_thread(pid, bot_id or "chief")
        self._bind_session_files()
        self._sync_history_from_tree()
        self._reload_transcript(self.history)
        self._refresh_session_combo(self.session.get("id"))
        self._apply_workspace_ui()
        self._sync_project_ui()
        from .platform.registry import get_bot

        bid = str(self.session.get("bot_id") or "chief")
        if bid.lower() == "chief":
            self._set_status("Everyone — group chat with Chief of Staff.")
            return
        bot = get_bot(bid)
        name = bot.name if bot else bid
        self._set_status(f"1:1 with {name}.")

    def _open_computer(self) -> None:
        self._set_activity("files")
        self._set_status("Computer — Files / Terminal. Project tab to return.")

    def _open_project(self, project_id: str) -> None:
        from .platform.registry import get_project

        project = get_project(project_id)
        if project is None:
            self._set_status("Unknown project.")
            return
        if getattr(self, "session", None):
            self._persist_session()
        self.session = open_project_thread(project.id, "chief", title=project.name)
        self._bind_session_files()
        self._sync_history_from_tree()
        self._reload_transcript(self.history)
        self._refresh_session_combo(self.session.get("id"))
        self._apply_workspace_ui()
        self._sync_project_ui()
        self._set_activity("teams")
        self._set_status(f"Project: {project.name}")

    def _new_project(self) -> None:
        from .platform_dialogs import ProjectDialog
        from .platform.registry import save_project

        dlg = ProjectDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        project = dlg.result_project()
        if project is None:
            return
        kickoff = dlg.kickoff_text()
        save_project(project)
        self._pending_kickoff = kickoff
        self._open_project(project.id)
        self._pending_kickoff = ""
        if kickoff:
            QTimer.singleShot(0, lambda text=kickoff: self._start_turn(text))

    def _edit_project(self, project_id: str) -> None:
        from .platform_dialogs import ProjectDialog
        from .platform.registry import get_project, save_project

        project = get_project(project_id)
        if project is None:
            return
        dlg = ProjectDialog(project, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.result_project()
        if updated is None:
            return
        save_project(updated)
        if hasattr(self, "home_page"):
            self.home_page.reload()
        current = str((self.session or {}).get("project_id") or "")
        if current == updated.id:
            bid = str(self.session.get("bot_id") or "chief")
            self.session = open_project_thread(updated.id, bid)
            self._apply_workspace_ui()
            self._sync_project_ui()

    def _delete_project(self, project_id: str) -> None:
        from .platform.registry import delete_project, get_project

        project = get_project(project_id)
        label = project.name if project else project_id
        if QMessageBox.question(
            self, "Delete project", f"Remove {label} from Home?"
        ) != QMessageBox.StandardButton.Yes:
            return
        current = str((self.session or {}).get("project_id") or "")
        delete_project(project_id)
        if current == project_id and getattr(self, "session", None):
            self.session.pop("project_id", None)
            self.session.pop("bot_id", None)
            self._persist_session()
            self._sync_project_ui()
            self._set_activity("home")
        if hasattr(self, "home_page"):
            self.home_page.reload()

    def _new_team(self) -> None:
        self._edit_team("")

    def _edit_team(self, team_id: str) -> None:
        from .platform_dialogs import TeamDialog
        from .platform.registry import get_team, save_team

        team = get_team(team_id) if team_id else None
        dlg = TeamDialog(team, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.result_team()
        if updated is None:
            return
        save_team(updated)
        if hasattr(self, "home_page"):
            self.home_page.reload()

    def _delete_team(self, team_id: str) -> None:
        from .platform.registry import delete_team, get_team

        team = get_team(team_id)
        label = team.name if team else team_id
        if QMessageBox.question(
            self, "Delete team", f"Remove {label}? Members stay in the catalog."
        ) != QMessageBox.StandardButton.Yes:
            return
        delete_team(team_id)
        if hasattr(self, "home_page"):
            self.home_page.reload()
        if hasattr(self, "platform_page"):
            self.platform_page.reload()

    def _new_member(self) -> None:
        self._edit_member("")

    def _edit_member(self, bot_id: str) -> None:
        from .platform_dialogs import BotDialog
        from .platform.registry import get_bot, save_bot

        bot = get_bot(bot_id) if bot_id else None
        dlg = BotDialog(bot, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.result_bot()
        if updated is None:
            return
        save_bot(updated)
        if hasattr(self, "members_page"):
            self.members_page.reload()
        if hasattr(self, "home_page"):
            self.home_page.reload()

    def _delete_member(self, bot_id: str) -> None:
        from .platform.registry import delete_bot, get_bot

        bot = get_bot(bot_id)
        label = bot.name if bot else bot_id
        if QMessageBox.question(
            self, "Delete member", f"Remove {label} from the catalog?"
        ) != QMessageBox.StandardButton.Yes:
            return
        delete_bot(bot_id)
        if hasattr(self, "members_page"):
            self.members_page.reload()
        if hasattr(self, "home_page"):
            self.home_page.reload()

    def _project_add_team(self) -> None:
        from .platform_dialogs import PickListDialog
        from .platform.registry import get_project, load_registry, save_project

        pid = str((self.session or {}).get("project_id") or "")
        project = get_project(pid)
        if project is None:
            return
        registry = load_registry()
        labels = [
            (team.id, team.name)
            for team in registry.teams.values()
            if team.id not in project.team_ids
        ]
        if not labels:
            self._set_status("No more teams to add. Create one on Home.")
            return
        dlg = PickListDialog("Add team", labels, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        tid = dlg.selected_id()
        if not tid:
            return
        project.team_ids.append(tid)
        save_project(project)
        self._sync_project_ui()

    def _project_add_bot(self) -> None:
        from .platform_dialogs import PickListDialog
        from .platform.registry import get_project, load_registry, save_project

        pid = str((self.session or {}).get("project_id") or "")
        project = get_project(pid)
        if project is None:
            return
        registry = load_registry()
        assigned = {bot.id for bot in registry.project_bots(project)}
        labels = [
            (bot.id, f"{bot.name} ({bot.role})")
            for bot in registry.bots.values()
            if bot.id not in assigned
        ]
        if not labels:
            self._set_status("No more members to add. Create one under Team Members.")
            return
        dlg = PickListDialog("Add bot", labels, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        bid = dlg.selected_id()
        if not bid:
            return
        project.bot_ids.append(bid)
        save_project(project)
        self._sync_project_ui()

    def _sync_project_ui(self) -> None:
        pid = str((getattr(self, "session", None) or {}).get("project_id") or "")
        bid = str((getattr(self, "session", None) or {}).get("bot_id") or "chief")
        if hasattr(self, "platform_page"):
            self.platform_page.set_active_project(pid)
            self.platform_page.set_active_bot(bid)
        self._update_input_placeholder()
        self._refresh_composer_harness()
        if hasattr(self, "transcript"):
            self.transcript.set_assistant_name(self._speaker_name())
        self._sync_chrome()

    def _finish_request(self, status: str) -> None:
        if hasattr(self, "transcript"):
            self.transcript.clear_activity()
        self._worker = None
        self._set_busy(False)
        self._set_status(status)
        if hasattr(self, "platform_page"):
            bid = str((getattr(self, "session", None) or {}).get("bot_id") or "chief")
            self.platform_page.set_presence(bid or "chief", "idle")
        if self.avatar and not status.lower().startswith("request"):
            from .life import load_state

            if load_state().get("phase") == "sleeping":
                self.avatar.set_activity("sleeping", "Sleeping")
            else:
                live = self.stt is not None and self.stt.listening
                if live and self.stt.awake:
                    self.avatar.set_activity("listening", "In a call")
                elif live:
                    self.avatar.set_activity("idle", "Ready")
                elif audio_settings().get("mic_muted"):
                    self.avatar.set_muted(True)
        if not (self.stt is not None and self.stt.listening):
            self.input.setFocus()

    def _set_busy(self, busy: bool) -> None:
        if hasattr(self, "composer"):
            self.composer.set_busy(busy)
        if hasattr(self, "life"):
            self.life.set_busy(busy)
            overlay = "thinking" if busy else "idle"
            if self.avatar:
                overlay = str(getattr(self.avatar.orb, "activity", "") or overlay)
            self.life.set_overlay(overlay)
        if not busy:
            self._update_input_placeholder()
        if hasattr(self, "session_combo"):
            self.session_combo.setEnabled(not busy)

    def _speaker_name(self) -> str:
        bid = str((getattr(self, "session", None) or {}).get("bot_id") or "chief").strip()
        pid = str((getattr(self, "session", None) or {}).get("project_id") or "").strip()
        if not pid:
            return "Jinx AI"
        if not bid or bid.lower() == "chief":
            from .platform.registry import load_registry

            name = str(load_registry().chief.name or "").strip()
            if name and name.lower() not in {"chief", "jinx", "jinx ai"}:
                return name
            return "Chief of Staff"
        from .platform.registry import get_bot

        bot = get_bot(bid)
        return bot.name if bot else bid

    def _share_everyone_turn(self, role: str, text: str) -> None:
        """Everyone messages become project memory so specialists see the brief."""
        if not getattr(self, "session", None):
            return
        if str(self.session.get("bot_id") or "chief").lower() != "chief":
            return
        pid = str(self.session.get("project_id") or "").strip()
        if not pid:
            return
        blob = str(text or "").strip()
        if not blob:
            return
        from .platform.registry import get_project, save_project

        project = get_project(pid)
        if project is None:
            return
        if role == "user" and not str(project.state.get("kickoff") or "").strip():
            project.state["kickoff"] = blob[:8000]
        project.remember(blob, role=role, bot_id="chief")
        save_project(project)

    def _append_message(self, role: str, text: str, **kwargs: object) -> None:
        if role == "assistant" and "speaker" not in kwargs:
            kwargs["speaker"] = self._speaker_name()
        self.transcript.add(role, text, **kwargs)

    def closeEvent(self, event) -> None:  # noqa: N802
        if hasattr(self, "life"):
            self.life.stop()
        self._persist_session()
        super().closeEvent(event)
