"""Headless chat controller: sessions, turns, follow-ups, approvals, live events.

This is the logic that used to live inside the desktop ``ChatWindow`` (turn
start/finish, the Cat Crew feed, mirroring a specialist's work into its own
1:1 thread, follow-up queueing, steering, approvals, project reset), with the
widgets removed and every UI update turned into an event-bus event that the API
forwards over ``/ws/events``.

Rules the code below keeps:

* **One turn at a time.** The workspace and the tool allow-list are
  process-global (as they always were), so a second message while a turn runs
  is queued and started right after the first finishes.
* **Lock discipline.** ``self._lock`` guards turn state only and is *never*
  held while publishing an event, touching ``activity_board`` (which
  publishes), or doing disk I/O. Bus subscribers run on the emitting thread
  inside the bus lock, so publishing under our lock could deadlock.
* **Read paths never mutate global state.** Listing threads/messages does not
  move the "active session" pointer or the process workspace, so the API can
  answer while a turn is running.
* **Context isolation.** A direct turn receives only its own thread's history;
  Cat Crew specialists are run by the SOP with scoped contexts. Nothing
  here hands one cat another cat's conversation.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.activity import visible_chat_reply
from backend.agent_context import expand_slash_command
from backend.config import MEMORY_ENABLED, MEMORY_TOP_K
from backend.core import provider_factory
from backend.core.activity_recorder import ActivityRecorder
from backend.core.agent_loop import RoomRunner, TurnListener, TurnRunner
from backend.core.messages import error_message, node_to_message, session_messages
from backend.platform import activity_board, bus
from backend.platform import handoff
from backend.platform.orchestrator import (
    BUG,
    BUG_REASON,
    CHAT,
    HANDOFF_REASON,
    MENTION,
    ROOM_ID,
    ROOM_NAME,
    Intent,
    classify_intent,
    is_room_id,
)
from backend.platform.registry import get_bot, get_project, load_registry, save_project, save_registry
from backend.providers.openai_compat import friendly_provider_error
from backend.services.lm_runtime import loaded_model_note
from backend.services.session_tree import append_turn, ensure_tree, provider_history
from backend.services.sessions import (
    load_session,
    new_session,
    project_thread_ids,
    save_session,
    use_active_session,
)
from backend.services.workspace import current_workspace
from backend.tools import tools_schema_for

ROOM = ROOM_ID  # the Cat Crew (orchestrator) thread id; legacy "chief" is folded into it

# Cat Crew SOP phase -> the status word a card may show.
_PHASE_STATUS_WORDS = {
    "blueprint": "thinking",
    "architecture": "thinking",
    "distribution": "thinking",
    "devops": "coding",
    "build": "coding",
    "design": "coding",
    "qa": "testing",
    "qa-fix": "testing",
    "fixplan": "thinking",
    "fix": "coding",
    "fixreport": "thinking",
    "mention": "chatting",
    "spontaneous": "chatting",
    "done": "idle",
}
_PHASE_LABELS = {
    "blueprint": "Blueprint",
    "architecture": "Phase 1: Architecture",
    "distribution": "Phase 2: Distribution",
    "devops": "Phase 3: DevOps",
    "build": "Phase 4: Build",
    "design": "Phase 5: Design",
    "qa": "Phase 6: QA",
    "qa-fix": "Phase 6: QA (fix)",
    "fixplan": "Fix plan",
    "fix": "Fix",
    "fixreport": "Fix report",
    "mention": "Direct mention",
    "spontaneous": "Spontaneous",
    "done": "Done",
}


def status_word_for_phase(phase: str) -> str:
    return _PHASE_STATUS_WORDS.get((phase or "").strip().lower(), "chatting")


def phase_label(phase: str) -> str:
    phase = (phase or "").strip().lower()
    return _PHASE_LABELS.get(phase, phase.title() if phase else "")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ChatError(Exception):
    """A request the service refuses; ``status`` maps onto the HTTP status."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Turn:
    id: str
    project_id: str
    bot_id: str
    kind: str  # "room" (the Cat Crew thread) | "direct" (a cat's own thread)
    started_at: str
    session: dict[str, Any]
    # How the turn runs: "chat" (Cat Crew talks to the primary model), "bug" (problem report: lead plan, fixes, QA), "work" (specialists via
    # the SOP), "mention" (a named cat, via the SOP) or "direct". Set when the turn starts.
    mode: str = ""
    reporter: str = ""  # bug turns: the cat in whose chat the problem was reported ("" = Cat Crew / the user)
    text: str = ""  # the message as sent, and how it was routed: what a retry runs again
    intent: Any = None
    runner: Any = None
    phase: str = ""
    stopped: bool = False
    tools_used: list[str] = field(default_factory=list)
    recorders: dict[str, ActivityRecorder] = field(default_factory=dict)
    last_room_node: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "bot_id": self.bot_id,
            "started_at": self.started_at,
            "phase": self.phase,
            "kind": self.kind,
            "mode": self.mode,
        }


class _Pending:
    def __init__(self, kind: str, turn_id: str) -> None:
        self.kind = kind
        self.turn_id = turn_id
        self.event = threading.Event()
        self.value = ""


class ChatService:
    def __init__(self, *, use_memory: bool | None = None) -> None:
        self._lock = threading.RLock()
        self._turn: Turn | None = None
        self._follow_ups: list[tuple[str, str, str, Intent | None]] = []  # (project_id, bot_id, text, intent)
        self._pending: dict[str, _Pending] = {}
        #: project -> (bot_id, text, intent) of the last turn a blocked model failed: what retry() runs.
        self._retry: dict[str, tuple[str, str, Any]] = {}
        self._rec_lock = threading.Lock()  # guards Turn.recorders (bus thread + turn thread)
        self._memory = None
        self._memory_tried = False
        self._use_memory = MEMORY_ENABLED if use_memory is None else use_memory
        self._unsubscribe = bus.get_bus().subscribe(self._on_bus_event)

    # ------------------------------------------------------------------ state ----
    def current_turn(self) -> dict[str, Any] | None:
        with self._lock:
            return self._turn.public() if self._turn else None

    def is_busy(self) -> bool:
        with self._lock:
            return self._turn is not None

    def queue_length(self) -> int:
        with self._lock:
            return len(self._follow_ups)

    def close(self) -> None:
        """Stop the running turn (server shutdown)."""
        with self._lock:
            turn = self._turn
            self._follow_ups.clear()
        if turn is not None and turn.runner is not None:
            turn.stopped = True
            turn.runner.request_abort()
        self._unsubscribe()

    # -------------------------------------------------------------- read models ----
    def list_threads(self, project_id: str) -> list[dict[str, Any]]:
        project = self._project(project_id)
        ids = project_thread_ids(project.id)
        registry = load_registry()
        members: list[str] = [ROOM]
        for bid in list(project.bot_ids):
            if bid not in members:
                members.append(bid)
        try:
            from backend.db import ledger

            for bid in ledger.list_project_members(project.id):
                if bid not in members:
                    members.append(bid)
        except Exception:  # noqa: BLE001
            pass
        for bid in ids:  # a thread for a bot that has since left the roster stays reachable
            if bid not in members:
                members.append(bid)
        out: list[dict[str, Any]] = []
        for bid in members:
            sid = ids.get(bid)
            session = load_session(sid) if sid else None
            msgs = session_messages(session, limit=10_000) if session else []
            if bid == ROOM:
                title = ROOM_NAME
            else:
                bot = registry.bots.get(bid)
                title = bot.name if bot else bid
            out.append(
                {
                    "bot_id": bid,
                    "title": title,
                    "message_count": len(msgs),
                    "last_message_at": msgs[-1]["created_at"] if msgs else None,
                }
            )
        return out

    def list_messages(self, project_id: str, bot_id: str, limit: int = 200) -> list[dict[str, Any]]:
        project = self._project(project_id)
        bid = self._bot_id(bot_id)
        sid = project_thread_ids(project.id).get(bid)
        session = load_session(sid) if sid else None
        if session is None:
            return []
        return session_messages(session, limit=limit, bot_id=bid)

    # ---------------------------------------------------------------- commands ----
    def submit(
        self, project_id: str, bot_id: str, text: str, intent: Intent | None = None
    ) -> dict[str, Any]:
        """Send ``text`` to a thread. Starts a turn now, or queues behind the running one.

        A message to Cat Crew is routed by ``classify_intent`` (free chat, work for the
        specialists, or an @mention); ``intent`` lets an internal caller (a hand-off from
        a chat turn) skip the classifier."""
        project = self._project(project_id)
        bid = self._bot_id(bot_id)
        body = (text or "").strip()
        if not body:
            raise ChatError("Message is empty.", 422)
        with self._lock:
            if self._turn is not None:
                self._follow_ups.append((project.id, bid, body, intent))
                return {"turn_id": None, "queued": True, "position": len(self._follow_ups)}
            # Claim the slot before releasing the lock so two concurrent POSTs
            # cannot both start a turn.
            claim = Turn(
                id=f"turn_{uuid.uuid4().hex[:12]}",
                project_id=project.id,
                bot_id=bid,
                kind="room" if bid == ROOM else "direct",
                started_at=_iso_now(),
                session={},
                text=body,
                intent=intent,
            )
            self._turn = claim
        try:
            from backend.harness import model_block

            model_block.clear(project.id)  # a new run starts with no blocked model
            self._start(claim, body, intent)
        except Exception:
            with self._lock:
                if self._turn is claim:
                    self._turn = None
            raise
        return {"turn_id": claim.id, "queued": False}

    def report_problem(self, project_id: str, bot_id: str, text: str) -> dict[str, Any]:
        """Report a problem with a cat's work (or, from Cat Crew, with the project).

        The report always runs as one bug turn in the Cat Crew room, so the whole
        activity is visible there: the team lead writes a fix plan, the cats fix
        it, QA verifies. Each specialist's step is also mirrored into its own
        thread. When the report is made in a cat's chat, the message is added to
        that thread too and the cat is recorded as the reporter (the lead reads
        that cat's recent work first)."""
        project = self._project(project_id)
        bid = self._bot_id(bot_id)
        body = (text or "").strip()
        if not body:
            raise ChatError("Report is empty.", 422)
        reporter = "" if bid == ROOM else bid
        if reporter:
            label = self._speaker(reporter)
            body = f"Problem report for {label}:\n{body}"
            try:
                session = self._thread_session(project.id, reporter, title=label)
                node = append_turn(session, "user", body)
                save_session(session, activate=False)
                self._publish_message(project.id, reporter, node_to_message(node))
            except Exception:  # noqa: BLE001 - the report itself must still run
                pass
        else:
            body = f"Problem report:\n{body}"
        return self.submit(
            project.id, ROOM, body, Intent(BUG, None, BUG_REASON, reporter=reporter)
        )

    def abort(self, turn_id: str) -> bool:
        with self._lock:
            turn = self._turn
            if turn is None or turn.id != turn_id:
                return False
            self._follow_ups.clear()
            turn.stopped = True
            runner = turn.runner
        if runner is not None:
            runner.request_abort()
        return True

    def steer(self, turn_id: str, text: str) -> bool:
        body = (text or "").strip()
        if not body:
            raise ChatError("Steer text is empty.", 422)
        with self._lock:
            turn = self._turn
            if turn is None or turn.id != turn_id or turn.runner is None:
                return False
            runner = turn.runner
        # Queued now; it enters the transcript (chat.message) when the runner
        # actually takes it, right after the current tool / at the next phase.
        runner.steer(body)
        return True

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        decision = (decision or "").strip().lower()
        if decision not in ("allow", "allow_always", "deny"):
            raise ChatError("decision must be allow, allow_always or deny.", 422)
        with self._lock:
            pending = self._pending.get(approval_id)
        if pending is None or pending.kind != "approval":
            return False
        pending.value = decision
        pending.event.set()
        return True

    def resolve_ask(self, ask_id: str, answer: str) -> bool:
        with self._lock:
            pending = self._pending.get(ask_id)
        if pending is None or pending.kind != "ask":
            return False
        pending.value = str(answer or "")
        pending.event.set()
        return True

    def reset_project(self, project_id: str) -> None:
        """Wipe the Cat Crew's chat + shared state and put every bot back to idle.

        The project's roster, folders and the specialists' own 1:1 threads are
        untouched (same contract as the desktop "Reset Project")."""
        if self.is_busy():
            raise ChatError("Stop the current turn before resetting.", 409)
        project = self._project(project_id)
        registry = load_registry()
        project = registry.projects.get(project.id) or project
        project.state.clear()
        project.events.clear()
        save_registry(registry)
        sid = project_thread_ids(project.id).get(ROOM)
        session = load_session(sid) if sid else None
        if session is not None:
            session["messages"] = []
            session["head"] = None
            session["bot_activities"] = {}
            ensure_tree(session)
            save_session(session, activate=False)
        member_ids = {b.id for b in registry.project_bots(project) if b.id}
        member_ids.add(ROOM)
        for bid in member_ids:
            activity_board.mark_idle(bid)
        bus.emit_project_changed(project.id)

    # ------------------------------------------------------------------ internals ----
    def _project(self, project_id: str):
        project = get_project(str(project_id or "").strip())
        if project is None:
            raise ChatError(f"Unknown project {project_id!r}.", 404)
        return project

    def _bot_id(self, bot_id: str) -> str:
        bid = str(bot_id or ROOM).strip() or ROOM
        if is_room_id(bid):
            return ROOM
        if get_bot(bid) is None:
            raise ChatError(f"Unknown agent {bot_id!r}.", 404)
        return bid

    def _speaker(self, bot_id: str) -> str:
        if is_room_id(bot_id):
            return ROOM_NAME
        bot = get_bot(bot_id)
        return bot.name if bot else bot_id

    def _memory_or_none(self):
        if not self._use_memory:
            return None
        with self._lock:
            if self._memory_tried:
                return self._memory
            self._memory_tried = True
        try:
            from backend.services.memory import Mem0Memory

            memory = Mem0Memory()
            memory.start_background()
            self._memory = memory
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] Failed to start mem0: {exc}")
            self._memory = None
        return self._memory

    def _thread_session(self, project_id: str, bot_id: str, *, title: str | None = None) -> dict[str, Any]:
        """Load (or create) the session of ``(project, bot)``.

        Never touches the active-session pointer or the process workspace."""
        sid = project_thread_ids(project_id).get(bot_id)
        session = load_session(sid) if sid else None
        if session is None:
            session = new_session(title=title, activate=False)
            session["project_id"] = project_id
            session["bot_id"] = bot_id
        project = get_project(project_id)
        folders = [str(p) for p in (project.folders if project else []) if p]
        session["folders"] = folders
        session["workspace"] = folders[0] if folders else ""
        save_session(session, activate=False)
        return session

    def _publish_message(self, project_id: str, thread: str, message: dict[str, Any]) -> None:
        bus.publish(bus.CHAT_MESSAGE, {"thread": thread, "message": message}, project_id=project_id)

    def _share_room_turn(self, project_id: str, role: str, text: str) -> None:
        """Cat Crew messages become project memory so specialists see the
        brief (bot turns themselves are remembered by ``platform/sop.py``)."""
        blob = str(text or "").strip()
        if not blob:
            return
        project = get_project(project_id)
        if project is None:
            return
        if role == "user" and not str(project.state.get("kickoff") or "").strip():
            project.state["kickoff"] = blob[:8000]
        project.remember(blob, role=role, bot_id=ROOM)
        save_project(project)

    # -- turn start ------------------------------------------------------------------
    def _start(self, turn: Turn, body: str, intent: Intent | None = None) -> None:
        """Begin ``turn`` (its slot is already claimed)."""
        project_id, bot_id = turn.project_id, turn.bot_id
        body, _template = expand_slash_command(body)
        session = self._thread_session(project_id, bot_id, title=self._speaker(bot_id))
        turn.session = session
        # Point the process at this turn's session: tools resolve paths against
        # its folders, and per-chat scratch (todo board, logs) keys off the
        # active session. Safe: only one turn ever runs, and the slot is ours.
        use_active_session(session)
        roles: tuple[str, ...] | None = None
        if turn.kind == "room":
            # Cat Crew classifies every message: a question or remark stays a free chat with
            # the primary model; work goes to the specialists, no matrix permission involved.
            if intent is None:
                from backend.platform.sop import parse_mention

                intent = classify_intent(
                    body, has_mention=parse_mention(body, load_registry()) is not None
                )
            turn.mode = "chat" if intent.kind == CHAT else ("mention" if intent.kind == MENTION else "work")
            if intent.kind == BUG:
                turn.mode = "bug"
                turn.reporter = str(intent.reporter or "").strip().lower()
            roles = intent.roles
        else:
            turn.mode = "direct"

        # A hand-off brief is Cat Crew's own instruction to the cats, not something the human
        # typed: it is not shown (or stored) as a user message.
        if not (intent is not None and intent.reason == HANDOFF_REASON):
            node = append_turn(session, "user", body)
            save_session(session, activate=False)
            self._publish_message(project_id, bot_id, node_to_message(node))
        if bot_id == ROOM:
            self._share_room_turn(project_id, "user", body)

        rec = ActivityRecorder()
        with self._rec_lock:
            turn.recorders[bot_id] = rec

        # Cat Crew's free conversation runs on the Chat role; work and 1:1 cat chats on Coding.
        model_role = "companion" if turn.mode == "chat" else "coding"
        provider = provider_factory.provider_for_turn(
            session, project_id, room=(turn.kind == "room"), role=model_role
        )
        hooks = _Hooks(self, turn)
        if turn.kind == "room" and turn.mode != "chat":
            runner: Any = RoomRunner(
                provider,
                project_id,
                body,
                hooks,
                session_id=str(session.get("id") or ""),
                roles=roles,
                bug=(turn.mode == "bug"),
                reporter=(intent.reporter if intent is not None else ""),
            )
            turn.runner = runner
            self._announce_started(turn, "Thinking", "")
            activity_board.set_working(ROOM, "thinking", "Thinking…")
            runner.start(name=f"room-{project_id}")
            return
        host, model, caps, _loaded = provider_factory.caps_for_current(session, project_id, model_role)
        prompt_mode = "crew" if turn.kind == "room" else "developer"
        runner = TurnRunner(
            provider,
            provider_history(session),
            hooks,
            memory=self._memory_or_none(),
            top_k=MEMORY_TOP_K,
            # Cat Crew's chat is plain conversation: no clarify pass, no task board seeding.
            tools_schema=tools_schema_for(prompt_mode, project_id=project_id, bot_id=bot_id),
            prompt_mode=prompt_mode,
            model_note=loaded_model_note(host, model, caps),
            session_id=str(session.get("id") or ""),
            workspace=str(current_workspace()),
            project_id=project_id,
            bot_id=bot_id,
        )
        turn.runner = runner
        self._announce_started(turn, "Thinking", "")
        activity_board.set_working(bot_id, "thinking", "Thinking…")
        runner.start(name=f"turn-{bot_id}")

    def _announce_started(self, turn: Turn, title: str, detail: str) -> None:
        bus.publish(bus.TURN_STARTED, turn.public(), project_id=turn.project_id)
        self._record_step(turn, turn.bot_id, title, detail)

    # -- live activity steps ----------------------------------------------------------
    def _record_step(self, turn: Turn, thread: str, title: str, detail: str = "") -> None:
        with self._rec_lock:
            rec = turn.recorders.setdefault(thread, ActivityRecorder())
            step = rec.set_status(title, detail)
        if step is not None:
            self._publish_step(turn, thread, step)

    def _record_preview(
        self, turn: Turn, thread: str, name: str, args_preview: str, result_preview: str
    ) -> None:
        with self._rec_lock:
            rec = turn.recorders.setdefault(thread, ActivityRecorder())
            asked = _asked_step(name, args_preview, result_preview)
            step = rec.add_step(*asked) if asked else rec.set_tool_preview(name, args_preview, result_preview)
        if step is not None:
            self._publish_step(turn, thread, step)

    def _publish_step(self, turn: Turn, thread: str, step: dict[str, Any]) -> None:
        bus.publish(
            bus.CHAT_ACTIVITY,
            {
                "thread": thread,
                "turn_id": turn.id,
                "bot_id": thread,
                "step": {"n": step["n"], "line": step["line"], "preview": step["preview"]},
            },
            project_id=turn.project_id,
        )

    def _on_bus_event(self, event: bus.Event) -> None:
        """While a Cat Crew turn runs, mirror each specialist's live status
        line into that specialist's own thread (and the room's card) as steps,
        so an open 1:1 chat shows what the cat is doing *right now*."""
        if event.type != bus.BOT_ACTIVITY:
            return
        turn = self._turn  # racy read is fine: worst case one stale step is skipped
        if turn is None or turn.kind != "room":
            return
        data = event.data
        text = str(data.get("text") or "").strip()
        bot_id = str(data.get("bot_id") or "")
        if data.get("state") != "working" or not text or not bot_id or bot_id == ROOM:
            return
        label = phase_label(turn.phase) or "Working"
        self._record_step(turn, bot_id, text)
        self._record_step(turn, ROOM, label, f"{self._speaker(bot_id)} — {text}")

    # -- room callbacks -----------------------------------------------------------------
    def _room_phase_started(self, turn: Turn, phase: str, bot_id: str) -> None:
        turn.phase = phase or ""
        label = phase_label(phase) or "Working"
        word = status_word_for_phase(phase)
        bus.emit_phase_started(turn.project_id, phase, bot_id, label)
        bus.emit_cat_swapped(turn.project_id, bot_id, self._speaker(bot_id), phase, label, word.capitalize())
        activity_board.set_working(bot_id, word, f"{label}: working…")
        self._record_step(turn, ROOM, label, f"{self._speaker(bot_id)} is working…")

    def _room_bot_turn(
        self, turn: Turn, bot_id: str, text: str, tool_events: list[str], phase: str
    ) -> None:
        name = self._speaker(bot_id)
        display = text
        if tool_events:
            display = display + "\n\n" + "\n".join(f"· {line}" for line in tool_events)
        node = append_turn(turn.session, "assistant", display)
        node["speaker"] = name
        node["bot_id"] = bot_id
        node["phase"] = phase or ""
        if tool_events:
            node["tool_events"] = list(tool_events)
        turn.last_room_node = node
        save_session(turn.session, activate=False)
        self._publish_message(turn.project_id, ROOM, node_to_message(node, bot_id=bot_id))
        summary = " ".join(text.split())
        if len(summary) > 88:
            summary = summary[:85] + "…"
        activity_board.mark_idle(bot_id, summary or "Idle")
        if bot_id.strip().lower() not in ("", ROOM):
            self._mirror_to_bot_thread(turn, bot_id, name, phase, text, tool_events)
        if turn.mode == "bug" and phase == "fixplan":
            self._share_plan_with_reporter(turn, bot_id, name, text)
        bus.emit_project_changed(turn.project_id)

    def _mirror_to_bot_thread(
        self, turn: Turn, bot_id: str, name: str, phase: str, text: str, tool_events: list[str]
    ) -> None:
        """A specialist's Cat Crew work also lands in its own 1:1 thread as a
        normal assistant message carrying a frozen execution-card snapshot, so
        opening that cat's chat shows a step-by-step trace, not just a reply.
        Best-effort: never let a mirroring hiccup break the real turn."""
        try:
            session = self._thread_session(turn.project_id, bot_id, title=name)
            summary = " ".join(text.split())
            node = append_turn(session, "assistant", summary or "Idle")
            label = phase_label(phase) or "Working"
            steps = [{"line": f"{label}: {line}", "preview": ""} for line in (tool_events or [])]
            if not steps:
                steps = [{"line": label, "preview": ""}]
            node["activity"] = {"elapsed_s": 0.0, "steps": steps[-24:]}
            node["speaker"] = name
            node["bot_id"] = bot_id
            node["phase"] = phase or ""
            save_session(session, activate=False)
            self._publish_message(turn.project_id, bot_id, node_to_message(node, bot_id=bot_id))
        except Exception:  # noqa: BLE001
            pass

    def _share_plan_with_reporter(self, turn: Turn, bot_id: str, name: str, text: str) -> None:
        """The cat whose chat the problem was reported in gets the lead's fix plan there too, so it can
        read what will be done about its report without opening Cat Crew. Skipped when the lead is the
        reporter (its own thread already has the plan). Best-effort, like the other mirrors."""
        reporter = turn.reporter
        if not reporter or reporter == bot_id.strip().lower():
            return
        try:
            body = (text or "").strip()
            if len(body) > 1800:
                body = body[:1797] + "…"
            session = self._thread_session(turn.project_id, reporter, title=self._speaker(reporter))
            node = append_turn(session, "assistant", f"Fix plan for your report, from {name}:\n{body}")
            node["speaker"] = name
            node["bot_id"] = bot_id
            node["phase"] = "fixplan"
            save_session(session, activate=False)
            self._publish_message(turn.project_id, reporter, node_to_message(node, bot_id=bot_id))
        except Exception:  # noqa: BLE001
            pass

    # -- turn end ---------------------------------------------------------------------------
    def _direct_finished(self, turn: Turn, reply: str) -> None:
        stopped = turn.stopped
        # A Cat Crew chat turn may have asked for specialists (hand_off_to_crew); they start
        # as a normal work turn right after this reply, so every cat streams over the WebSocket.
        pending = handoff.take(turn.project_id) if turn.mode == "chat" else None
        display = visible_chat_reply(reply, tools_used=bool(turn.tools_used))
        if pending is None and turn.mode == "chat" and not stopped and promises_handoff(display):
            # "I'm ready to hand the implementation over to the developers" + a whole brief, and no
            # hand_off_to_crew call: nothing reached the cats. Take the model at its word.
            brief = f"{turn.text.strip()}\n\nCat Crew's brief for the crew:\n{display.strip()}".strip()
            pending = handoff.Handoff(brief[:12000], None)
            print("[crew] the reply promised a hand-off without calling hand_off_to_crew; handing off", flush=True)
        if stopped:
            pending = None
        if stopped and not (display or "").strip():
            display = "[stopped]"
        node = append_turn(turn.session, "assistant", display)
        node["speaker"] = self._speaker(turn.bot_id)
        node["bot_id"] = turn.bot_id
        with self._rec_lock:
            rec = turn.recorders.get(turn.bot_id)
            snap = rec.snapshot() if rec else {}
        if snap:
            node["activity"] = snap
        save_session(turn.session, activate=False)
        if (display or "").strip():
            self._publish_message(turn.project_id, turn.bot_id, node_to_message(node, bot_id=turn.bot_id))
        chain = None
        if pending is not None:
            chain = (turn.project_id, ROOM, pending.brief, Intent("work", pending.roles, HANDOFF_REASON))
        self._finish(turn, "aborted" if stopped else "ok", chain=chain)

    def _room_finished(self, turn: Turn) -> None:
        node, turn.last_room_node = turn.last_room_node, None
        with self._rec_lock:
            rec = turn.recorders.get(ROOM)
            snap = rec.snapshot() if rec else {}
        if snap and isinstance(node, dict):
            node["activity"] = snap
            save_session(turn.session, activate=False)
            # Same id as the bot's message -> the client upserts it with the card attached.
            self._publish_message(turn.project_id, ROOM, node_to_message(node, bot_id=str(node.get("bot_id") or "")))
        self._finish(turn, "aborted" if turn.stopped else "ok")

    def _failed(self, turn: Turn, error: str, *, block: Any = None) -> None:
        turn.last_room_node = None
        handoff.discard(turn.project_id)
        msg = friendly_provider_error(error, display_name="The model host")
        if not msg.lower().startswith("error"):
            msg = f"Error: {msg}"
        self._publish_message(turn.project_id, turn.bot_id, error_message(msg))
        payload: dict[str, Any] = {"turn_id": turn.id, "error": msg}
        if block is not None:
            # A model was turned away (rate limit / no credit): say which, where it is used, and
            # keep the message so "switch the model and retry" can run it again.
            with self._lock:
                self._retry[turn.project_id] = (turn.bot_id, turn.text, turn.intent)
            payload["model_issue"] = {**block.public(), "roles": _roles_using(turn.project_id, block.host, block.model)}
        bus.publish(bus.TURN_FAILED, payload, project_id=turn.project_id)
        self._finish(turn, None)

    def retry(self, project_id: str) -> dict[str, Any]:
        """Run again the last message that failed because its model was turned away."""
        with self._lock:
            saved = self._retry.pop(project_id, None)
        if saved is None:
            raise ChatError("Nothing to retry.", 404)
        bot_id, text, intent = saved
        return self.submit(project_id, bot_id, text, intent)

    def _finish(self, turn: Turn, status: str | None, *, chain: tuple | None = None) -> None:
        """Port of ``_finish_request``: free the slot, idle the bot, chain the next queued message.

        ``chain`` (a hand-off) runs before anything the user queued meanwhile."""
        bid = turn.bot_id or ROOM
        with self._lock:
            if self._turn is turn:
                self._turn = None
            follow = None
            if not turn.stopped and chain is not None:
                follow = chain
            elif not turn.stopped and self._follow_ups:
                follow = _collect_follow_up(self._follow_ups)
        with self._rec_lock:
            turn.recorders.clear()
        with self._lock:
            orphans = [p for p in self._pending.values() if p.turn_id == turn.id]
        for pending in orphans:
            pending.event.set()
        activity_board.mark_idle(bid)
        if status is not None:
            bus.publish(
                bus.TURN_FINISHED, {"turn_id": turn.id, "status": status}, project_id=turn.project_id
            )
        if follow is not None:
            try:
                self.submit(*follow)
            except Exception as exc:  # noqa: BLE001 - a bad queued message must not wedge the queue
                bus.publish(
                    bus.TURN_FAILED,
                    {"turn_id": "", "error": f"Queued message failed: {exc}"},
                    project_id=follow[0],
                )


def _collect_follow_up(queue: list[tuple[str, str, str, Intent | None]]) -> tuple[str, str, str, Intent | None]:
    """Pop the next queued message, folding in the ones queued right behind it for the same thread
    ("collect").

    Three quick messages typed into one chat while a turn runs used to become three turns, each
    re-reading the whole thread. Like OpenClaw's collect queue mode they now run as one turn, in
    the order they were typed. A message with its own routing (a hand-off, a problem report, a
    ``/template`` or an ``@mention``) is never merged, and neither is anything queued for another
    thread; both keep their place."""

    def mergeable(item: tuple[str, str, str, Intent | None]) -> bool:
        return item[3] is None and not item[2].lstrip().startswith(("/", "@"))

    project_id, bot_id, text, intent = queue.pop(0)
    if not mergeable((project_id, bot_id, text, intent)):
        return project_id, bot_id, text, intent
    parts = [text]
    rest: list[tuple[str, str, str, Intent | None]] = []
    for item in queue:
        if item[0] == project_id and item[1] == bot_id and mergeable(item) and not rest:
            parts.append(item[2])
        else:
            rest.append(item)
    queue[:] = rest
    return project_id, bot_id, "\n\n".join(parts), None


def _asked_step(name: str, args_preview: str, result_preview: str) -> tuple[str, str] | None:
    """``ask_user`` reads as a conversation, not as a tool call: "Asked you: <question>" with
    "You answered: <answer>" -- never the raw arguments object."""
    if name != "ask_user":
        return None
    import json

    try:
        args = json.loads(args_preview or "{}")
    except (TypeError, ValueError):
        args = {}  # the preview is cut at 400 characters: fall back to reading the field
    question = str((args or {}).get("prompt") or (args or {}).get("question") or "").strip()
    if not question:
        import re

        found = re.search(r'"(?:prompt|question)"\s*:\s*"((?:[^"\\]|\\.)*)', args_preview or "")
        question = found.group(1).strip() if found else "a question"
    answer = str(result_preview or "").strip()
    for prefix in ("User answered:", "The user answered:"):
        if answer.startswith(prefix):
            answer = answer[len(prefix):].strip()
    return f"Asked you: {question}", (f"You answered: {answer}" if answer else "No answer")


_PROMISED_HANDOFF = re.compile(
    r"\b(?:hand(?:ing)?\s+(?:it|this|the\s+\w+(?:\s+\w+)?)\s+(?:over\s+)?to\s+the\s+(?:crew|team|developers?|cats?|specialists)"
    r"|hand(?:ing)?\s+(?:it|this|the\s+\w+)\s+over"
    r"|ready\s+to\s+hand"
    r"|(?:i'?ll|i\s+will|let\s+me)\s+(?:get|have|ask|send)\s+the\s+(?:crew|team|cats|specialists)"
    r"|(?:passing|sending)\s+(?:it|this)\s+(?:on\s+)?to\s+the\s+(?:crew|team|cats))",
    re.IGNORECASE,
)


def promises_handoff(reply: str) -> bool:
    """A Cat Crew chat reply that says it is handing the work to the cats (not one that asks whether
    it should: that ends in a question)."""
    text = (reply or "").strip()
    return bool(text) and not text.endswith("?") and bool(_PROMISED_HANDOFF.search(text))


def _roles_using(project_id: str, host: str, model: str) -> list[dict[str, str]]:
    """The roles that run on ``host``/``model`` in this project (its own pick or the global one)."""
    from backend.services import model_resolver

    labels = {"chat": "Chat", "coding": "Coding", "memory": "Memory", "vision": "Vision", "planning": "Planning", "review": "Review"}
    out: list[dict[str, str]] = []
    for wire, label in labels.items():
        try:
            eff = model_resolver.get_effective_model(project_id, wire)
        except Exception:  # noqa: BLE001
            continue
        if eff.host == host and eff.model == model:
            out.append({"role": wire, "label": label, "source": eff.source})
    return out


class _Hooks(TurnListener):
    """Adapts a runner's callbacks onto the service (one instance per turn)."""

    def __init__(self, service: ChatService, turn: Turn) -> None:
        self.svc = service
        self.turn = turn

    # progress
    def on_phase(self, title: str, detail: str = "") -> None:
        self.svc._record_step(self.turn, self.turn.bot_id, title, detail)

    def on_tool_used(self, message: str) -> None:
        self.turn.tools_used.append(message)

    def on_tool_preview(self, name: str, args_preview: str, result_preview: str) -> None:
        self.svc._record_preview(self.turn, self.turn.bot_id, name, args_preview, result_preview)

    def on_steered(self, text: str) -> None:
        # The runner just took the queued steer text: it is now part of the
        # model's history (direct) or the next phase's context (room).
        node = append_turn(self.turn.session, "user", text)
        save_session(self.turn.session, activate=False)
        self.svc._publish_message(self.turn.project_id, self.turn.bot_id, node_to_message(node))

    # interaction
    def request_approval(self, name: str, detail: str, abort: threading.Event) -> str:
        return self._wait(
            "approval",
            {"tool": name, "detail": detail},
            (bus.APPROVAL_REQUESTED, bus.APPROVAL_RESOLVED),
            abort,
            default="deny",
            wait_title="Waiting for approval",
            wait_detail=name,
        )

    def request_ask(self, prompt: str, choices: list[str], abort: threading.Event) -> str:
        return self._wait(
            "ask",
            {"prompt": prompt, "choices": list(choices or [])},
            (bus.ASK_REQUESTED, bus.ASK_RESOLVED),
            abort,
            default="",
            wait_title="Waiting for you",
            wait_detail=(prompt or "")[:88],
        )

    def _wait(
        self,
        kind: str,
        payload: dict[str, Any],
        events: tuple[str, str],
        abort: threading.Event,
        *,
        default: str,
        wait_title: str,
        wait_detail: str,
    ) -> str:
        svc, turn = self.svc, self.turn
        from backend.platform.runtime import current_bot_id

        # In a work run the hook is called on the specialist's own thread, where run_bot bound its
        # id: the card then says "Pulse wants to use run_command", not "Cat Crew wants to…".
        asker = current_bot_id() or turn.bot_id
        rid = f"{'appr' if kind == 'approval' else 'ask'}_{uuid.uuid4().hex[:10]}"
        pending = _Pending(kind, turn.id)
        with svc._lock:
            svc._pending[rid] = pending
        svc._record_step(turn, turn.bot_id, wait_title, wait_detail)
        key = "approval_id" if kind == "approval" else "ask_id"
        bus.publish(
            events[0], {key: rid, "bot_id": asker, **payload}, project_id=turn.project_id
        )
        try:
            while not abort.is_set():
                if pending.event.wait(timeout=0.25):
                    break
            value = pending.value if pending.event.is_set() and pending.value else default
        finally:
            with svc._lock:
                svc._pending.pop(rid, None)
        resolved = {key: rid}
        if kind == "approval":
            resolved["decision"] = value or default
        bus.publish(events[1], resolved, project_id=turn.project_id)
        return value or default

    # results
    def on_finished(self, reply: str) -> None:
        from backend.harness import model_block

        block = model_block.take(self.turn.project_id)
        if block is not None and not self.turn.stopped:
            # The pipeline stopped early because a model was turned away: a failed turn, not a
            # finished one, with the offer to switch the model.
            self.svc._failed(self.turn, block.message, block=block)
            return
        if self.turn.kind == "room" and self.turn.mode != "chat":
            self.svc._room_finished(self.turn)
        else:
            self.svc._direct_finished(self.turn, reply)

    def on_failed(self, error: str) -> None:
        from backend.harness import model_block

        self.svc._failed(self.turn, error, block=model_block.take(self.turn.project_id))

    def on_bot_turn(self, bot_id: str, text: str, tool_events: list[str], phase: str) -> None:
        self.svc._room_bot_turn(self.turn, bot_id, text, tool_events, phase)

    def on_phase_started(self, phase: str, bot_id: str) -> None:
        self.svc._room_phase_started(self.turn, phase, bot_id)
