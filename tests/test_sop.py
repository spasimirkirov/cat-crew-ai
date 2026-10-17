"""platform/sop.py: the Project Room's deterministic SOP pipeline (Master
Architecture Document Phase 3 — replaces an LLM Chief that decided,
turn by turn, whether to delegate). Covers the three concrete complaints
from the live session that triggered the pivot: Chief as a bottleneck
(fixed — there is no Chief turn, phases run automatically), a bot's tool
calls invisible to the UI (fixed — collect_tool_events), and QA/DevOps
getting skipped (fixed — a fixed phase order, not an LLM's judgment call).
"""

from __future__ import annotations

import pytest

from backend.harness.runtime import reset_provider, set_provider
from backend.platform.models import Bot, Project, Registry
from backend.platform import sop as sop_mod
from backend.platform.sop import parse_mention, run_project_room_turn
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall


@pytest.fixture(autouse=True)
def _own_ledger(isolated):
    """Every SOP run writes task rows: keep them in tmp_path, never in the real data/platform ledger."""
    yield


class _Echo(BaseProvider):
    def chat(self, messages: list[Message], tools: list[dict] | None = None):
        return AssistantMessage(content="ack")

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


class _Worker(_Echo):
    """Writes one file when it may (a producing phase must change something on disk), then acks."""

    def chat(self, messages: list[Message], tools: list[dict] | None = None):
        names = {t.get("function", {}).get("name") for t in tools or []}
        if "write_file" in names and messages and messages[-1].get("role") != "tool":
            return AssistantMessage(
                content=None,
                tool_calls=[ToolCall(name="write_file", arguments={"path": "notes.txt", "content": "x"}, id="c1")],
            )
        return AssistantMessage(content="PASS: ack")


def _arcane_like_registry() -> Registry:
    bots = {
        "heimerdinger": Bot(id="heimerdinger", name="Heimerdinger", role="architect", system_prompt="Plan."),
        "jayce": Bot(id="jayce", name="Jayce", role="lead", system_prompt="Lead."),
        "singed": Bot(id="singed", name="Singed", role="devops", system_prompt="Ops."),
        "ekko": Bot(id="ekko", name="Ekko", role="developer", system_prompt="Build."),
        "catcrew": Bot(id="catcrew", name="CatCrewAI", role="designer", system_prompt="Style."),
        "caitlyn": Bot(id="caitlyn", name="Caitlyn", role="qa", system_prompt="Verify."),
        "mel": Bot(id="mel", name="Mel", role="legal", system_prompt="Advise."),
        "silco": Bot(id="silco", name="Silco", role="accountant", system_prompt="Count."),
    }
    project = Project(id="p1", name="P", bot_ids=list(bots))
    return Registry(bots=bots, projects={"p1": project})


def _catcrew_registry() -> Registry:
    """The current pipeline roles (platform/sop.PHASE_ROLES): Cipher writes the blueprint,
    Vanguard runs both architecture and distribution, then DevOps, build, design, QA."""
    bots = {
        "cipher": Bot(id="cipher", name="Cipher", role="prompt_engineer", system_prompt="Blueprint."),
        "vanguard": Bot(id="vanguard", name="Vanguard", role="vanguard", system_prompt="Lead."),
        "forge": Bot(id="forge", name="Forge", role="devops", system_prompt="Ops."),
        "pulse": Bot(id="pulse", name="Pulse", role="developer", system_prompt="Build."),
        "pixel": Bot(id="pixel", name="Pixel", role="designer", system_prompt="Style."),
        "aegis": Bot(id="aegis", name="Aegis", role="qa", system_prompt="Verify."),
        "lex": Bot(id="lex", name="Lex", role="legal", system_prompt="Advise."),
        "blaze": Bot(id="blaze", name="Blaze", role="accountant", system_prompt="Count."),
    }
    project = Project(id="p1", name="P", bot_ids=list(bots))
    return Registry(bots=bots, projects={"p1": project})


def test_full_pipeline_runs_every_phase_in_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    registry = _catcrew_registry()
    project = registry.projects["p1"]
    token = set_provider(_Worker())
    try:
        turns = run_project_room_turn("Build a landing page.", project=project, registry=registry)
    finally:
        reset_provider(token)
    phases = [(t.phase, t.bot.id) for t in turns]
    assert phases == [
        ("blueprint", "cipher"),
        ("architecture", "vanguard"),
        ("distribution", "vanguard"),
        ("devops", "forge"),
        ("build", "pulse"),
        ("design", "pixel"),
        ("qa", "aegis"),
    ]
    assert project.state["sop_phase"] == "done"
    # every bot's turn also landed in shared project memory, not just the UI
    remembered_bots = {e.bot_id for e in project.events}
    assert remembered_bots == {bot_id for _, bot_id in phases}


def test_mention_routes_directly_and_skips_the_pipeline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    registry = _arcane_like_registry()
    project = registry.projects["p1"]
    token = set_provider(_Echo())
    try:
        turns = run_project_room_turn("@Ekko fix the padding", project=project, registry=registry)
    finally:
        reset_provider(token)
    assert len(turns) == 1
    assert turns[0].bot.id == "ekko"
    assert "sop_phase" not in project.state


def test_spontaneous_legal_and_budget_bots_join_after_distribution(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    registry = _catcrew_registry()
    project = registry.projects["p1"]
    token = set_provider(_Echo())
    try:
        turns = run_project_room_turn(
            "Build a checkout flow. Also draft a privacy policy and set our pricing.",
            project=project,
            registry=registry,
        )
    finally:
        reset_provider(token)
    phases = [t.phase for t in turns]
    ids = [t.bot.id for t in turns]
    assert "lex" in ids
    assert "blaze" in ids
    dist_idx = phases.index("distribution")
    # Legal and budget join right after Vanguard hands out the work, before DevOps.
    assert set(ids[dist_idx + 1 : dist_idx + 3]) == {"lex", "blaze"}
    assert phases[dist_idx + 1 : dist_idx + 3] == ["spontaneous", "spontaneous"]
    assert phases[dist_idx + 3] == "devops"


def test_no_spontaneous_bots_when_brief_is_plain(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    registry = _arcane_like_registry()
    project = registry.projects["p1"]
    token = set_provider(_Echo())
    try:
        turns = run_project_room_turn("Build a landing page.", project=project, registry=registry)
    finally:
        reset_provider(token)
    ids = {t.bot.id for t in turns}
    assert "mel" not in ids
    assert "silco" not in ids


def test_parse_mention_matches_name_or_id() -> None:
    registry = _arcane_like_registry()
    bot = parse_mention("hey @caitlyn can you check this", registry)
    assert bot is not None and bot.id == "caitlyn"
    bot2 = parse_mention("@Jayce status?", registry)
    assert bot2 is not None and bot2.id == "jayce"
    assert parse_mention("no mention here", registry) is None


def test_tool_events_are_collected_per_bot(tmp_path, monkeypatch) -> None:
    """A bot's tool call becomes a visible event line instead of a silent
    background call — the concrete UI-visibility complaint the pivot fixes."""
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    registry = _arcane_like_registry()
    project = registry.projects["p1"]

    class _ToolUser(BaseProvider):
        def __init__(self) -> None:
            self._called = False

        def chat(self, messages: list[Message], tools: list[dict] | None = None):
            if not self._called:
                self._called = True
                return AssistantMessage(
                    content="",
                    tool_calls=[ToolCall(name="system_time", arguments={})],
                )
            return AssistantMessage(content="done")

        def tool_message(self, tool_call: ToolCall, result: str) -> Message:
            return {"role": "tool", "name": tool_call.name, "content": result}

    token = set_provider(_ToolUser())
    try:
        turns = run_project_room_turn("@Jayce plan this", project=project, registry=registry)
    finally:
        reset_provider(token)
    assert turns[0].tool_events
    assert "Jayce executed system_time" in turns[0].tool_events[0]


def test_a_round_capped_work_order_ends_blocked(tmp_path, monkeypatch) -> None:
    """A cat that runs out of its tool-round budget does not close its task as done."""
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    registry = _catcrew_registry()
    project = registry.projects["p1"]
    results: list[tuple[str, str]] = []
    monkeypatch.setattr("backend.platform.sop.db.create_task", lambda *a, **k: "t-" + a[1])
    monkeypatch.setattr("backend.platform.sop.db.update_task_status", lambda *a, **k: None)
    monkeypatch.setattr("backend.platform.sop.db.ensure_project", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.platform.sop.db.add_task_result",
        lambda task_id, state, summary, *a: results.append((task_id, state)),
    )

    class _Busy(BaseProvider):
        def chat(self, messages: list[Message], tools: list[dict] | None = None):
            return AssistantMessage(content="", tool_calls=[ToolCall(name="system_time", arguments={})])

        def tool_message(self, tool_call: ToolCall, result: str) -> Message:
            return {"role": "tool", "name": tool_call.name, "content": result}

    monkeypatch.setattr("backend.platform.runner.agent_settings", lambda: {"max_rounds": 0})
    monkeypatch.setitem(sop_mod.PHASE_BUDGETS, "mention", 2)
    monkeypatch.setattr(sop_mod, "_STALL_PHASES", frozenset())
    token = set_provider(_Busy())
    try:
        turns = run_project_room_turn("@Pulse build it", project=project, registry=registry)
    finally:
        reset_provider(token)
    assert turns[0].round_capped
    assert "reached the most one work order may take (2 tool rounds)" in turns[0].text
    assert "send continue" not in turns[0].text
    assert results == [("t-pulse", "blocked")]


def test_a_build_that_wrote_nothing_gets_one_more_go_then_the_crew_stops(tmp_path, monkeypatch) -> None:
    """Pulse read composer.json and stopped, run after run, and every phase still said done."""
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    registry = _catcrew_registry()
    project = registry.projects["p1"]
    token = set_provider(_Echo())  # talks, never writes
    try:
        turns = run_project_room_turn("Build a landing page.", project=project, registry=registry)
    finally:
        reset_provider(token)
    phases = [t.phase for t in turns]
    assert phases == ["blueprint", "architecture", "distribution", "devops", "devops"]  # one redo, then stop
    assert not turns[-1].verified and turns[-1].ledger_status == "unverified"
    assert "crew stopped after devops" in turns[-1].text
    assert project.state["sop_phase"] != "done"


def test_a_qa_reply_without_a_verdict_is_not_a_pass() -> None:
    from backend.platform.sop import _qa_verdict_failed

    leaked = "=== QA VERDICT — Aegis ===\n[Aegis / qa]\n<|channel|>commentary to=functions.run_smoke_test<|call|>\n=== END QA VERDICT ==="
    assert _qa_verdict_failed(leaked)
    assert _qa_verdict_failed("[Aegis / qa]\nFAIL: /gallery is a 404")
    assert not _qa_verdict_failed("[Aegis / qa]\n**PASS** -- every page renders")
