"""The pipeline end to end with a scripted model: work orders, QA cycles, resume, stall guard,
automatic continue, harness smoke checks, and Cat Crew's promised-but-missing hand-off."""

from __future__ import annotations

import pytest

from backend.harness.runtime import reset_provider, set_provider
from backend.platform import sop
from backend.platform.models import Bot, Project, Registry
from backend.platform.sop import run_project_room_turn
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall

DISTRIBUTION = """Todo list by role ...

## Work orders
1. Layout, navigation and home page sections -- resources/views
2. Public gallery -- GalleryController
3. Admin gallery and uploads -- Admin/GalleryController

## Acceptance checks
- GET / -> 200, shows "Цялостен ремонт"
- GET /admin -> redirects to login
"""


@pytest.fixture(autouse=True)
def _own_ledger(isolated):
    yield


def _registry() -> Registry:
    bots = {
        "cipher": Bot(id="cipher", name="Cipher", role="prompt_engineer", system_prompt="Blueprint."),
        "vanguard": Bot(id="vanguard", name="Vanguard", role="vanguard", system_prompt="Lead."),
        "forge": Bot(id="forge", name="Forge", role="devops", system_prompt="Ops."),
        "pulse": Bot(id="pulse", name="Pulse", role="developer", system_prompt="Build."),
        "pixel": Bot(id="pixel", name="Pixel", role="designer", system_prompt="Style."),
        "aegis": Bot(id="aegis", name="Aegis", role="qa", system_prompt="Verify."),
    }
    return Registry(bots=bots, projects={"p1": Project(id="p1", name="P", bot_ids=list(bots))})


def _text(messages: list[Message]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages if m.get("role") in ("system", "user"))


class _Script(BaseProvider):
    """Writes one file per producing turn; Vanguard's distribution lists work orders; QA fails
    ``qa_fails`` times, then passes."""

    def __init__(self, qa_fails: int = 0) -> None:
        self.qa_fails = qa_fails
        self.qa_runs = 0
        self.tasks: list[str] = []

    def chat(self, messages: list[Message], tools: list[dict] | None = None):
        text = _text(messages)
        names = {t.get("function", {}).get("name") for t in tools or []}
        if messages[-1].get("role") != "tool":
            self.tasks.append(text)
            if "Break it into a strict todo" in text:
                return AssistantMessage(content=DISTRIBUTION)
            if "Prove the product works" in text:
                self.qa_runs += 1
                if self.qa_runs <= self.qa_fails:
                    return AssistantMessage(content="FAIL: tests/Feature/GalleryTest fails: /gallery is a 404")
                return AssistantMessage(content="PASS: 12 tests passed")
            if "write_file" in names:
                return AssistantMessage(content=None, tool_calls=[ToolCall(name="write_file", arguments={"path": "notes.txt", "content": "x"}, id="c1")])
        return AssistantMessage(content="Done.")

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": result}


def _run(provider: BaseProvider, message: str = "Build the repair site.", registry: Registry | None = None):
    registry = registry or _registry()
    project = registry.projects["p1"]
    token = set_provider(provider)
    try:
        turns = run_project_room_turn(message, project=project, registry=registry)
    finally:
        reset_provider(token)
    return turns, project, registry


def test_the_build_runs_one_work_order_at_a_time(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    provider = _Script()
    turns, project, _ = _run(provider)
    builds = [t for t in turns if t.phase == "build"]
    assert len(builds) == 3 and all(t.verified for t in builds)
    orders = [task for task in provider.tasks if "YOUR WORK ORDER" in task]
    assert "YOUR WORK ORDER (1 of 3): Layout, navigation and home page sections" in orders[0]
    assert "YOUR WORK ORDER (3 of 3): Admin gallery and uploads" in orders[2]
    pipe = project.state["pipeline"]
    assert pipe["checks"][0]["path"] == "/" and pipe["checks"][1]["redirect"] is True
    assert project.state["sop_phase"] == "done"


def test_qa_fails_the_developer_fixes_and_qa_checks_again_until_it_passes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    provider = _Script(qa_fails=2)
    turns, _project, _ = _run(provider)
    tail = [t.phase for t in turns if t.phase in ("qa", "qa-fix")]
    assert tail == ["qa", "qa-fix", "qa", "qa-fix", "qa"]
    fix_task = next(task for task in provider.tasks if "QA checked the work and FAILED it (check 1 of 3)" in task)
    assert "/gallery is a 404" in fix_task


def test_qa_stops_after_three_checks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    turns, project, _ = _run(_Script(qa_fails=9))
    assert [t.phase for t in turns].count("qa") == 3
    assert "qa" not in project.state["pipeline"]["done"]


def test_continue_finishes_the_planned_run_instead_of_planning_again(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    first = _Script(qa_fails=9)
    _turns, _project, registry = _run(first)
    again = _Script()
    turns, project, _ = _run(again, "ok try again", registry)
    phases = [t.phase for t in turns]
    assert "blueprint" not in phases and "architecture" not in phases and "distribution" not in phases
    assert "build" not in phases  # every work order was already built
    assert phases[-1] == "qa" and project.state["sop_phase"] == "done"
    assert all("Build the repair site." in task for task in again.tasks)  # the planned brief, not "ok try again"


def test_a_new_request_plans_from_scratch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    _turns, _project, registry = _run(_Script())
    turns, _project, _ = _run(_Script(), "Add a blog with categories.", registry)
    assert turns[0].phase == "blueprint"


def test_a_hand_off_can_ask_to_resume(tmp_path, monkeypatch) -> None:
    from backend.platform import handoff

    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    _turns, _project, registry = _run(_Script(qa_fails=9))
    handoff.request("p1", "Finish the remaining QA items for the repair site", None, resume=True)
    handoff.take("p1")  # the chat service takes the brief; the resume flag stays for the SOP
    turns, _project, _ = _run(_Script(), "Finish the remaining QA items for the repair site", registry)
    assert turns[0].phase == "qa"


class _Reader(BaseProvider):
    """Reads files forever: never writes anything."""

    def __init__(self) -> None:
        self.n = 0

    def chat(self, messages: list[Message], tools: list[dict] | None = None):
        self.n += 1
        return AssistantMessage(content=None, tool_calls=[ToolCall(name="list_dir", arguments={"path": f"d{self.n}"}, id=f"c{self.n}")])

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": result}


def test_a_cat_that_only_reads_is_stopped_by_the_stall_guard() -> None:
    from backend.harness.loop import hit_stall

    out = _Reader().run_agent([{"role": "user", "content": "build"}], [{"type": "function", "function": {"name": "list_dir"}}], lambda n, a: "dir x", max_rounds=100, stall_rounds=6)
    assert hit_stall(out)


def test_a_cat_that_keeps_writing_is_never_stalled() -> None:
    class _Writer(_Reader):
        def chat(self, messages, tools=None):
            self.n += 1
            if self.n > 20:
                return AssistantMessage(content="All written.")
            return AssistantMessage(content=None, tool_calls=[ToolCall(name="write_file", arguments={"path": f"f{self.n}"}, id=f"c{self.n}")])

    out = _Writer().run_agent([{"role": "user", "content": "build"}], [{"type": "function", "function": {"name": "write_file"}}], lambda n, a: "Wrote f", max_rounds=100, stall_rounds=6)
    assert out == "All written."


def test_a_productive_work_order_that_runs_out_of_rounds_is_continued_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    calls: list[str] = []

    def fake_run(bot, task, **kwargs):
        calls.append(task)
        kwargs["collect_tool_events"].append(f"{bot.name} executed write_file on a.php")
        return "Done." if len(calls) > 1 else "Stopped after 60 tool rounds with work still in flight."

    monkeypatch.setattr(sop, "run_delegated", fake_run)
    registry = _registry()
    turn = sop._run_one(registry.bots["pulse"], "build it", project=registry.projects["p1"], registry=registry, phase="build")
    assert len(calls) == 2 and calls[1].startswith("CONTINUE:") and "executed write_file on a.php" in calls[1]
    assert turn.verified and not turn.round_capped


def test_phase_budgets_reach_the_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    seen: dict[str, int] = {}

    def fake_run(bot, task, **kwargs):
        seen["rounds"], seen["stall"] = kwargs["max_rounds"], kwargs["stall_rounds"]
        return "ok"

    monkeypatch.setattr(sop, "run_delegated", fake_run)
    registry = _registry()
    sop._run_one(registry.bots["cipher"], "plan", project=registry.projects["p1"], registry=registry, phase="blueprint")
    assert seen == {"rounds": 15, "stall": 0}
    sop._run_one(registry.bots["pulse"], "build", project=registry.projects["p1"], registry=registry, phase="build", budget=60)
    assert seen == {"rounds": 60, "stall": sop._STALL_ROUNDS}


def test_the_harness_smoke_checks_request_every_acceptance_url(monkeypatch, tmp_path) -> None:
    (tmp_path / ".env").write_text('APP_URL="http://site.local"\n', encoding="utf-8")
    project = Project(id="p1", name="P", folders=[str(tmp_path)])

    class _R:
        def __init__(self, status, text="", location=""):
            self.status_code, self.text, self.headers = status, text, {"location": location}

    pages = {
        "http://site.local/": _R(200, "<h2>Цялостен ремонт</h2>"),
        "http://site.local/gallery": _R(200, "<h1>Gallery</h1>"),
        "http://site.local/admin": _R(302, "", "http://site.local/login"),
    }
    monkeypatch.setattr("requests.get", lambda url, **k: pages[url])
    report = sop._smoke_report(project, sop._acceptance_checks(DISTRIBUTION + '- GET /gallery -> 200, shows "Галерия"\n'))
    assert "2 of 3 passed" in report
    assert "PASS GET /: HTTP 200" in report and "PASS GET /admin: HTTP 302 -> http://site.local/login" in report
    assert "FAIL GET /gallery: HTTP 200, missing: Галерия" in report


def test_cat_crew_promising_a_hand_off_without_the_tool_still_hands_off() -> None:
    from backend.core.chat_service import promises_handoff

    assert promises_handoff("I'm ready to hand the implementation over to the developers.\n### Brief ...")
    assert not promises_handoff("Should I hand this over to the crew?")
    assert not promises_handoff("The tiles have a hand-made look.")


def test_ticking_the_checklist_does_not_eat_the_round_budget() -> None:
    class _Planner(_Reader):
        def chat(self, messages, tools=None):
            self.n += 1
            if self.n <= 5:  # five rounds of checklist bookkeeping first
                return AssistantMessage(content=None, tool_calls=[ToolCall(name="update_todo", arguments={"action": "add", "item": f"step {self.n}"}, id=f"t{self.n}")])
            if self.n <= 9:  # then four rounds of real work
                return AssistantMessage(content=None, tool_calls=[ToolCall(name="write_file", arguments={"path": f"f{self.n}"}, id=f"w{self.n}")])
            return AssistantMessage(content="Finished.")

    tools = [{"type": "function", "function": {"name": n}} for n in ("update_todo", "write_file")]
    out = _Planner().run_agent([{"role": "user", "content": "go"}], tools, lambda n, a: "ok", max_rounds=5)
    assert out == "Finished."  # 9 tool rounds on a budget of 5: the 5 checklist rounds were free


def test_a_cat_called_in_for_one_job_gets_a_real_budget_and_the_stall_guard(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    seen: dict[str, int] = {}

    def fake_run(bot, task, **kwargs):
        seen["rounds"], seen["stall"] = kwargs["max_rounds"], kwargs["stall_rounds"]
        return "ok"

    monkeypatch.setattr(sop, "run_delegated", fake_run)
    registry = _registry()
    sop._run_one(registry.bots["pulse"], "seo", project=registry.projects["p1"], registry=registry, phase="spontaneous")
    assert seen == {"rounds": 60, "stall": sop._STALL_ROUNDS}


def test_a_work_order_out_of_rounds_never_asks_the_person_to_type_continue(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    calls: list[str] = []

    def fake_run(bot, task, **kwargs):
        calls.append(task)
        kwargs["collect_tool_events"].append(f"{bot.name} executed write_file on f{len(calls)}.php")
        return f"Stopped after 60 tool rounds with work still in flight. {sop._SEND_CONTINUE}"

    monkeypatch.setattr(sop, "run_delegated", fake_run)
    registry = _registry()
    turn = sop._run_one(registry.bots["pulse"], "build", project=registry.projects["p1"], registry=registry, phase="build")
    assert len(calls) == 1 + sop._MAX_AUTO_CONTINUES  # kept going while it made progress
    assert "send continue" not in turn.text and turn.ledger_status == "blocked"


def test_update_todo_adds_a_whole_plan_in_one_call(tmp_path, monkeypatch) -> None:
    from backend.tools import execute_tool

    monkeypatch.setattr("backend.services.todo.load_board", lambda: getattr(test_update_todo_adds_a_whole_plan_in_one_call, "board", None))

    def save(board):
        test_update_todo_adds_a_whole_plan_in_one_call.board = board

    monkeypatch.setattr("backend.services.todo.save_board", save)
    out = execute_tool("update_todo", {"action": "add", "items": ["Meta tags", "Open Graph", "Sitemap"]})
    assert "Meta tags" in out and "Open Graph" in out and "Sitemap" in out
