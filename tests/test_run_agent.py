from jinx_ai.providers.base import (
    AssistantMessage,
    BaseProvider,
    Message,
    ToolCall,
    extract_tool_calls_from_content,
)
from jinx_ai.tools import run_command
import jinx_ai.pending_restart as _pending_restart


def setup_function() -> None:
    _pending_restart.reset_runtime_state()


def teardown_function() -> None:
    _pending_restart.reset_runtime_state()


class _Scripted(BaseProvider):
    def __init__(self, name: str, replies: list[AssistantMessage]) -> None:
        self.display_name = name
        self._replies = list(replies)
        self.closed = 0
        self.seen: list[str] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AssistantMessage:
        self.seen.append(self.display_name)
        return self._replies.pop(0)

    def close_stream(self) -> None:
        self.closed += 1

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


_ECHO = [
    {
        "type": "function",
        "function": {"name": "echo", "parameters": {"type": "object", "properties": {}}},
    }
]


def test_run_agent_swaps_provider_between_rounds() -> None:
    first = _Scripted(
        "first",
        [AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={})])],
    )
    second = _Scripted(
        "second",
        [AssistantMessage(content="done from second")],
    )
    pending: list[BaseProvider | None] = [None, second]

    def swap() -> BaseProvider | None:
        return pending.pop(0) if pending else None

    executed: list[str] = []

    def run_tool(name: str, arguments: dict) -> str:
        executed.append(name)
        return "ok"

    result = first.run_agent(
        [{"role": "user", "content": "go"}],
        _ECHO,
        run_tool,
        swap_provider=swap,
    )
    assert result == "done from second"
    assert executed == ["echo"]
    assert first.seen == ["first"]
    assert second.seen == ["second"]
    assert first.closed >= 1


def test_run_agent_skips_identical_tool_and_stops_loop() -> None:
    busy = _Scripted(
        "loop",
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(name="echo", arguments={"path": r"D:\laragon\www\jinx-ai"})],
            ),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(name="echo", arguments={"path": r"D:/laragon/www/jinx-ai"})],
            ),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(name="echo", arguments={"path": r"D:\laragon\www\jinx-ai"})],
            ),
            AssistantMessage(content="should not reach"),
        ],
    )
    executed: list[str] = []

    def run_tool(name: str, arguments: dict) -> str:
        executed.append(name)
        return "listed"

    result = busy.run_agent(
        [{"role": "user", "content": "go"}],
        _ECHO,
        run_tool,
        max_rounds=8,
    )
    assert executed == ["echo"]
    assert "repeat" in result.lower()
    assert "continue" in result.lower()


def test_run_agent_still_runs_same_tool_with_new_args() -> None:
    scripted = _Scripted(
        "ok",
        [
            AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={"path": "a"})]),
            AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={"path": "b"})]),
            AssistantMessage(content="done"),
        ],
    )
    executed: list[str] = []

    def run_tool(name: str, arguments: dict) -> str:
        executed.append(str(arguments.get("path")))
        return "ok"

    result = scripted.run_agent(
        [{"role": "user", "content": "go"}],
        _ECHO,
        run_tool,
    )
    assert executed == ["a", "b"]
    assert result == "done"


def test_run_agent_says_continue_when_rounds_run_out() -> None:
    busy = _Scripted(
        "busy",
        [
            AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={})]),
            AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={})]),
        ],
    )
    result = busy.run_agent(
        [{"role": "user", "content": "go"}],
        _ECHO,
        lambda _n, _a: "ok",
        max_rounds=1,
    )
    assert "continue" in result.lower()
    assert "1 tool rounds" in result


def test_run_agent_unlimited_zero_keeps_going() -> None:
    replies = [
        AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={"n": str(i)})])
        for i in range(6)
    ]
    replies.append(AssistantMessage(content="all done"))
    scripted = _Scripted("long", replies)
    executed: list[str] = []

    def run_tool(name: str, arguments: dict) -> str:
        executed.append(str(arguments.get("n")))
        return "ok"

    result = scripted.run_agent(
        [{"role": "user", "content": "go"}],
        _ECHO,
        run_tool,
        max_rounds=0,
    )
    assert executed == [str(i) for i in range(6)]
    assert result == "all done"


def _schema(*names: str) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": name, "parameters": {"type": "object", "properties": {}}},
        }
        for name in names
    ]


_QWEN_DUMP = """
I'm sorry for the confusion earlier. Let's get started.
Step 1: Create a React Project
```json
{
  "name": "run_command",
  "arguments": {
    "command": "npx create-react-app jinx-ai-website"
  }
}
```
Step 2: Navigate to the Project Directory
{
  "name": "run_command",
  "arguments": {
    "command": "cd jinx-ai-website"
  }
}
Step 3: Replace the Default App Component
{
  "name": "replace_in_file",
  "arguments": {
    "path": "src/App.js",
    "old_string": "function App() {",
    "new_string": "function App() { return <h1>Jinx</h1>; }"
  }
}
```
Step 6: Open the Website
{
  "name": "open_url",
  "arguments": {
    "url": "http://localhost:3000"
  }
}
That's it!
Great idea! Let's get started again.
Step 1: Create a React Project
{
  "name": "run_command",
  "arguments": {
    "command": "npx create-react-app jinx-ai-website"
  }
}
"""


def test_extract_multiple_json_tool_objects() -> None:
    calls = extract_tool_calls_from_content(
        _QWEN_DUMP,
        _schema("run_command", "replace_in_file", "open_url"),
    )
    names = [call.name for call in calls]
    assert names == ["run_command", "run_command", "replace_in_file", "open_url"]
    assert calls[0].arguments["command"] == "npx create-react-app jinx-ai-website"
    assert calls[1].arguments["command"] == "cd jinx-ai-website"
    assert calls[2].arguments["path"] == "src/App.js"
    assert calls[3].arguments["url"] == "http://localhost:3000"


def test_extract_single_object_in_prose() -> None:
    calls = extract_tool_calls_from_content(
        'I will echo now\n{"name": "echo", "arguments": {}}\nthen stop.',
        _ECHO,
    )
    assert len(calls) == 1
    assert calls[0].name == "echo"


def test_extract_tool_calls_wrapper_and_xml() -> None:
    wrapped = '{"tool_calls": [{"name": "echo", "arguments": {"x": 1}}]}'
    calls = extract_tool_calls_from_content(wrapped, _ECHO)
    assert calls[0].name == "echo"
    assert calls[0].arguments == {"x": 1}
    xml = "<tool_call>echo\n<arg_key>x</arg_key><arg_value>2</arg_value></tool_call>"
    xml_calls = extract_tool_calls_from_content(xml, _ECHO)
    assert xml_calls[0].arguments == {"x": "2"}


def test_run_agent_executes_text_json_and_hides_dump() -> None:
    dump = 'Step 1\n```json\n{"name": "echo", "arguments": {}}\n```\n'
    scripted = _Scripted(
        "qwen",
        [AssistantMessage(content=dump), AssistantMessage(content="files are ready")],
    )
    executed: list[str] = []
    streamed: list[str] = []

    def run_tool(name: str, arguments: dict) -> str:
        executed.append(name)
        return "ok"

    result = scripted.run_agent(
        [{"role": "user", "content": "go"}],
        _ECHO,
        run_tool,
        on_text=streamed.append,
    )
    assert executed == ["echo"]
    assert result == "files are ready"
    assert "".join(streamed) == "files are ready"
    assert "run_command" not in "".join(streamed)
    assert "{" not in "".join(streamed)


def test_extract_tool_code_python_positional_and_win_path() -> None:
    text = (
        "```tool_code\n"
        'write_file("D:\\laragon\\www\\app\\routes\\web.php", "<?php\\n")\n'
        "```\n"
    )
    calls = extract_tool_calls_from_content(
        text, _schema("write_file", "replace_in_file")
    )
    assert len(calls) == 1
    assert calls[0].name == "write_file"
    assert "web.php" in calls[0].arguments["path"].replace("\\", "/")
    assert calls[0].arguments["content"].startswith("<?php")


def test_extract_gemma_bare_tool_code_cli() -> None:
    """Real Gemma dump from activity: bare tool_code + space-separated args."""
    text = (
        "I'll change the GUI title.\n"
        "tool_code\n"
        "read_file jinx_ai/ui_style.py\n"
    )
    calls = extract_tool_calls_from_content(text, _schema("read_file", "replace_in_file"))
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments["path"] == "jinx_ai/ui_style.py"

    repl = (
        "tool_code\n"
        "replace_in_file jinx_ai/widgets.py \"title='Jinx AI'\" "
        "\"title='Jinx AI - The Best AI Assistant'\"\n"
    )
    calls2 = extract_tool_calls_from_content(
        repl, _schema("read_file", "replace_in_file")
    )
    assert calls2[0].name == "replace_in_file"
    assert calls2[0].arguments["path"] == "jinx_ai/widgets.py"
    assert calls2[0].arguments["old_string"] == "title='Jinx AI'"
    assert "Best AI Assistant" in calls2[0].arguments["new_string"]


def test_extract_search_files_from_apology_prose() -> None:
    text = (
        "I will now use search_files to locate the string.\n"
        'search_files "Jinx AI - The Best AI Assistant" -jinx_ai\n'
    )
    calls = extract_tool_calls_from_content(text, _schema("search_files", "replace_in_file"))
    assert len(calls) >= 1
    assert calls[0].name == "search_files"
    assert "Best AI Assistant" in calls[0].arguments.get("query", "")
    assert "jinx_ai" in str(calls[0].arguments.get("path", ""))


def test_extract_bare_restart_aliases() -> None:
    tools = _schema("restart_jinx", "read_file")
    for sample in (
        "restart",
        "restart_jinx",
        "tool_code\nrestart",
        "Done.\nreboot\n",
        "`restart_jinx`",
        "restart()",
        "restart_jinx()",
    ):
        calls = extract_tool_calls_from_content(sample, tools)
        assert len(calls) == 1, sample
        assert calls[0].name == "restart_jinx"
        assert calls[0].arguments == {}
    assert extract_tool_calls_from_content("I will restart later.", tools) == []
    assert extract_tool_calls_from_content("read_file is useful", tools) == []


def test_extract_fenced_cli_run_command() -> None:
    text = (
        "```tool_code\n"
        "run_command npm create react-app jinx-ai-website --template typescript\n"
        "```\n"
    )
    calls = extract_tool_calls_from_content(text, _schema("run_command"))
    assert len(calls) == 1
    assert calls[0].arguments["command"].startswith("npm create react-app")


def test_run_agent_text_tools_use_user_assistant_not_role_tool() -> None:
    """Gemma-style printed tools must not inject role=tool (local Jinja 400)."""
    dump = (
        "Okay\n```tool_code\n"
        'echo()\n'
        "```\n"
    )
    scripted = _Scripted(
        "gemma",
        [AssistantMessage(content=dump), AssistantMessage(content="done")],
    )
    history: list[dict] = [{"role": "user", "content": "go"}]

    def run_tool(name: str, arguments: dict) -> str:
        return "tool-ok"

    # Capture messages passed into the second stream_chat via subclassing.
    seen_roles: list[list[str]] = []
    orig = scripted.stream_chat

    def wrapped(messages, tools=None, on_text=None, abort=None):
        seen_roles.append([str(m.get("role")) for m in messages])
        return orig(messages, tools, on_text, abort)

    scripted.stream_chat = wrapped  # type: ignore[method-assign]
    result = scripted.run_agent(history, _ECHO, run_tool)
    assert result == "done"
    assert len(seen_roles) >= 2
    second = seen_roles[1]
    assert "tool" not in second
    assert second.count("user") >= 1
    assert second.count("assistant") >= 1
    # Second-round user payload carries tool results.
    user_blobs = [
        str(m.get("content") or "")
        for m in history
        if m.get("role") == "user"
    ]
    # history is mutated in place by run_agent
    joined = "\n".join(str(m.get("content") or "") for m in history)
    assert "tool-ok" in joined or any("Tool results" in b for b in user_blobs)


def test_run_command_allows_scaffold_and_background_dev(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("jinx_ai.tools._allowed_roots", lambda: [tmp_path.resolve()])
    monkeypatch.setattr(
        "jinx_ai.terminal.run_foreground",
        lambda *a, **k: "exit 0\ncreated",
    )
    out = run_command("npx create-react-app jinx-ai-website")
    assert "Refused" not in out
    assert "created" in out.lower() or "exit 0" in out

    monkeypatch.setattr(
        "jinx_ai.terminal.run_background",
        lambda *a, **k: "Started in background pid=4242 terminal=abc",
    )
    bg = run_command("npm start")
    assert "background" in bg.lower()
    assert "4242" in bg
    node = run_command("node server.js")
    assert "background" in node.lower()
    assert "cwd" in run_command("cd jinx-ai-website").lower()
    assert "cwd" in run_command(r"cd /d D:\laragon\www\test-project").lower()


def test_visible_chat_hides_qwen_tool_dump() -> None:
    from jinx_ai.activity import visible_chat_reply

    shown = visible_chat_reply(_QWEN_DUMP, tools_used=True)
    assert "run_command" not in shown
    assert "create-react-app" not in shown
    assert "function App" not in shown
    assert "workspace" in shown.lower() or "Done" in shown


def test_visible_chat_keeps_a_short_summary() -> None:
    from jinx_ai.activity import visible_chat_reply

    text = "I wrote index.html with a hero and a capabilities list."
    assert visible_chat_reply(text, tools_used=True) == text
    assert visible_chat_reply("Hi there.", tools_used=False) == "Hi there."


def test_visible_chat_keeps_a_long_chief_report() -> None:
    from jinx_ai.activity import visible_chat_reply

    report = (
        "I assigned Heimerdinger the React layout, Jinx the visual system, "
        "and Ekko the implementation. "
        "Heimerdinger proposed src/components with Hero, Services, Team, and Contact. "
        "Jinx set a dark cyan/pink palette. Ekko will write the files next.\n\n"
    ) * 20
    shown = visible_chat_reply(report, tools_used=True)
    assert "Heimerdinger" in shown
    assert "Ekko" in shown
    assert "Done — I used the tools" not in shown

