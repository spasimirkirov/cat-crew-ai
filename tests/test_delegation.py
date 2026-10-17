"""platform/delegation.py: the engine-routed bridge for chief.py's
delegate_to_bot/delegate_to_team, including the developer verify/auto-heal
loop (Master Architecture Document, section 5.3).

Runs a real ``npm run build`` (via the existing terminal.py/tools.py
pipeline) against a throwaway package.json whose build script fails once
then succeeds, so the auto-heal retry is exercised end to end rather than
mocked away.
"""

from __future__ import annotations

import json

from backend.harness.runtime import reset_provider, set_provider
from backend.platform.delegation import run_delegated
from backend.platform.models import Bot, Project, Registry
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall
from backend.services.workspace import current_folders, set_folders


class _Echo(BaseProvider):
    def chat(self, messages: list[Message], tools: list[dict] | None = None):
        return AssistantMessage(content="built it")

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


def _developer_setup(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "package.json").write_text(
        json.dumps(
            {
                "name": "app",
                "scripts": {"build": "test -f ok.marker || (touch ok.marker && exit 1)"},
            }
        ),
        encoding="utf-8",
    )
    bot = Bot(id="ekko", name="Ekko", role="developer", system_prompt="Build.")
    project = Project(id="p1", name="P", folders=[str(folder)])
    registry = Registry(bots={"ekko": bot}, projects={"p1": project})
    return bot, project, registry, folder


def test_run_delegated_developer_auto_heals_a_failing_build(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    bot, project, registry, folder = _developer_setup(tmp_path)
    # run_command's path guard only allows the current workspace/attached
    # folders — run_bot() sets/restores this around its OWN turn, but the
    # verify step runs after that turn returns, so the test pins it for
    # the whole call the same way a real attached project folder would be.
    saved = current_folders()
    set_folders([folder])
    token = set_provider(_Echo())
    try:
        result = run_delegated(bot, "build the thing", project=project, registry=registry)
    finally:
        reset_provider(token)
        set_folders(saved)
    assert "built it" in result
    assert "fixed after retry" in result
    assert (folder / "ok.marker").exists()  # the healing child run's "verify" actually ran the build


def test_run_delegated_non_developer_skips_verify(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    folder = tmp_path / "app2"
    folder.mkdir()
    bot = Bot(id="catcrew", name="CatCrewAI", role="designer", system_prompt="Design.")
    project = Project(id="p1", name="P", folders=[str(folder)])
    registry = Registry(bots={"catcrew": bot}, projects={"p1": project})
    token = set_provider(_Echo())
    try:
        result = run_delegated(bot, "sketch it", project=project, registry=registry)
    finally:
        reset_provider(token)
    assert "built it" in result
    assert "Harness bridge" not in result  # verify/heal never runs for a non-developer role


def test_run_delegated_developer_with_no_build_script_never_heals(tmp_path, monkeypatch) -> None:
    """No package.json / no build script means nothing to verify — a pass
    on the first check, no healing child spawned, no extra text appended."""
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    folder = tmp_path / "app3"
    folder.mkdir()
    bot = Bot(id="ekko", name="Ekko", role="developer", system_prompt="Build.")
    project = Project(id="p1", name="P", folders=[str(folder)])
    registry = Registry(bots={"ekko": bot}, projects={"p1": project})
    token = set_provider(_Echo())
    try:
        result = run_delegated(bot, "build the thing", project=project, registry=registry)
    finally:
        reset_provider(token)
    assert "built it" in result
    assert "Harness bridge" not in result


def test_run_delegated_no_provider_bound_reports_error() -> None:
    bot = Bot(id="ekko", name="Ekko", role="developer", system_prompt="Build.")
    project = Project(id="p1", name="P")
    registry = Registry(bots={"ekko": bot})
    result = run_delegated(bot, "x", project=project, registry=registry)
    assert "no chat provider" in result
