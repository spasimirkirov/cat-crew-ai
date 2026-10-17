from pathlib import Path

from jinx_ai.trace import (
    bind_session,
    export_copy,
    load_events,
    log_event,
    render_markdown,
    write_markdown,
)


def test_trace_jsonl_and_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.trace.TRACES_DIR", tmp_path)
    monkeypatch.setattr("jinx_ai.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    bind_session("sess-1")
    log_event("turn_start", user="build the site", workspace=str(tmp_path))
    log_event(
        "tool",
        name="write_file",
        arguments={"path": "index.html"},
        result="Wrote index.html",
    )
    log_event("turn_end", reply="Done")
    events = load_events("sess-1")
    assert [item["kind"] for item in events] == ["turn_start", "tool", "turn_end"]
    md = render_markdown("sess-1", title="Test session")
    assert "build the site" in md
    assert "write_file" in md
    dest = export_copy("sess-1", tmp_path / "out.md")
    assert dest.is_file()
    written = write_markdown("sess-1", title="Test session")
    assert written.is_file()
    extra = tmp_path / "scratch" / "sess-1" / "activity.md"
    assert extra.is_file()
    bind_session("")


def test_run_agent_runs_full_printed_batch() -> None:
    from jinx_ai.providers.base import AssistantMessage, ToolCall
    from tests.test_run_agent import _Scripted

    tools = [
        {
            "type": "function",
            "function": {"name": "run_command", "parameters": {"type": "object"}},
        },
        {
            "type": "function",
            "function": {"name": "write_file", "parameters": {"type": "object"}},
        },
        {
            "type": "function",
            "function": {"name": "open_url", "parameters": {"type": "object"}},
        },
    ]
    busy = _Scripted(
        "qwen",
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        name="run_command",
                        arguments={"command": "npx create-react-app jinx-ai-website"},
                    ),
                    ToolCall(
                        name="write_file",
                        arguments={"path": "src/App.js", "content": "ok"},
                    ),
                    ToolCall(name="run_command", arguments={"command": "npm start"}),
                    ToolCall(name="open_url", arguments={"url": "http://localhost:3000"}),
                ],
            ),
            AssistantMessage(content="site is up"),
        ],
    )
    executed: list[str] = []

    def run_tool(name: str, arguments: dict) -> str:
        executed.append(name)
        return "ok"

    result = busy.run_agent(
        [{"role": "user", "content": "go"}],
        tools,
        run_tool,
    )
    assert executed == ["run_command", "write_file", "run_command", "open_url"]
    assert result == "site is up"


def test_run_agent_skips_duplicate_failed_call() -> None:
    from jinx_ai.providers.base import AssistantMessage, ToolCall
    from tests.test_run_agent import _Scripted

    tools = [
        {
            "type": "function",
            "function": {"name": "open_url", "parameters": {"type": "object"}},
        },
    ]
    busy = _Scripted(
        "qwen",
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(name="open_url", arguments={"url": "file:///nope.html"}),
                ],
            ),
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(name="open_url", arguments={"url": "file:///nope.html"}),
                ],
            ),
            AssistantMessage(content="gave up"),
        ],
    )
    executed = {"n": 0}

    def run_tool(name: str, arguments: dict) -> str:
        executed["n"] += 1
        return "Error: url must start with http:// or https://"

    result = busy.run_agent(
        [{"role": "user", "content": "go"}],
        tools,
        run_tool,
    )
    assert executed["n"] == 1
    assert result == "gave up"
