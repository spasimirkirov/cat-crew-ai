from __future__ import annotations

from backend.platform import activity_board
from backend.platform.chief import (
    ChiefBot,
    describe_project,
    execute_chief_tool,
)
from backend.platform.presets import marketplace_builder, marketplace_registry
from backend.platform.registry import PlatformBuilder, reset_registry_cache
from backend.platform.runtime import reset_project_id, set_project_id
from backend.platform.toolset import builtin_names_for_role, mcp_selector_ids
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall
from backend.harness.runtime import reset_provider, set_provider
from backend.tools import tools_schema_for


def test_builder_roundtrip(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    plat = PlatformBuilder()
    plat.global_mcp("github")
    plat.bot("b1", "Bot", role="developer", prompt="You build.", mcp_tools=["github"])
    plat.project("p1", "P")
    plat.chief(name="Cat Crew", prompt="Route.")
    plat.save()
    reset_registry_cache()
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", store)
    from backend.platform.registry import load_registry

    loaded = load_registry(refresh=True)
    assert loaded.bots["b1"].mcp_tools == ["github"]
    assert loaded.global_mcp_tools == ["github"]
    assert loaded.chief.name == "Cat Crew"
    reset_registry_cache()


def test_role_packs_block_orchestration_tools() -> None:
    names = builtin_names_for_role("developer", [])
    assert "write_file" in names
    assert "list_roster" in names
    assert "get_member" in names
    assert "delegate_to_bot" not in names
    assert "restart_catcrew" not in names
    qa = builtin_names_for_role("qa", None)
    assert "run_command" in qa
    assert "write_file" in qa  # QA writes the automated tests it re-runs


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
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    marketplace_builder().save()
    try:
        names = {
            item["function"]["name"]
            for item in tools_schema_for("developer", project_id="marketplace")
        }
        # Phase 3 pivot: Chief has no delegate tool any more — platform/sop.py
        # drives specialists deterministically instead of an LLM choosing to
        # delegate. Chief keeps only read/lookup tools.
        assert "delegate_to_bot" not in names
        assert "delegate_to_team" not in names
        assert "list_project" in names
        assert "list_roster" in names
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
        assert "get_member" in one
        assert "delegate_to_bot" not in one
    finally:
        reset_registry_cache()


def test_delegate_round_budget_ignores_a_low_cap(monkeypatch) -> None:
    from types import SimpleNamespace

    from backend.platform.runner import delegate_round_budget

    monkeypatch.setattr(
        "backend.platform.runner.agent_settings",
        lambda: {"max_rounds": 24},
    )
    bot = SimpleNamespace(max_rounds=8)
    assert delegate_round_budget(5, bot=bot) == 24
    assert delegate_round_budget(0, bot=bot) == 24
    assert delegate_round_budget(40, bot=bot) == 40


def test_delegate_round_budget_defaults_to_a_finite_cap_when_settings_zero(monkeypatch) -> None:
    """Settings 0 (unlimited) still gives delegated work the DELEGATE_MAX_ROUNDS default."""
    from types import SimpleNamespace

    from backend.platform.runner import delegate_round_budget

    monkeypatch.setattr(
        "backend.platform.runner.agent_settings",
        lambda: {"max_rounds": 0},
    )
    monkeypatch.setattr("backend.config.DELEGATE_MAX_ROUNDS", 40)
    bot = SimpleNamespace(max_rounds=8)
    assert delegate_round_budget(0, bot=bot) == 40
    assert delegate_round_budget(5, bot=bot) == 5  # an explicit request wins


def test_delegate_round_budget_zero_default_means_unlimited(monkeypatch) -> None:
    from types import SimpleNamespace

    from backend.platform.runner import delegate_round_budget

    monkeypatch.setattr("backend.platform.runner.agent_settings", lambda: {"max_rounds": 0})
    monkeypatch.setattr("backend.config.DELEGATE_MAX_ROUNDS", 0)
    assert delegate_round_budget(0, bot=SimpleNamespace(max_rounds=8)) == 0


def test_project_folders_roundtrip() -> None:
    from backend.platform.models import Project

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
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    computer = tmp_path / "app"
    computer.mkdir()
    try:
        PlatformBuilder().project("p1", "P", folders=[str(computer)]).save()
        from backend.platform.registry import load_registry

        loaded = load_registry(refresh=True)
        assert loaded.projects["p1"].folders == [str(computer)]
    finally:
        reset_registry_cache()


def test_delegated_bot_prompt_includes_folder_and_readme(tmp_path, monkeypatch) -> None:
    from backend.platform.runner import _project_context

    store = tmp_path / "registry.json"
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    computer = tmp_path / "site"
    computer.mkdir()
    (computer / "README.md").write_text("# Shop\nLanding page.", encoding="utf-8")
    monkeypatch.setattr("backend.services.workspace.current_folders", lambda: [computer])
    monkeypatch.setattr("backend.agent_context.current_folders", lambda: [computer])
    try:
        plat = PlatformBuilder()
        plat.bot("ekko", "Ekko", role="developer", prompt="Build.")
        plat.project("p1", "Shop", folders=[str(computer)])
        registry = plat.save()
        project = registry.projects["p1"]
        project.state["kickoff"] = "Build a Team Arcane landing page."
        text = _project_context(project, registry)
        assert str(computer) in text
        # the user's brief is added by run_bot ("User brief:"), not by this project block
        assert "Shop" in text or "Landing page" in text
    finally:
        reset_registry_cache()


def test_qa_delegation_gets_an_explicit_verdict_banner(tmp_path, monkeypatch) -> None:
    """UI & Agent Visibility (Master Architecture Document Phase 2): a QA
    bot's result must be unmistakable in Chief's feed, not blend in with
    every other delegate_to_bot result — see chief.py::_format_delegation_result.
    Also covers the sidebar's "Chief is delegating to X" activity line,
    which is a plain in-memory board so this can assert on it directly
    instead of driving the Qt UI."""
    store = tmp_path / "registry.json"
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    marketplace_builder().save()
    token = set_project_id("marketplace")
    activity_board.reset()

    class _Echo(BaseProvider):
        def chat(self, messages: list[Message], tools: list[dict] | None = None):
            return AssistantMessage(content="All checks pass. No regressions found.")

        def tool_message(self, tool_call: ToolCall, result: str) -> Message:
            return {"role": "tool", "name": tool_call.name, "content": result}

    ptok = set_provider(_Echo())
    try:
        out = execute_chief_tool(
            "delegate_to_bot",
            {"bot_id": "qa", "task": "Verify the build."},
        )
    finally:
        reset_provider(ptok)
        reset_project_id(token)
        reset_registry_cache()
    assert "=== QA VERDICT — QA ===" in out
    assert "=== END QA VERDICT ===" in out
    assert "All checks pass" in out
    # Cat Crew's own sidebar activity line was set while the delegate call was
    # in flight (and touched again once it returned) — not asserting the
    # exact live text since the call already finished by the time we check,
    # only that something was recorded (never seen before activity_board.reset()).
    text, ts = activity_board.get_activity("crew")  # Cat Crew is the orchestrator's activity key
    assert ts > 0


def test_non_qa_delegation_is_not_wrapped_in_a_verdict_banner(tmp_path, monkeypatch) -> None:
    store = tmp_path / "registry.json"
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    marketplace_builder().save()
    token = set_project_id("marketplace")

    class _Echo(BaseProvider):
        def chat(self, messages: list[Message], tools: list[dict] | None = None):
            return AssistantMessage(content="wireframes ready")

        def tool_message(self, tool_call: ToolCall, result: str) -> Message:
            return {"role": "tool", "name": tool_call.name, "content": result}

    ptok = set_provider(_Echo())
    try:
        out = execute_chief_tool(
            "delegate_to_bot",
            {"bot_id": "designer", "task": "Sketch the listing page."},
        )
    finally:
        reset_provider(ptok)
        reset_project_id(token)
        reset_registry_cache()
    assert "QA VERDICT" not in out
