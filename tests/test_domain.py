"""Unit tests for the new backend.domain package (pure data, no I/O)."""

from __future__ import annotations

from backend.domain import (
    AgentRun,
    Bot,
    ChiefSpec,
    Project,
    Registry,
    RunKind,
    RunStatus,
    Team,
)


def test_bot_roundtrip_matches_legacy_shape() -> None:
    bot = Bot.from_dict(
        {
            "id": "ekko",
            "name": "Ekko",
            "role": "Developer",
            "prompt": "Build things.",
            "mcp": ["github"],
            "max_rounds": 6,
        }
    )
    assert bot.role == "developer"
    assert bot.system_prompt == "Build things."
    assert bot.mcp_tools == ["github"]
    dumped = bot.to_dict()
    again = Bot.from_dict(dumped)
    assert again == bot


def test_team_and_registry_lookups() -> None:
    lead = Bot(id="jayce", name="Jayce", role="lead", system_prompt="x")
    dev = Bot(id="ekko", name="Ekko", role="developer", system_prompt="x")
    team = Team(id="arcane", name="Arcane", bot_ids=["jayce", "ekko"], lead_bot_id="jayce")
    project = Project(id="p1", name="P", team_ids=["arcane"])
    registry = Registry(bots={"jayce": lead, "ekko": dev}, teams={"arcane": team}, projects={"p1": project})
    assert registry.bot_members(team) == [lead, dev]
    assert registry.assigned_teams(project) == [team]
    assert registry.project_bots(project) == [lead, dev]


def test_project_remember_caps_events() -> None:
    project = Project(id="p1", name="P")
    for i in range(5):
        project.remember(f"event {i}", role="bot", bot_id="ekko", cap=3)
    assert len(project.events) == 3
    assert [e.text for e in project.events] == ["event 2", "event 3", "event 4"]


def test_chief_spec_as_bot() -> None:
    chief = ChiefSpec(name="Chief", system_prompt="Route.")
    bot = chief.as_bot()
    assert bot.id == "crew"
    assert bot.role == "orchestrator"
    assert bot.team_id is None


def test_agent_run_lifecycle() -> None:
    run = AgentRun.new(project_id="p1", bot_id="ekko", kind=RunKind.DELEGATED, task="build it")
    assert run.status == RunStatus.QUEUED
    assert not run.is_terminal()
    run.mark_running()
    assert run.status == RunStatus.RUNNING
    run.mark_done("done!")
    assert run.is_terminal()
    assert run.summary == "done!"
    assert run.ended_at is not None
    dumped = run.to_dict()
    again = AgentRun.from_dict(dumped)
    assert again.status == RunStatus.DONE
    assert again.kind == RunKind.DELEGATED


def test_agent_run_parent_child_link() -> None:
    parent = AgentRun.new(project_id="p1", bot_id="chief", kind=RunKind.CHIEF, task="ship it")
    child = AgentRun.new(
        project_id="p1", bot_id="ekko", kind=RunKind.DELEGATED, task="build it",
        parent_run_id=parent.id,
    )
    assert child.parent_run_id == parent.id
