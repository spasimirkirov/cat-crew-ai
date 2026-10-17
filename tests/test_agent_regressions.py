"""End to end through the real run_bot / execute_tool path: the two failures that made every
cat end a turn with "I got stuck repeating the same failed tool".

1. Verify -> fix -> verify again. The same command must be allowed to run a second time once
   something changed in between.
2. The designer must be able to build the project it is styling (it used to have no
   run_command, so it kept calling a tool it was not given).
"""

from __future__ import annotations

import json

from backend.harness.runtime import reset_provider, set_provider
from backend.platform.delegation import run_delegated
from backend.platform.models import Bot, Project, Registry
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall
from backend.services.workspace import current_folders, set_folders
import backend.services.pending_restart as _pending_restart


class _Script(BaseProvider):
    display_name = "script"

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)
        self.tool_results: list[str] = []

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        self.tool_results = [str(m.get("content")) for m in messages if m.get("role") == "tool"]
        return self._replies.pop(0)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


def _call(name: str, **arguments) -> AssistantMessage:
    return AssistantMessage(content="", tool_calls=[ToolCall(name=name, arguments=arguments)])


def _setup(tmp_path, role: str, script: str):
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "package.json").write_text(
        json.dumps({"name": "site", "scripts": {"build": script}}), encoding="utf-8"
    )
    bot = Bot(id="b1", name="Cat", role=role, system_prompt="Work.")
    project = Project(id="p1", name="P", folders=[str(folder)])
    return bot, project, Registry(bots={"b1": bot}, projects={"p1": project}), folder


def _run(bot, project, registry, folder, provider) -> str:
    saved = current_folders()
    set_folders([folder])
    token = set_provider(provider)
    try:
        return run_delegated(bot, "do the task", project=project, registry=registry)
    finally:
        reset_provider(token)
        set_folders(saved)


def setup_function() -> None:
    _pending_restart.reset_runtime_state()


def test_qa_can_run_the_same_check_again_after_the_environment_changed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    bot, project, registry, folder = _setup(tmp_path, "qa", "test -f up.flag")
    check = {"command": "npm run build"}
    provider = _Script(
        [
            _call("run_command", **check),  # fails: the server is not up yet
            _call("run_command", command="touch up.flag"),  # bring it up
            _call("run_command", **check),  # the identical check must run again, and pass
            AssistantMessage(content="PASS build ok"),
        ]
    )
    result = _run(bot, project, registry, folder, provider)
    assert "PASS build ok" in result
    assert "got stuck" not in result
    assert (folder / "up.flag").exists()
    third = provider.tool_results[-1]
    assert not third.startswith("Skipped")  # it really ran


def test_designer_can_build_the_site_it_is_styling(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    bot, project, registry, folder = _setup(tmp_path, "designer", "touch built.marker")
    provider = _Script(
        [
            _call("write_file", path=str(folder / "styles.css"), content=".hero{color:red}"),
            _call("run_command", command="npm run build"),
            AssistantMessage(content="styled and built"),
        ]
    )
    result = _run(bot, project, registry, folder, provider)
    assert "styled and built" in result
    assert (folder / "built.marker").exists()
    assert not any("is not available" in r for r in provider.tool_results)


def test_a_missing_tool_error_lists_what_the_cat_does_have(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    bot, project, registry, folder = _setup(tmp_path, "designer", "true")
    provider = _Script([_call("delete_everything", path="x"), AssistantMessage(content="ok")])
    _run(bot, project, registry, folder, provider)
    assert any("Your tools are:" in r and "run_command" in r for r in provider.tool_results)


def test_the_healing_run_sees_the_verification_failure(tmp_path, monkeypatch) -> None:
    """A-02: the retry used to re-run the original task text with none of the build output."""
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    bot, project, registry, folder = _setup(tmp_path, "developer", "test -f ok.marker || (touch ok.marker && echo BOOM-MARKER && exit 1)")
    seen: list[str] = []

    class _Recorder(_Script):
        def chat(self, messages, tools=None):
            seen.append(" ".join(str(m.get("content")) for m in messages))
            return AssistantMessage(content="done")

    result = _run(bot, project, registry, folder, _Recorder([]))
    assert "fixed after retry" in result
    assert any("BOOM-MARKER" in text for text in seen[1:])  # the second run was told what failed
