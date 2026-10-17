"""ChatService: direct turns, Cat Crew (chat + work) turns, queueing, abort, approvals, reset."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from backend.core import chat_service as cs
from backend.core.chat_service import ROOM, ChatError, ChatService, Turn
from backend.platform import activity_board, bus
from backend.platform.models import Project, new_id
from backend.platform.registry import load_registry, save_project
from backend.providers.base import AssistantMessage, BaseProvider, ToolCall


class FakeProvider(BaseProvider):
    """Scripted model: ``reply`` is text or a callable(messages) -> text."""

    display_name = "Fake"

    def __init__(self, reply: str | Callable[[list], str] = "Hello from the fake model.") -> None:
        self.reply = reply
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages, tools=None) -> AssistantMessage:
        self.calls.append(list(messages))
        text = self.reply(messages) if callable(self.reply) else self.reply
        return AssistantMessage(content=text)

    def tool_message(self, tool_call: ToolCall, result: str) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call.id or "", "content": result}


@pytest.fixture
def svc(recorder, monkeypatch):
    """A service whose provider factory yields ``svc.provider`` (no network, no LM Studio)."""
    service = ChatService(use_memory=False)
    service.provider = FakeProvider()
    monkeypatch.setattr(cs.provider_factory, "provider_for_turn", lambda *a, **k: service.provider)
    monkeypatch.setattr(cs.provider_factory, "caps_for_current", lambda session, *_a, **_k: ("lmstudio", "fake", [], True))
    yield service
    service.close()


@pytest.fixture
def project(isolated):
    from backend.api.app import _startup_data
    from backend.db import ledger

    _startup_data()  # schema + the 8 core cats, exactly as the server does at boot

    ws = isolated / "workspace"
    ws.mkdir()
    p = Project(id=new_id("proj"), name="Room Test", folders=[str(ws)])
    save_project(p)
    ledger.ensure_project(p.id, p.name, str(ws))
    return p


def wait_for(pred: Callable[[], bool], timeout: float = 10.0, what: str = "condition") -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def wait_idle(service: ChatService, timeout: float = 10.0) -> None:
    wait_for(lambda: not service.is_busy(), timeout, "the turn to finish")


def _messages(service: ChatService, project_id: str, bot: str) -> list[dict]:
    return service.list_messages(project_id, bot)


# -- validation -----------------------------------------------------------------------


def test_submit_validation(svc, project):
    with pytest.raises(ChatError) as e:
        svc.submit("nope", "pulse", "hi")
    assert e.value.status == 404
    with pytest.raises(ChatError) as e:
        svc.submit(project.id, "ghost", "hi")
    assert e.value.status == 404
    with pytest.raises(ChatError) as e:
        svc.submit(project.id, "pulse", "   ")
    assert e.value.status == 422
    assert not svc.is_busy()


# -- direct (1:1) turn ------------------------------------------------------------------


def test_direct_turn_streams_events_and_persists(svc, project, recorder):
    svc.provider = FakeProvider("The answer is 42.")
    out = svc.submit(project.id, "pulse", "What is the answer to everything?")
    assert out["queued"] is False and out["turn_id"].startswith("turn_")
    assert svc.current_turn()["bot_id"] == "pulse"
    wait_idle(svc)

    types = recorder.types()
    assert types.index("turn.started") < types.index("turn.finished")
    msgs = [e.data["message"] for e in recorder.of("chat.message") if e.data["thread"] == "pulse"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["text"] == "The answer is 42."
    assert msgs[1]["speaker"] == "Pulse" and msgs[1]["bot_id"] == "pulse"
    finished = recorder.of("turn.finished")[0]
    assert finished.data == {"turn_id": out["turn_id"], "status": "ok"}
    assert recorder.of("chat.activity"), "the live step ('Thinking') is streamed"
    step = recorder.of("chat.activity")[0].data
    assert step["thread"] == "pulse" and step["step"]["n"] >= 1

    # persisted: a fresh read (as after a restart / page refresh) sees the same thread
    stored = _messages(svc, project.id, "pulse")
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[1]["text"] == "The answer is 42."
    assert stored[0]["id"] == msgs[0]["id"] and stored[1]["id"] == msgs[1]["id"]
    threads = {t["bot_id"]: t for t in svc.list_threads(project.id)}
    assert threads["pulse"]["message_count"] == 2
    assert activity_board.display_status("pulse") == "idle"


def test_direct_turn_sees_only_its_own_thread(svc, project):
    """Context isolation: a specialist never receives another thread's history."""
    svc.provider = FakeProvider("ok one")
    svc.submit(project.id, "pulse", "Secret plan: the vault code is 1234.")
    wait_idle(svc)
    svc.provider = FakeProvider("ok two")
    svc.submit(project.id, "aegis", "Review my work please.")
    wait_idle(svc)
    seen = " ".join(str(m.get("content")) for call in svc.provider.calls for m in call if m.get("role") != "system")
    assert "vault code" not in seen
    assert "Review my work" in seen


def test_second_message_is_queued_then_chained(svc, project, recorder):
    gate = threading.Event()

    def slow(messages):
        gate.wait(5)
        return "done"

    svc.provider = FakeProvider(slow)
    first = svc.submit(project.id, "pulse", "First task please.")
    second = svc.submit(project.id, "aegis", "Second task please.")
    assert second == {"turn_id": None, "queued": True, "position": 1}
    assert svc.queue_length() == 1
    gate.set()
    wait_for(lambda: len(recorder.of("turn.finished")) == 2, what="both turns")
    wait_idle(svc)
    started = [e.data["bot_id"] for e in recorder.of("turn.started")]
    assert started == ["pulse", "aegis"]
    assert first["turn_id"] != recorder.of("turn.started")[1].data["id"]
    assert svc.queue_length() == 0


def test_abort_marks_turn_aborted_and_drops_the_queue(svc, project, recorder):
    gate = threading.Event()
    svc.provider = FakeProvider(lambda m: (gate.wait(5), "late reply")[1])
    out = svc.submit(project.id, "pulse", "Do a long thing.")
    svc.submit(project.id, "aegis", "Queued behind it.")
    assert svc.abort("turn_unknown") is False
    assert svc.abort(out["turn_id"]) is True
    assert svc.queue_length() == 0, "abort also cancels queued follow-ups"
    gate.set()
    wait_idle(svc)
    assert recorder.of("turn.finished")[0].data["status"] == "aborted"
    assert len(recorder.of("turn.started")) == 1  # the queued one never ran


def test_provider_failure_publishes_turn_failed_and_frees_the_slot(svc, project, recorder):
    def boom(messages):
        raise RuntimeError("model exploded")

    svc.provider = FakeProvider(boom)
    svc.submit(project.id, "pulse", "Trigger the failure.")
    wait_idle(svc)
    failed = recorder.of("turn.failed")
    assert len(failed) == 1 and "exploded" in failed[0].data["error"].lower() or failed[0].data["error"]
    err = [e.data["message"] for e in recorder.of("chat.message") if e.data["message"].get("role") == "error"]
    assert err, "the error becomes a visible chat message"
    assert not recorder.of("turn.finished")
    # the service is usable again
    svc.provider = FakeProvider("recovered")
    svc.submit(project.id, "pulse", "Try again please.")
    wait_idle(svc)
    assert recorder.of("turn.finished")[-1].data["status"] == "ok"


def test_steer_wrong_turn_and_empty(svc, project):
    assert svc.steer("turn_nope", "hey") is False
    with pytest.raises(ChatError):
        svc.steer("turn_nope", "  ")


# -- Cat Crew work turn ---------------------------------------------------------------------


def _fake_sop(monkeypatch, script):
    import backend.platform.sop as sop

    def run(message, *, project, registry, on_turn=None, on_phase_start=None, should_stop=None, take_steer=None, roles=None):
        return script(message, project, registry, on_turn, on_phase_start)

    monkeypatch.setattr(sop, "run_project_room_turn", run)


def test_room_turn_pops_specialists_in_order_and_mirrors_their_threads(svc, project, recorder, monkeypatch):
    def script(message, proj, registry, on_turn, on_phase_start):
        for phase, bot_id in (("architecture", "vanguard"), ("build", "pulse")):
            bot = registry.bots[bot_id]
            from backend.platform.registry import staff_bot_on_project

            staff_bot_on_project(registry, proj, bot)  # bot.staffed -> the sidebar pop-in
            on_phase_start(phase, bot)
            activity_board.set_working(bot_id, "coding", f"{phase} step")
            on_turn(SimpleNamespace(bot=bot, text=f"{bot.name} finished {phase}.", tool_events=["write_file a.py"], phase=phase))

    _fake_sop(monkeypatch, script)
    out = svc.submit(project.id, ROOM, "Build me a todo app")
    assert out["queued"] is False
    wait_idle(svc)

    def first(pred):
        return next(i for i, e in enumerate(recorder.events) if pred(e))

    i_started = first(lambda e: e.type == "turn.started")
    i_staffed = first(lambda e: e.type == "bot.staffed" and e.data["bot_id"] == "vanguard")
    i_phase = first(lambda e: e.type == "phase.started" and e.data["bot_id"] == "vanguard")
    i_working = first(
        lambda e: e.type == "bot.activity" and e.data["bot_id"] == "vanguard" and e.data["state"] == "working"
    )
    i_reply = first(
        lambda e: e.type == "chat.message" and e.data["message"].get("bot_id") == "vanguard" and e.data["thread"] == ROOM
    )
    i_done = first(lambda e: e.type == "turn.finished")
    # the documented order for one specialist's appearance in the room
    assert i_started < i_staffed < i_phase < i_working < i_reply < i_done
    staffed = [e.data["bot_id"] for e in recorder.of("bot.staffed")]
    assert staffed[:2] == ["vanguard", "pulse"]
    phases = [(e.data["phase"], e.data["bot_id"], e.data["label"]) for e in recorder.of("phase.started")]
    assert phases == [("architecture", "vanguard", "Phase 1: Architecture"), ("build", "pulse", "Phase 4: Build")]

    room = _messages(svc, project.id, ROOM)
    assert [m["role"] for m in room][0] == "user"
    assert "Vanguard finished architecture." in room[1]["text"]
    assert any(m.get("activity") for m in room), "the last bot message carries the execution card"

    # each specialist got the work mirrored into its own thread, with a card snapshot
    for bot in ("vanguard", "pulse"):
        thread = _messages(svc, project.id, bot)
        assert thread and thread[-1]["role"] == "assistant"
        assert thread[-1]["activity"]["steps"]

    # the room's kickoff is stored as project state for the SOP
    assert load_registry().projects[project.id].state.get("kickoff") == "Build me a todo app"
    # per-bot live status lines were mirrored as steps into the specialist's thread
    steps = [e.data for e in recorder.of("chat.activity") if e.data["thread"] == "pulse"]
    assert any("build step" in s["step"]["line"] for s in steps)
    assert [s["step"]["n"] for s in steps] == sorted(s["step"]["n"] for s in steps)
    assert recorder.of("turn.finished")[0].data["status"] == "ok"
    assert activity_board.display_status("pulse") == "idle"


class ScriptedProvider(FakeProvider):
    """Replays a fixed list of ``AssistantMessage``s, one per model call."""

    def __init__(self, script: list[AssistantMessage]) -> None:
        super().__init__()
        self.script = list(script)
        self.tool_names: list[list[str]] = []

    def chat(self, messages, tools=None) -> AssistantMessage:
        self.calls.append(list(messages))
        if not tools:  # side calls (clarify / compaction) are not part of the script
            return AssistantMessage(content="")
        self.tool_names.append([t["function"]["name"] for t in tools])
        return self.script.pop(0) if self.script else AssistantMessage(content="done")


def _capture_sop(monkeypatch):
    import backend.platform.sop as sop

    seen: list[dict] = []

    def run(message, *, project, registry, on_turn=None, on_phase_start=None, should_stop=None, take_steer=None, roles=None):
        seen.append({"message": message, "roles": roles})
        bot = registry.bots["pulse"]
        on_phase_start("build", bot)
        on_turn(SimpleNamespace(bot=bot, text="built", tool_events=[], phase="build"))

    monkeypatch.setattr(sop, "run_project_room_turn", run)
    return seen


def test_cat_crew_chat_is_a_free_conversation_with_the_primary_model(svc, project, recorder, monkeypatch):
    seen = _capture_sop(monkeypatch)
    svc.provider = ScriptedProvider([AssistantMessage(content="SQLite is a fine start; here is why.")])
    out = svc.submit(project.id, ROOM, "What do you think about using SQLite for this?")
    assert out["queued"] is False
    wait_idle(svc)
    assert seen == [], "a question must not start the SOP pipeline"
    assert recorder.of("turn.started")[0].data["mode"] == "chat"
    room = _messages(svc, project.id, ROOM)
    assert room[-1]["role"] == "assistant" and "fine start" in room[-1]["text"]
    assert room[-1]["speaker"] == "Cat Crew" and room[-1]["bot_id"] == ROOM
    assert recorder.of("turn.finished")[0].data["status"] == "ok"
    # the orchestrator sees read-only lookups + the hand-off, never a write tool or delegate_to_bot
    tools = set(svc.provider.tool_names[0])
    assert "hand_off_to_crew" in tools and "list_members" in tools
    assert not tools & {"write_file", "run_command", "delegate_to_bot"}
    assert activity_board.display_status(ROOM) == "idle"


def test_cat_crew_routes_work_to_the_matching_specialists(svc, project, recorder, monkeypatch):
    seen = _capture_sop(monkeypatch)
    svc.submit(project.id, ROOM, "fix the bug in the login endpoint")
    wait_idle(svc)
    assert seen == [{"message": "fix the bug in the login endpoint", "roles": ("developer",)}]
    assert recorder.of("turn.started")[0].data["mode"] == "work"
    seen.clear()
    svc.submit(project.id, ROOM, "Build a todo app with React and a FastAPI backend")
    wait_idle(svc)
    assert seen[0]["roles"] is None, "a whole app runs the full pipeline"
    seen.clear()
    svc.submit(project.id, ROOM, "@pulse please look at this")
    wait_idle(svc)
    assert [t.data["mode"] for t in recorder.of("turn.started")][-1] == "mention"


def test_cat_crew_chat_can_hand_the_work_to_the_crew(svc, project, recorder, monkeypatch):
    seen = _capture_sop(monkeypatch)
    svc.provider = ScriptedProvider(
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall("hand_off_to_crew", {"brief": "Add dark mode to the settings page", "roles": ["designer"]}, id="c1")],
            ),
            AssistantMessage(content="I asked Pixel to take the dark mode."),
        ]
    )
    svc.submit(project.id, ROOM, "The settings screen feels bright, thoughts?")
    wait_for(lambda: bool(seen) and not svc.is_busy(), 10, "the hand-off turn")
    assert seen == [{"message": "Add dark mode to the settings page", "roles": ("designer",)}]
    modes = [e.data["mode"] for e in recorder.of("turn.started")]
    assert modes == ["chat", "work"]
    texts = [m["text"] for m in _messages(svc, project.id, ROOM)]
    assert any("asked Pixel" in t for t in texts) and "built" in texts[-1]
    users = [m["text"] for m in _messages(svc, project.id, ROOM) if m["role"] == "user"]
    assert users == ["The settings screen feels bright, thoughts?"], "the brief is Cat Crew's, not a user message"
    assert [e.data["status"] for e in recorder.of("turn.finished")] == ["ok", "ok"]


def test_room_turn_error_is_reported_and_room_becomes_idle(svc, project, recorder, monkeypatch):
    def script(*a):
        raise RuntimeError("sop crashed")

    _fake_sop(monkeypatch, script)
    svc.submit(project.id, ROOM, "Build a crashing app")
    wait_idle(svc)
    assert recorder.of("turn.failed")
    assert activity_board.display_status(ROOM) == "idle"


def test_room_turn_for_deleted_project_fails_cleanly(svc, project, recorder):
    from backend.platform.registry import delete_project

    svc.submit(project.id, ROOM, "hello")
    wait_idle(svc)
    delete_project(project.id)
    with pytest.raises(ChatError) as e:
        svc.submit(project.id, ROOM, "hello again")
    assert e.value.status == 404


# -- approvals / questions -------------------------------------------------------------------


def _turn(project_id: str) -> Turn:
    return Turn(id="turn_x", project_id=project_id, bot_id="pulse", kind="direct", started_at="", session={})


def test_approval_round_trip(svc, project, recorder):
    hooks = cs._Hooks(svc, _turn(project.id))
    result: list[str] = []
    t = threading.Thread(target=lambda: result.append(hooks.request_approval("run_command", "rm -rf build", threading.Event())))
    t.start()
    wait_for(lambda: recorder.of("approval.requested"), what="approval.requested")
    req = recorder.of("approval.requested")[0].data
    assert req["tool"] == "run_command" and req["bot_id"] == "pulse"
    assert svc.resolve_approval("appr_unknown", "allow") is False
    with pytest.raises(ChatError):
        svc.resolve_approval(req["approval_id"], "maybe")
    assert svc.resolve_approval(req["approval_id"], "allow_always") is True
    t.join(5)
    assert result == ["allow_always"]
    resolved = recorder.of("approval.resolved")[0].data
    assert resolved == {"approval_id": req["approval_id"], "decision": "allow_always"}
    assert svc.resolve_approval(req["approval_id"], "allow") is False  # already consumed


def test_a_specialist_in_a_work_run_is_named_on_its_approval_card(svc, project, recorder):
    from backend.platform.runtime import reset_bot_id, set_bot_id

    room_turn = Turn(id="turn_r", project_id=project.id, bot_id="crew", kind="room", started_at="", session={})
    hooks = cs._Hooks(svc, room_turn)
    abort = threading.Event()

    def specialist() -> None:  # run_bot binds the cat's id on its own thread
        token = set_bot_id("aegis")
        try:
            hooks.request_approval("run_test_suite", "npm test", abort)
        finally:
            reset_bot_id(token)

    t = threading.Thread(target=specialist)
    t.start()
    wait_for(lambda: recorder.of("approval.requested"), what="approval.requested")
    assert recorder.of("approval.requested")[0].data["bot_id"] == "aegis"
    abort.set()
    t.join(5)


def test_approval_defaults_to_deny_when_the_turn_is_aborted(svc, project, recorder):
    hooks = cs._Hooks(svc, _turn(project.id))
    abort = threading.Event()
    result: list[str] = []
    t = threading.Thread(target=lambda: result.append(hooks.request_approval("write_file", "x", abort)))
    t.start()
    wait_for(lambda: recorder.of("approval.requested"), what="approval.requested")
    abort.set()
    t.join(5)
    assert result == ["deny"]


def test_ask_round_trip(svc, project, recorder):
    hooks = cs._Hooks(svc, _turn(project.id))
    result: list[str] = []
    t = threading.Thread(target=lambda: result.append(hooks.request_ask("Which db?", ["sqlite", "pg"], threading.Event())))
    t.start()
    wait_for(lambda: recorder.of("ask.requested"), what="ask.requested")
    ask = recorder.of("ask.requested")[0].data
    assert ask["choices"] == ["sqlite", "pg"]
    assert svc.resolve_approval(ask["ask_id"], "allow") is False  # wrong kind
    assert svc.resolve_ask(ask["ask_id"], "sqlite") is True
    t.join(5)
    assert result == ["sqlite"]


# -- reset ------------------------------------------------------------------------------------


def test_reset_project_clears_the_room_but_keeps_specialist_threads(svc, project, recorder, monkeypatch):
    def script(message, proj, registry, on_turn, on_phase_start):
        bot = registry.bots["pulse"]
        on_phase_start("build", bot)
        on_turn(SimpleNamespace(bot=bot, text="built", tool_events=[], phase="build"))

    _fake_sop(monkeypatch, script)
    svc.submit(project.id, ROOM, "Build me a todo app")
    wait_idle(svc)
    assert _messages(svc, project.id, ROOM) and _messages(svc, project.id, "pulse")

    svc.reset_project(project.id)
    assert _messages(svc, project.id, ROOM) == []
    assert _messages(svc, project.id, "pulse"), "1:1 threads survive a reset"
    assert load_registry().projects[project.id].state == {}
    assert recorder.of("project.changed")


def test_reset_is_refused_while_a_turn_runs(svc, project):
    gate = threading.Event()
    svc.provider = FakeProvider(lambda m: (gate.wait(5), "x")[1])
    svc.submit(project.id, "pulse", "Long running thing.")
    with pytest.raises(ChatError) as e:
        svc.reset_project(project.id)
    assert e.value.status == 409
    gate.set()
    wait_idle(svc)


# -- problem report -> lead -> fix plan ---------------------------------------------------------


def _fake_bug_flow(monkeypatch):
    import backend.platform.bugflow as bugflow

    seen: list[dict] = []

    def run(report, *, project, registry, reporter_id="", on_turn=None, on_phase_start=None, should_stop=None, take_steer=None):
        seen.append({"report": report, "reporter": reporter_id})
        for phase, bot_id in (("fixplan", "vanguard"), ("fix", "pulse")):
            bot = registry.bots[bot_id]
            on_phase_start(phase, bot)
            on_turn(SimpleNamespace(bot=bot, text=f"{bot.name} did {phase}.", tool_events=[], phase=phase))

    monkeypatch.setattr(bugflow, "run_bug_turn", run)
    return seen


def test_a_problem_report_in_a_cats_chat_runs_the_bug_flow_with_that_cat_as_reporter(svc, project, recorder, monkeypatch):
    seen = _fake_bug_flow(monkeypatch)
    out = svc.report_problem(project.id, "pulse", "the login button does nothing")
    assert out["queued"] is False
    wait_idle(svc)

    assert seen == [{"report": "Problem report for Pulse:\nthe login button does nothing", "reporter": "pulse"}]
    # the report is in the cat's own thread and in Cat Crew; the plan and the fix are visible in Cat Crew
    assert "the login button does nothing" in _messages(svc, project.id, "pulse")[0]["text"]
    room = _messages(svc, project.id, ROOM)
    assert "the login button does nothing" in room[0]["text"]
    assert any("Vanguard did fixplan." in m["text"] for m in room)
    assert any("Pulse did fix." in m["text"] for m in room)
    phases = [(e.data["phase"], e.data["label"]) for e in recorder.of("phase.started")]
    assert phases == [("fixplan", "Fix plan"), ("fix", "Fix")]
    assert recorder.of("turn.started")[0].data["mode"] == "bug"
    assert recorder.of("turn.finished")[0].data["status"] == "ok"


def test_the_reporter_cat_gets_the_fix_plan_in_its_own_thread(svc, project, recorder, monkeypatch):
    _fake_bug_flow(monkeypatch)
    svc.report_problem(project.id, "pulse", "the login button does nothing")
    wait_idle(svc)
    plans = [m for m in _messages(svc, project.id, "pulse") if "Fix plan for your report" in m["text"]]
    assert len(plans) == 1
    assert "Vanguard did fixplan." in plans[0]["text"] and "from Vanguard" in plans[0]["text"]
    # only the plan is copied: the fix itself is already mirrored as Pulse's own step, once
    assert sum("Pulse did fix." in m["text"] for m in _messages(svc, project.id, "pulse")) == 1
    # the lead keeps its own mirror and gets no extra "for your report" copy
    assert not any("Fix plan for your report" in m["text"] for m in _messages(svc, project.id, "vanguard"))
    assert any(e.data["message"]["text"].startswith("Fix plan for your report") for e in recorder.of("chat.message") if e.data["thread"] == "pulse")


def test_a_report_made_in_the_leads_own_chat_or_in_cat_crew_adds_no_copy(svc, project, monkeypatch):
    _fake_bug_flow(monkeypatch)
    svc.report_problem(project.id, "vanguard", "the plan tool is broken")
    wait_idle(svc)
    assert not any("Fix plan for your report" in m["text"] for m in _messages(svc, project.id, "vanguard"))
    svc.report_problem(project.id, ROOM, "the whole site is blank")
    wait_idle(svc)
    for bot in ("pulse", "vanguard"):
        assert not any("Fix plan for your report" in m["text"] for m in _messages(svc, project.id, bot))


def test_a_problem_report_in_cat_crew_has_no_reporter_cat(svc, project, monkeypatch):
    seen = _fake_bug_flow(monkeypatch)
    svc.report_problem(project.id, ROOM, "the whole site is blank")
    wait_idle(svc)
    assert seen == [{"report": "Problem report:\nthe whole site is blank", "reporter": ""}]


def test_an_empty_problem_report_is_refused(svc, project):
    with pytest.raises(ChatError) as e:
        svc.report_problem(project.id, "pulse", "  ")
    assert e.value.status == 422
    assert not svc.is_busy()


# -- persona swaps + the optional shared history ------------------------------------------------


def test_each_hand_over_emits_a_cat_swapped_event_for_the_ui(svc, project, recorder, monkeypatch):
    def script(message, proj, registry, on_turn, on_phase_start):
        for phase, bot_id in (("devops", "forge"), ("build", "pulse")):
            bot = registry.bots[bot_id]
            on_phase_start(phase, bot)
            on_turn(SimpleNamespace(bot=bot, text=f"{bot.name} done.", tool_events=[], phase=phase))

    _fake_sop(monkeypatch, script)
    svc.submit(project.id, ROOM, "Build me a todo app")
    wait_idle(svc)
    swaps = [e.data for e in recorder.of("cat.swapped")]
    assert [(s["cat_id"], s["name"], s["phase"], s["label"], s["status"]) for s in swaps] == [
        ("forge", "Forge", "devops", "Phase 3: DevOps", "Coding"),
        ("pulse", "Pulse", "build", "Phase 4: Build", "Coding"),
    ]
    types = recorder.types()  # the swap arrives together with (right after) the phase event
    assert types.index("phase.started") < types.index("cat.swapped") < types.index("turn.finished")


def test_a_work_run_owns_one_shared_thread_only_in_unified_mode(svc, project, monkeypatch):
    from backend import config
    from backend.platform import shared_context

    seen: list = []

    def script(message, proj, registry, on_turn, on_phase_start):
        seen.append(shared_context.active_thread(proj.id))

    _fake_sop(monkeypatch, script)
    svc.submit(project.id, ROOM, "Build me a todo app")
    wait_idle(svc)
    assert seen == [None]  # default: isolated runs

    monkeypatch.setattr(config, "UNIFIED_CONTEXT", True)
    svc.submit(project.id, ROOM, "Build me a todo app")
    wait_idle(svc)
    assert isinstance(seen[1], shared_context.SharedThread)
    assert shared_context.active_thread(project.id) is None  # released when the run ends
