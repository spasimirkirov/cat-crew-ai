"""Problem report -> lead writes a fix plan (create_fix_task) -> fixes -> QA verifies.

Run end to end through the real run_bug_turn / run_delegated / execute_tool path with a scripted
provider that plays all four cats, so what is tested is the wiring: ledger work orders, fresh
per-step context, the QA loop and the honest closing status.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from backend.db import ledger as db
from backend.harness.runtime import reset_provider, set_provider
from backend.platform import role_tools
from backend.platform.bugflow import MAX_FIX_ATTEMPTS, run_bug_turn
from backend.platform.models import Bot, Project, Registry
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall
from backend.services.workspace import current_folders, set_folders
import backend.services.pending_restart as _pending_restart


def _call(name: str, **arguments) -> AssistantMessage:
    return AssistantMessage(content="", tool_calls=[ToolCall(name=name, arguments=arguments)])


class _Crew(BaseProvider):
    """Plays Vanguard, Pulse and Aegis; tells them apart by the work order they were given."""

    display_name = "crew-script"

    def __init__(self, *, verdicts: list[str], plan_steps: int = 1, plan_with_tools: bool = True) -> None:
        self.verdicts = list(verdicts)
        self.plan_steps = plan_steps
        self.plan_with_tools = plan_with_tools
        self.seen_by_fixer: list[str] = []
        self.fix_runs = 0

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        text = "\n".join(str(m.get("content")) for m in messages if m.get("role") != "tool")
        results = [str(m.get("content")) for m in messages if m.get("role") == "tool"]
        if "Write the fix plan." in text:
            bug = re.search(r"create_fix_task is: (\S+)", text).group(1)
            if self.plan_with_tools and len(results) < self.plan_steps:
                n = len(results) + 1
                return _call(
                    "create_fix_task",
                    bug_task_id=bug,
                    assignee="Pulse",
                    title=f"Step {n}",
                    instructions=f"Change file step{n}.txt so it says fixed",
                    acceptance=f"step{n}.txt contains fixed",
                )
            return AssistantMessage(content="Plan: fix it in the code.")
        if "WORK ORDER (fix)" in text:
            task_id = re.search(r"report_task_results is: (\S+)", text).group(1)
            if not results:
                self.fix_runs += 1
                self.seen_by_fixer.append(text)
                return _call("report_task_results", task_id=task_id, summary="changed it", status="done")
            return AssistantMessage(content="Fixed it.")
        if "Verify the fix for this problem" in text:
            return AssistantMessage(content=self.verdicts.pop(0))
        return AssistantMessage(content="?")

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


@pytest.fixture
def world(isolated, tmp_path, monkeypatch):
    _pending_restart.reset_runtime_state()
    db.init_db()
    role_tools.ensure_role_tools()
    folder = tmp_path / "site"
    folder.mkdir()
    bots = {
        "vanguard": Bot(id="vanguard", name="Vanguard", role="vanguard", system_prompt="Lead.", lead=True),
        "pulse": Bot(id="pulse", name="Pulse", role="developer", system_prompt="Dev."),
        "aegis": Bot(id="aegis", name="Aegis", role="qa", system_prompt="QA."),
    }
    project = Project(id="p1", name="P", folders=[str(folder)], bot_ids=list(bots))
    registry = Registry(bots=bots, projects={"p1": project})
    # create_fix_task reads the live registry, exactly as it does in the engine.
    monkeypatch.setattr("backend.platform.registry.load_registry", lambda: registry)
    saved = current_folders()
    set_folders([folder])
    yield registry, project, folder
    set_folders(saved)


def _run(world, provider, report="the header is broken", reporter="pulse", **kw):
    registry, project, _folder = world
    token = set_provider(provider)
    turns = []
    try:
        run_bug_turn(report, project=project, registry=registry, reporter_id=reporter, on_turn=turns.append, **kw)
    finally:
        reset_provider(token)
    return turns


def _bug(project_id="p1"):
    bugs = [t for t in db.list_tasks_with_summary(project_id, 100) if t["kind"] == "bug"]
    assert len(bugs) == 1
    return bugs[0]


def test_a_reported_problem_is_planned_fixed_verified_and_closed(world) -> None:
    provider = _Crew(verdicts=["PASS both criteria hold"], plan_steps=2)
    turns = _run(world, provider)

    bug = _bug()
    assert bug["status"] == "done"
    assert bug["reported_by"] == "pulse" and bug["assigned_to"] == "vanguard"
    fixes = db.list_child_tasks(bug["id"], "fix")
    assert [f["title"] for f in fixes] == ["Step 1", "Step 2"]
    assert all(f["status"] == "done" and f["assigned_to"] == "pulse" for f in fixes)
    assert all(f["acceptance"].endswith("contains fixed") for f in fixes)
    assert [t.phase for t in turns] == ["fixplan", "fix", "fix", "qa", "fixreport"]
    assert "Fixed and verified by QA" in turns[-1].text
    review = db.list_child_tasks(bug["id"], "review")[0]
    assert "step1.txt contains fixed" in review["acceptance"]


def test_a_fix_gets_its_work_order_not_the_conversation(world) -> None:
    provider = _Crew(verdicts=["PASS"], plan_steps=1)
    _run(world, provider, report="the header overlaps the menu")
    order = provider.seen_by_fixer[0]
    assert "Change file step1.txt" in order and "step1.txt contains fixed" in order
    assert "the header overlaps the menu" in order


def test_qa_fail_becomes_another_fix_with_the_verdict_and_then_passes(world) -> None:
    provider = _Crew(verdicts=["FAIL step1.txt is still broken", "PASS now fine"])
    turns = _run(world, provider)
    bug = _bug()
    assert bug["status"] == "done"
    fixes = db.list_child_tasks(bug["id"], "fix")
    assert [f["title"] for f in fixes] == ["Step 1", "Fix again (1): the header is broken"]
    assert fixes[1]["attempt"] == 1
    assert "step1.txt is still broken" in provider.seen_by_fixer[1]
    assert [t.phase for t in turns].count("qa") == 2


def test_the_bug_closes_failed_when_qa_keeps_rejecting(world) -> None:
    provider = _Crew(verdicts=["FAIL no"] * (MAX_FIX_ATTEMPTS + 1))
    turns = _run(world, provider)
    bug = _bug()
    assert bug["status"] == "failed"
    assert provider.fix_runs == 1 + MAX_FIX_ATTEMPTS
    assert "Not fixed" in turns[-1].text


def test_a_stuck_qa_turn_is_not_a_pass(world) -> None:
    provider = _Crew(verdicts=["I got stuck repeating the same failed tool.", "PASS"])
    _run(world, provider)
    assert provider.fix_runs == 2  # the stuck verdict sent the developer round again
    assert _bug()["status"] == "done"


def test_without_a_qa_cat_the_bug_is_unverified_not_done(world) -> None:
    registry, project, _ = world
    project.bot_ids.remove("aegis")
    del registry.bots["aegis"]
    provider = _Crew(verdicts=[])
    _run(world, provider)
    assert _bug()["status"] == "unverified"


def test_a_lead_that_writes_prose_still_gets_one_fix_for_the_reporter(world) -> None:
    provider = _Crew(verdicts=["PASS"], plan_with_tools=False)
    _run(world, provider)
    bug = _bug()
    fixes = db.list_child_tasks(bug["id"], "fix")
    assert len(fixes) == 1 and fixes[0]["assigned_to"] == "pulse"
    assert "Plan: fix it in the code." in provider.seen_by_fixer[0]
    assert bug["status"] == "done"


def test_a_stop_request_ends_the_flow_without_closing_it_as_fixed(world) -> None:
    provider = _Crew(verdicts=["PASS"])
    _run(world, provider, should_stop=lambda: True)
    assert _bug()["status"] == "blocked"
    assert provider.fix_runs == 0


# ---- create_fix_task on its own -------------------------------------------------------------


@pytest.fixture
def lead_env(world):
    bug = db.create_task("p1", "vanguard", "Bug: x", "x", kind="bug")
    return bug


def test_create_fix_task_rejects_an_unknown_bug_and_an_unknown_cat(lead_env) -> None:
    from backend.tool_functions.lead_tools import create_fix_task

    assert create_fix_task("task_nope", "Pulse", "t", "i", "a").startswith("Error: unknown bug_task_id")
    msg = create_fix_task(lead_env, "Aegis", "t", "i", "a")  # QA is not a fixer
    assert msg.startswith("Error: no cat named") and "Pulse (developer)" in msg
    assert "Aegis (" not in msg.split("Assign one of:")[1]
    assert create_fix_task(lead_env, "Pulse", "", "i", "a").startswith("Error: title")


def test_create_fix_task_caps_the_plan_and_validates_dependencies(lead_env) -> None:
    from backend.tool_functions.lead_tools import MAX_FIX_TASKS, create_fix_task

    assert "unknown fix task ids" in create_fix_task(lead_env, "pulse", "t", "i", "a", depends_on="task_zzz")
    first = create_fix_task(lead_env, "developer", "one", "i", "a")
    fid = re.search(r"(task_\w+)", first).group(1)
    assert create_fix_task(lead_env, "Pulse", "two", "i", "a", depends_on=fid).startswith("Created")
    for _ in range(MAX_FIX_TASKS - 2):
        assert create_fix_task(lead_env, "Pulse", "more", "i", "a").startswith("Created")
    assert create_fix_task(lead_env, "Pulse", "too many", "i", "a").startswith("Error: a fix plan has at most")
    assert db.get_task(fid)["kind"] == "fix" and db.get_task(fid)["parent_id"] == lead_env


def test_the_lead_always_has_create_fix_task() -> None:
    from backend.platform.essentials import essential_slugs_for_agent

    assert "create_fix_task" in essential_slugs_for_agent("vanguard")


# ---- ledger migration -----------------------------------------------------------------------


def test_an_old_ledger_gets_the_work_order_columns_and_keeps_its_rows(tmp_path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE project_member_tasks (id TEXT PRIMARY KEY, project_id TEXT, bot_id TEXT, title TEXT,"
        " description TEXT, status TEXT, created_by TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute("INSERT INTO project_member_tasks (id, title) VALUES ('t1', 'old row')")
    from backend.db import ledger

    ledger._migrate_task_work_orders(conn)
    ledger._migrate_task_work_orders(conn)  # idempotent
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(project_member_tasks)")}
    assert {"kind", "parent_id", "acceptance", "depends_on", "attempt", "reported_by"} <= cols
    row = conn.execute("SELECT * FROM project_member_tasks WHERE id='t1'").fetchone()
    assert row["title"] == "old row" and row["kind"] == "build" and row["attempt"] == 0
