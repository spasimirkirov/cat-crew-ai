"""Project checks beyond PHP: Python, TypeScript, Go; and the agent loop's end-of-turn check."""

from __future__ import annotations

import os
import stat

from backend import tools
from backend.harness import checkpoints, project_check
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall
from backend.services.workspace import set_workspace


def test_python_syntax_errors_are_reported_with_file_and_line(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(project_check.shutil, "which", lambda name: None)  # no ruff, php, npm, go
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "ok.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "bad.py").write_text("def f(:\n    pass\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.py").write_text("def (:")  # dependencies are not the project's
    result = project_check.check_project(tmp_path)
    assert result is not None and not result.ok
    assert [p for p in result.problems if p.startswith("Python syntax in pkg/bad.py:1:")]
    assert not any("junk.py" in p for p in result.problems)


def test_only_the_checks_the_changed_files_call_for_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(project_check.shutil, "which", lambda name: None)
    (tmp_path / "bad.py").write_text("def f(:\n")
    (tmp_path / "style.css").write_text("body{}")
    assert project_check.check_project(tmp_path, changed=["style.css"]) is None
    result = project_check.check_project(tmp_path, changed=["bad.py"])
    assert result is not None and result.problems


def test_typescript_errors_come_from_the_projects_own_tsc(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(project_check.shutil, "which", lambda name: None)
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}')
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    tsc = bin_dir / "tsc"
    tsc.write_text("#!/bin/sh\necho \"src/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.\"\nexit 2\n")
    tsc.chmod(tsc.stat().st_mode | stat.S_IEXEC)
    if os.name == "nt":  # the fake tsc is a shell script
        return
    result = project_check.check_project(tmp_path, changed=["src/a.ts"])
    assert result is not None
    assert "tsc --noEmit failed (1 error(s))" in result.problems[0]
    assert "error TS2322" in result.problems[0]


def test_verify_turn_nudges_and_the_caller_can_turn_it_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(project_check.shutil, "which", lambda name: None)
    (tmp_path / "bad.py").write_text("def f(:\n")
    nudge = project_check.verify_turn(tmp_path, ["bad.py"])
    assert nudge and nudge.startswith("Before you finish") and "bad.py" in nudge
    assert "did not touch" in nudge
    with project_check.checks_by_caller():
        assert project_check.verify_turn(tmp_path, ["bad.py"]) is None
    assert project_check.verify_turn(tmp_path, []) is None


class _Scripted(BaseProvider):
    def __init__(self, replies: list[AssistantMessage]) -> None:
        self.display_name = "scripted"
        self._replies = list(replies)
        self.prompts: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        self.prompts.append([dict(m) for m in messages])
        return self._replies.pop(0)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


_WRITE = [{"type": "function", "function": {"name": "write_file", "parameters": {"type": "object", "properties": {}}}}]


def _run_write(name: str, arguments: dict) -> str:
    from backend.harness import hooks

    ctx = hooks.ToolCallContext(name=name, arguments=arguments)
    hooks.run_before(ctx)
    return tools.write_file(**ctx.arguments)


def test_a_turn_that_broke_the_project_goes_back_to_the_agent_before_it_ends(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(project_check.shutil, "which", lambda name: None)
    set_workspace(tmp_path)
    agent = _Scripted(
        [
            AssistantMessage(content="", tool_calls=[ToolCall(name="write_file", arguments={"path": "app.py", "content": "def f(:\n"})]),
            AssistantMessage(content="Done."),
            AssistantMessage(content="", tool_calls=[ToolCall(name="write_file", arguments={"path": "app.py", "content": "def f():\n    return 1\n"})]),
            AssistantMessage(content="Fixed the syntax error; done."),
        ]
    )
    result = agent.run_agent([{"role": "user", "content": "write app.py"}], _WRITE, _run_write)
    assert result == "Fixed the syntax error; done."
    nudge = agent.prompts[2][-1]["content"]
    assert nudge.startswith("Before you finish") and "app.py:1" in nudge
    # The turn is one checkpoint, holding app.py as it was before (absent).
    [cp] = checkpoints.list_checkpoints(tmp_path)
    assert cp["files"] == ["app.py"]


def test_a_clean_turn_ends_without_a_second_round(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(project_check.shutil, "which", lambda name: None)
    set_workspace(tmp_path)
    agent = _Scripted(
        [
            AssistantMessage(content="", tool_calls=[ToolCall(name="write_file", arguments={"path": "app.py", "content": "x = 1\n"})]),
            AssistantMessage(content="Done."),
        ]
    )
    assert agent.run_agent([{"role": "user", "content": "go"}], _WRITE, _run_write) == "Done."
    assert len(agent.prompts) == 2
