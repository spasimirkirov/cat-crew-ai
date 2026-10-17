from __future__ import annotations

from jinx_ai.platform.chief import (
    ChiefBot,
    describe_project,
    execute_chief_tool,
)
from jinx_ai.platform.presets import marketplace_builder, marketplace_registry
from jinx_ai.platform.registry import PlatformBuilder, reset_registry_cache
from jinx_ai.platform.runtime import reset_project_id, set_project_id
from jinx_ai.platform.toolset import builtin_names_for_role, mcp_selector_ids
from jinx_ai.providers.base import AssistantMessage, BaseProvider, Message, ToolCall
from jinx_ai.harness.runtime import reset_provider, set_provider
from jinx_ai.tools import tools_schema_for


def test_marketplace_roster() -> None:
    registry = marketplace_registry()
    assert "software-eng" in registry.teams
    assert "accountant" in registry.teams
    se = registry.teams["software-eng"]
    assert se.lead_bot_id == "architect"
    names = {bot.role for bot in registry.bot_members(se)}
    assert names == {"architect", "designer", "developer", "qa"}
    project = registry.projects["marketplace"]
    assert project.team_ids == ["software-eng", "accountant"]
    assert len(registry.project_bots(project)) == 5
    registry.projects["marketplace"].bot_ids.append("architect")
    assert len(registry.project_bots(project)) == 5
    chief = registry.chief.as_bot()
    assert chief.team_id is None
    assert chief.role == "chief"
    assert ChiefBot(registry.chief).id == "chief"


def test_builder_roundtrip(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    plat = PlatformBuilder()
    plat.global_mcp("github")
    plat.team("t1", "T", "d")
    plat.bot("b1", "Bot", role="developer", prompt="You build.", mcp_tools=["github"])
    plat.project("p1", "P", teams=["t1"])
    plat.chief(name="Chief", prompt="Route.")
    plat.save()
    reset_registry_cache()
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    from jinx_ai.platform.registry import load_registry

    loaded = load_registry(refresh=True)
    assert loaded.bots["b1"].mcp_tools == ["github"]
    assert loaded.global_mcp_tools == ["github"]
    assert loaded.projects["p1"].team_ids == ["t1"]
    assert loaded.chief.name == "Chief"
    reset_registry_cache()


def test_role_packs_block_orchestration_tools() -> None:
    names = builtin_names_for_role("developer", [])
    assert "write_file" in names
    assert "list_roster" in names
    assert "list_teams" in names
    assert "get_member" in names
    assert "delegate_to_bot" not in names
    assert "restart_jinx" not in names
    qa = builtin_names_for_role("qa", None)
    assert "run_command" in qa
    assert "write_file" not in qa


def test_mcp_selectors_global_and_specific() -> None:
    ids, filt = mcp_selector_ids(
        ["github:list_issues"],
        global_mcp=["slack"],
        inherit_global=True,
    )
    assert "github" in ids
    assert "slack" in ids
    assert "list_issues" in filt
    ids2, filt2 = mcp_selector_ids(
        ["*"],
        global_mcp=[],
        inherit_global=False,
    )
    assert filt2 == set()
    # wildcard expands to enabled connectors (may be empty in tests)
    assert isinstance(ids2, list)


def test_chief_schema_when_project_bound(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    marketplace_builder().save()
    try:
        names = {
            item["function"]["name"]
            for item in tools_schema_for("developer", project_id="marketplace")
        }
        assert "delegate_to_bot" in names
        assert "delegate_to_team" in names
        assert "list_project" in names
        assert "list_roster" in names
        assert "list_teams" in names
        assert "list_members" in names
        assert "get_member" in names
        assert "write_file" not in names
        assert "run_command" not in names
        solo = {item["function"]["name"] for item in tools_schema_for("developer")}
        assert "write_file" in solo
        assert "delegate_to_bot" not in solo
        one = {
            item["function"]["name"]
            for item in tools_schema_for(
                "developer", project_id="marketplace", bot_id="developer"
            )
        }
        assert "write_file" in one
        assert "run_command" in one
        assert "list_roster" in one
        assert "list_teams" in one
        assert "get_member" in one
        assert "delegate_to_bot" not in one
    finally:
        reset_registry_cache()


def test_delegate_schema_omits_max_rounds(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    marketplace_builder().save()
    try:
        schema = tools_schema_for("developer", project_id="marketplace")
        by_name = {item["function"]["name"]: item["function"] for item in schema}
        for name in ("delegate_to_bot", "delegate_to_team"):
            props = (by_name[name].get("parameters") or {}).get("properties") or {}
            assert "max_rounds" not in props
    finally:
        reset_registry_cache()


def test_delegate_round_budget_ignores_a_low_cap(monkeypatch) -> None:
    from types import SimpleNamespace

    from jinx_ai.platform.runner import delegate_round_budget

    monkeypatch.setattr(
        "jinx_ai.platform.runner.agent_settings",
        lambda: {"max_rounds": 24},
    )
    bot = SimpleNamespace(max_rounds=8)
    assert delegate_round_budget(5, bot=bot) == 24
    assert delegate_round_budget(0, bot=bot) == 24
    assert delegate_round_budget(40, bot=bot) == 40


def test_team_task_skips_lead_when_build_roles_exist() -> None:
    from jinx_ai.platform.chief import bots_for_team_task
    from jinx_ai.platform.models import Bot, Registry, Team

    lead = Bot(id="jayce", name="Jayce", role="lead", system_prompt="x")
    arch = Bot(id="heimerdinger", name="Heimerdinger", role="architect", system_prompt="x")
    dev = Bot(id="ekko", name="Ekko", role="developer", system_prompt="x")
    team = Team(id="arcane", name="Arcane", bot_ids=["jayce", "heimerdinger", "ekko"], lead_bot_id="jayce")
    registry = Registry(bots={"jayce": lead, "heimerdinger": arch, "ekko": dev}, teams={"arcane": team})
    chosen, note = bots_for_team_task(team, [lead, arch, dev], registry)
    assert [bot.id for bot in chosen] == ["heimerdinger", "ekko"]
    assert "Jayce" not in note
    assert "Heimerdinger" in note


def test_delegate_round_budget_unlimited_when_settings_zero(monkeypatch) -> None:
    from types import SimpleNamespace

    from jinx_ai.platform.runner import delegate_round_budget

    monkeypatch.setattr(
        "jinx_ai.platform.runner.agent_settings",
        lambda: {"max_rounds": 0},
    )
    bot = SimpleNamespace(max_rounds=8)
    assert delegate_round_budget(0, bot=bot) == 0
    assert delegate_round_budget(5, bot=bot) == 0


def test_list_project_and_delegate(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    registry = marketplace_builder().save()
    token = set_project_id("marketplace")
    try:
        text = execute_chief_tool("list_project", {})
        assert "Software Engineering Team" in text
        assert "Architect" in text
        assert "Accountant Team" in text
        bots = execute_chief_tool("list_bots", {"role": "qa"})
        assert "qa" in bots.lower()
        execute_chief_tool("update_project_state", {"key": "phase", "value": "design"})
        from jinx_ai.platform.registry import load_registry

        assert load_registry(refresh=True).projects["marketplace"].state["phase"] == "design"

        class _Echo(BaseProvider):
            def chat(self, messages: list[Message], tools: list[dict] | None = None):
                return AssistantMessage(content="wireframes ready")

            def tool_message(self, tool_call: ToolCall, result: str) -> Message:
                return {"role": "tool", "name": tool_call.name, "content": result}

        provider = _Echo()
        ptok = set_provider(provider)
        try:
            out = execute_chief_tool(
                "delegate_to_bot",
                {"bot_id": "designer", "task": "Sketch the listing page."},
            )
        finally:
            reset_provider(ptok)
        assert "Designer" in out
        assert "wireframes ready" in out
        unknown = execute_chief_tool(
            "delegate_to_bot",
            {"bot_id": "nope", "task": "x"},
        )
        assert "Unknown bot" in unknown
        roster = describe_project(registry.projects["marketplace"], registry)
        assert "Assigned teams" in roster
        assert "Shared computer" in roster
    finally:
        reset_project_id(token)
        reset_registry_cache()


def test_delete_team_drops_project_assignment(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    marketplace_builder().save()
    from jinx_ai.platform.registry import delete_team, load_registry

    try:
        delete_team("software-eng")
        registry = load_registry(refresh=True)
        assert "software-eng" not in registry.teams
        assert "software-eng" not in registry.projects["marketplace"].team_ids
        assert "architect" in registry.bots
    finally:
        reset_registry_cache()


def test_arcane_seed_upserts_team_and_keeps_photos(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    from jinx_ai.platform.arcane import ARCANE_MEMBERS, seed_arcane
    from jinx_ai.platform.registry import load_registry, save_registry

    try:
        registry = seed_arcane(replace_catalog=True)
        assert "arcane" in registry.teams
        assert registry.teams["arcane"].name == "Arcane"
        assert registry.teams["arcane"].lead_bot_id == "jayce"
        assert len(registry.teams["arcane"].bot_ids) == len(ARCANE_MEMBERS)
        jayce = registry.bots["jayce"]
        assert jayce.role == "lead"
        assert jayce.title == "Team Lead"
        assert "planning" in jayce.skills
        assert jayce.description.startswith("Visionary")
        assert registry.bots["jinx"].role == "designer"
        assert registry.bots["braum"].role == "morale"
        assert registry.bots["blitzcrank"].role == "assistant"
        jayce.avatar = str(tmp_path / "jayce.png")
        save_registry(registry)
        reset_registry_cache()
        again = seed_arcane(replace_catalog=False)
        assert again.bots["jayce"].avatar.endswith("jayce.png")
        assert again.bots["ekko"].title == "Rapid Prototyper & Dev"
    finally:
        reset_registry_cache()


def test_project_folders_roundtrip() -> None:
    from jinx_ai.platform.models import Project

    row = Project.from_dict(
        {"id": "p1", "name": "Shop", "workspace": "/sites/shop", "folders": []}
    )
    assert row.folders == ["/sites/shop"]
    dumped = row.to_dict()
    assert dumped["folders"] == ["/sites/shop"]
    again = Project.from_dict(dumped)
    assert again.folders == ["/sites/shop"]


def test_builder_persists_project_folders(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    computer = tmp_path / "app"
    computer.mkdir()
    try:
        PlatformBuilder().project("p1", "P", folders=[str(computer)]).save()
        from jinx_ai.platform.registry import load_registry

        loaded = load_registry(refresh=True)
        assert loaded.projects["p1"].folders == [str(computer)]
    finally:
        reset_registry_cache()


def test_delegated_bot_prompt_includes_folder_and_kickoff(tmp_path, monkeypatch) -> None:
    from jinx_ai.platform.runner import _project_context

    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    computer = tmp_path / "site"
    computer.mkdir()
    (computer / "README.md").write_text("# Shop\nLanding page.", encoding="utf-8")
    monkeypatch.setattr("jinx_ai.workspace.current_folders", lambda: [computer])
    monkeypatch.setattr("jinx_ai.agent_context.current_folders", lambda: [computer])
    try:
        plat = PlatformBuilder()
        plat.team("t1", "Arcane")
        plat.bot("ekko", "Ekko", role="developer", prompt="Build.")
        plat.project("p1", "Shop", teams=["t1"], folders=[str(computer)])
        registry = plat.save()
        project = registry.projects["p1"]
        project.state["kickoff"] = "Build a Team Arcane landing page."
        text = _project_context(project, registry)
        assert str(computer) in text
        assert "Team Arcane landing page" in text
        assert "Shop" in text or "Landing page" in text
    finally:
        reset_registry_cache()


def test_list_roster_is_a_lookup_tool(tmp_path, monkeypatch) -> None:
    from jinx_ai.platform.arcane import seed_arcane
    from jinx_ai.platform.runtime import reset_project_id, set_project_id
    from jinx_ai.tools import execute_tool

    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    token = set_project_id("site")
    try:
        registry = seed_arcane(replace_catalog=True)
        PlatformBuilder(registry).project("site", "Site", teams=["arcane"]).save()
        everyone = execute_tool("list_roster", {})
        assert "Jayce" in everyone
        assert "Visionary leader" in everyone
        assert "Ekko" in everyone
        teams = execute_tool("list_teams", {})
        assert "arcane" in teams
        assert "13 member" in teams
        assert "Visionary leader" not in teams
        members = execute_tool("list_members", {"team_id": "arcane"})
        assert "jayce" in members
        assert "Ekko" in members
        assert "Visionary leader" not in members
        dossier = execute_tool("get_member", {"bot_id": "Jayce"})
        assert "Team Lead" in dossier
        assert "Visionary leader" in dossier
        one = execute_tool("list_roster", {"bot_id": "jinx"})
        assert "UX & Visual Designer" in one
        assert "explosive creativity" in one
        assert "Jayce" not in one
        missing = execute_tool("list_roster", {"bot_id": "nope"})
        assert "Unknown member" in missing
        team = execute_tool("list_roster", {"team_id": "arcane"})
        assert "Team Arcane" in team
        assert "Caitlyn" in team
    finally:
        reset_project_id(token)
        reset_registry_cache()
