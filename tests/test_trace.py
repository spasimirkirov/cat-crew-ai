from pathlib import Path

from backend.services.trace import bind_session, export_copy, load_events, log_event, render_markdown, write_markdown


def test_trace_jsonl_and_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.trace.TRACES_DIR", tmp_path)
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
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
    from backend.providers.base import AssistantMessage, ToolCall
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
                        arguments={"command": "npx create-react-app cat-crew-ai-website"},
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
    from backend.providers.base import AssistantMessage, ToolCall
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


def test_one_trace_row_per_call_with_the_model_id_and_a_model_call_record(tmp_path, monkeypatch) -> None:
    from backend.providers.base import AssistantMessage, BaseProvider, ToolCall

    monkeypatch.setattr("backend.services.trace.TRACES_DIR", tmp_path)

    class _Metered(BaseProvider):
        model = "anthropic/claude-sonnet-4"
        host_id = "openrouter"

        def __init__(self) -> None:
            self.replies = [
                AssistantMessage(content="", tool_calls=[ToolCall("read_file", {"path": "a"}, id="toolu_42")]),
                AssistantMessage(content="done"),
            ]

        def chat(self, messages, tools=None):
            self.last_usage = {"prompt_tokens": 1200, "completion_tokens": 30, "prompt_tokens_details": {"cached_tokens": 1000}}
            return self.replies.pop(0)

        def tool_message(self, tool_call, result):
            return {"role": "tool", "tool_call_id": tool_call.id, "content": result}

    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    bind_session("sess-calls")
    try:
        _Metered().run_agent([{"role": "user", "content": "go"}], tools, lambda n, a: "body")
    finally:
        bind_session("")
    events = load_events("sess-calls")
    assert [e["kind"] for e in events].count("tool") == 1
    call = next(e for e in events if e["kind"] == "tool_call")
    assert call["call_id"] == "toolu_42"
    calls = [e for e in events if e["kind"] == "model_call"]
    assert len(calls) == 2 and calls[0]["cached_tokens"] == "1000" and calls[0]["model"] == "anthropic/claude-sonnet-4"
    assert "1000 cached" in render_markdown("sess-calls")


def test_credentials_never_reach_a_trace(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.trace.TRACES_DIR", tmp_path)
    bind_session("sess-secret")
    try:
        log_event(
            "tool",
            name="connector__github__create_issue",
            arguments={"token": "ghp_" + "a" * 36, "title": "bug"},
            result="curl -H 'Authorization: Bearer abcdefghijklmnopqrstuv' ... key sk-or-v1-" + "b" * 40,
        )
    finally:
        bind_session("")
    raw = (tmp_path / "sess-secret.jsonl").read_text(encoding="utf-8")
    assert "ghp_" not in raw and "abcdefghijklmnop" not in raw and "bbbbbbbbbbbbbbbb" not in raw
    assert '"title": "bug"' in raw


def test_a_tool_that_ran_is_not_shown_as_skipped(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.trace.TRACES_DIR", tmp_path)
    bind_session("sess-skip")
    try:
        log_event("tool", name="read_file", arguments={"path": "a"}, result="x", skipped=False)
        log_event("tool", name="list_dir", arguments={"path": "."}, result="y", skipped=True)
    finally:
        bind_session("")
    md = render_markdown("sess-skip")
    assert "`read_file` (ran)" in md and "`list_dir` (skipped)" in md
