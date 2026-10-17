"""The in-process event bus and everything that feeds it.

These pin down the real-time contract: every writer of live state (activity
board, task ledger, registry staffing, the SOP) announces a change, so the API
can push it to clients without polling.
"""

from __future__ import annotations

import threading

import pytest

from backend.db import ledger as db
from backend.platform import activity_board, bus
from backend.platform.models import Bot, Project, Registry
from conftest import Recorder


# -- the bus itself ----------------------------------------------------------------------


def test_publish_reaches_subscribers_with_increasing_seq(isolated) -> None:
    got: list[bus.Event] = []
    off = bus.get_bus().subscribe(got.append)
    bus.publish("x.one", {"a": 1}, project_id="p1")
    bus.publish("x.two")
    assert [e.type for e in got] == ["x.one", "x.two"]
    assert got[0].project_id == "p1" and got[0].data == {"a": 1}
    assert got[1].seq == got[0].seq + 1
    off()
    bus.publish("x.three")
    assert len(got) == 2


def test_a_failing_subscriber_never_breaks_the_writer(isolated) -> None:
    def boom(_e: bus.Event) -> None:
        raise RuntimeError("bad subscriber")

    good: list[bus.Event] = []
    bus.get_bus().subscribe(boom)
    bus.get_bus().subscribe(good.append)
    bus.emit_task_changed("p1", "t1")  # must not raise
    assert len(good) == 1


def test_publish_from_many_threads_keeps_seq_order_per_subscriber(isolated) -> None:
    seen: list[int] = []
    bus.get_bus().subscribe(lambda e: seen.append(e.seq))

    def work() -> None:
        for _ in range(200):
            bus.emit_bot_activity("pulse", "coding", "working", "x")

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(seen) == 1600
    assert seen == sorted(seen)  # delivered in sequence order, no interleaving


def test_a_subscriber_may_publish_without_deadlocking(isolated) -> None:
    got: list[str] = []

    def echo(e: bus.Event) -> None:
        got.append(e.type)
        if e.type == "a":
            bus.publish("b")

    bus.get_bus().subscribe(echo)
    bus.publish("a")
    assert got == ["a", "b"]


# -- activity board -> bus -----------------------------------------------------------------


def _activity(rec: Recorder) -> list[tuple[str, str, str, str]]:
    return [
        (e.data["bot_id"], e.data["status"], e.data["state"], e.data["text"])
        for e in rec.of(bus.BOT_ACTIVITY)
    ]


def test_activity_board_announces_display_status_and_dedupes(recorder) -> None:
    activity_board.set_state("Pulse", "working")
    # Busy with no specific word yet reads "thinking", never a misleading "idle".
    assert _activity(recorder)[-1] == ("Pulse", "thinking", "working", "")
    activity_board.set_status_word("Pulse", "coding")
    activity_board.set_activity("Pulse", "Editing src/App.jsx")
    assert _activity(recorder)[-1] == ("Pulse", "coding", "working", "Editing src/App.jsx")
    n = len(_activity(recorder))
    activity_board.set_activity("Pulse", "Editing src/App.jsx")  # unchanged snapshot
    assert len(_activity(recorder)) == n
    activity_board.mark_idle("Pulse")
    assert _activity(recorder)[-1][1:3] == ("idle", "idle")
    assert activity_board.display_status("pulse") == "idle"


def test_set_working_is_one_atomic_announcement(recorder) -> None:
    activity_board.set_working("aegis", "testing", "Running the suite")
    assert _activity(recorder) == [("aegis", "testing", "working", "Running the suite")]


def test_a_stale_status_word_never_lights_an_idle_bot(isolated) -> None:
    activity_board.set_status_word("aegis", "testing")
    activity_board.set_state("aegis", "idle")
    assert activity_board.display_status("aegis") == "idle"


def test_status_word_for_tool() -> None:
    assert activity_board.status_word_for_tool("write_file") == "coding"
    assert activity_board.status_word_for_tool("browser_check") == "testing"
    assert activity_board.status_word_for_tool("read_file") == "thinking"


# -- ledger / registry -> bus -----------------------------------------------------------------


def test_new_member_is_announced_once(recorder) -> None:
    db.add_project_member("p1", "pulse")
    db.add_project_member("p1", "pulse")  # already staffed: nothing changes on screen
    staffed = recorder.of(bus.BOT_STAFFED)
    assert len(staffed) == 1
    assert staffed[0].project_id == "p1" and staffed[0].data["bot_id"] == "pulse"
    db.remove_project_member("p1", "pulse")
    assert [e.data["bot_id"] for e in recorder.of(bus.BOT_RELEASED)] == ["pulse"]


def test_ledger_writes_announce_their_project(recorder) -> None:
    task_id = db.create_task("p1", "pulse", "Build it", "desc")
    changed = recorder.of(bus.TASK_CHANGED)
    assert [(e.project_id, e.data["task_id"]) for e in changed] == [("p1", task_id)]
    # create_task staffs the assignee first, then announces the task.
    assert recorder.types().index(bus.BOT_STAFFED) < recorder.types().index(bus.TASK_CHANGED)
    db.update_task_status(task_id, "in_progress")
    db.add_task_result(task_id, "done", "shipped")
    assert len(recorder.of(bus.TASK_CHANGED)) == 3
    assert db.list_tasks_with_summary("p1")[0]["latest_summary"] == "shipped"
    assert db.get_task_with_summary(task_id)["latest_summary"] == "shipped"


def test_file_changes_announce_and_are_listed_newest_first(recorder) -> None:
    task_id = db.create_task("p1", "pulse", "t")
    d1 = db.log_file_change("p1", task_id, "pulse", "a.py", "+1")
    d2 = db.log_file_change("p1", task_id, "pulse", "b.py", "+2")
    events = recorder.of(bus.FILE_CHANGED)
    assert [e.data["diff_uuid"] for e in events] == [d1, d2]
    rows = db.list_file_changes("p1", 8)
    assert [r["file_path"] for r in rows] == ["b.py", "a.py"]
    assert "diff_content" not in rows[0]  # the body is fetched on demand
    assert db.get_file_change(d2)["diff_content"] == "+2"


def test_staffing_a_bot_on_a_project_announces_it(recorder) -> None:
    from backend.platform import registry as reg

    bot = Bot(id="pulse", name="Pulse", role="developer", system_prompt="Build.")
    project = Project(id="p1", name="Proj", bot_ids=[])
    registry = Registry(bots={"pulse": bot}, projects={"p1": project})
    reg._cache = registry
    reg.staff_bot_on_project(registry, project, bot)
    assert [(e.project_id, e.data["bot_id"]) for e in recorder.of(bus.BOT_STAFFED)] == [("p1", "pulse")]


# -- SOP wiring -----------------------------------------------------------------------------------


def _registry() -> Registry:
    bots = {
        "cipher": Bot(id="cipher", name="Cipher", role="prompt_engineer", system_prompt="Spec."),
        "pulse": Bot(id="pulse", name="Pulse", role="developer", system_prompt="Build."),
        "aegis": Bot(id="aegis", name="Aegis", role="qa", system_prompt="Verify."),
    }
    return Registry(bots=bots, projects={"p1": Project(id="p1", name="Proj", bot_ids=["cipher"])})


@pytest.fixture
def registry(isolated, monkeypatch) -> Registry:
    reg = _registry()
    monkeypatch.setattr("backend.platform.registry._cache", reg)
    return reg


def test_sop_staffs_bot_before_it_starts_and_marks_task_in_progress(
    registry, recorder, monkeypatch
) -> None:
    from backend.platform import sop

    project = registry.projects["p1"]
    order: list[str] = []
    statuses_at_start: list[str] = []
    bus.get_bus().subscribe(lambda e: order.append(e.type) if e.type == bus.BOT_STAFFED else None)

    monkeypatch.setattr(sop, "run_delegated", lambda bot, task, **kw: "done")

    def on_phase_start(phase: str, bot: Bot) -> None:
        order.append("phase:" + bot.id)
        statuses_at_start.append(db.list_tasks_for_project("p1")[0]["status"])

    sop.run_project_room_turn(
        "@pulse build the thing", project=project, registry=registry, on_phase_start=on_phase_start
    )
    assert order.index(bus.BOT_STAFFED) < order.index("phase:pulse")
    assert statuses_at_start == ["in_progress"]
    assert db.list_tasks_for_project("p1")[0]["status"] in {"done", "unverified"}


def test_sop_marks_the_task_failed_when_the_bot_run_blows_up(registry, monkeypatch) -> None:
    from backend.platform import sop

    def boom(bot, task, **kwargs):
        raise RuntimeError("model died")

    monkeypatch.setattr(sop, "run_delegated", boom)
    with pytest.raises(RuntimeError):
        sop.run_project_room_turn("@pulse build", project=registry.projects["p1"], registry=registry)
    assert db.list_tasks_for_project("p1")[0]["status"] == "failed"
