from backend.providers.base import (
    AssistantMessage,
    BaseProvider,
    Message,
    ToolCall,
    extract_tool_calls_from_content,
)
from backend.tools import run_command
import backend.services.pending_restart as _pending_restart


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
        self.tools_seen: list[list[dict] | None] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AssistantMessage:
        self.seen.append(self.display_name)
        self.tools_seen.append(tools)
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
                tool_calls=[ToolCall(name="echo", arguments={"path": r"D:\laragon\www\cat-crew-ai"})],
            ),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(name="echo", arguments={"path": r"D:/laragon/www/cat-crew-ai"})],
            ),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(name="echo", arguments={"path": r"D:\laragon\www\cat-crew-ai"})],
            ),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(name="echo", arguments={"path": r"D:/laragon/www/cat-crew-ai"})],
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


def test_hit_round_cap_recognises_a_capped_run() -> None:
    from backend.providers.base import hit_round_cap

    busy = _Scripted(
        "busy",
        [
            AssistantMessage(content="working on it", tool_calls=[ToolCall(name="echo", arguments={})]),
            AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={})]),
        ],
    )
    result = busy.run_agent(
        [{"role": "user", "content": "go"}],
        _ECHO,
        lambda _n, _a: "ok",
        max_rounds=1,
    )
    assert hit_round_cap(result)
    assert not hit_round_cap("all done")


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
    "command": "npx create-react-app cat-crew-ai-website"
  }
}
```
Step 2: Navigate to the Project Directory
{
  "name": "run_command",
  "arguments": {
    "command": "cd cat-crew-ai-website"
  }
}
Step 3: Replace the Default App Component
{
  "name": "replace_in_file",
  "arguments": {
    "path": "src/App.js",
    "old_string": "function App() {",
    "new_string": "function App() { return <h1>CatCrewAI</h1>; }"
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
    "command": "npx create-react-app cat-crew-ai-website"
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
    assert calls[0].arguments["command"] == "npx create-react-app cat-crew-ai-website"
    assert calls[1].arguments["command"] == "cd cat-crew-ai-website"
    assert calls[2].arguments["path"] == "src/App.js"
    assert calls[3].arguments["url"] == "http://localhost:3000"


def test_extract_single_object_in_prose() -> None:
    calls = extract_tool_calls_from_content(
        'I will echo now\n{"name": "echo", "arguments": {}}\nthen stop.',
        _ECHO,
    )
    assert len(calls) == 1
    assert calls[0].name == "echo"


def test_extract_gemma_delegate_json_without_tool_name() -> None:
    """Chief prints {task, bot_id} fences instead of native tool_calls."""
    text = """
Thank you, Vanguard. I'll delegate the design work to Pixel.

**Delegation to Pixel:**

```json
{
  "task": "Implement the visual design for the Team Arcane landing page.",
  "bot_id": "pixel",
  "context": "Vanguard's architecture notes."
}
```

**Delegation to Pulse:**

```json
{
  "task": "Build the React landing page.",
  "bot_id": "pulse"
}
```
"""
    tools = _schema("delegate_to_bot", "delegate_to_team", "update_todo")
    calls = extract_tool_calls_from_content(text, tools)
    assert [call.name for call in calls] == ["delegate_to_bot", "delegate_to_bot"]
    assert calls[0].arguments["bot_id"] == "pixel"
    assert "visual design" in calls[0].arguments["task"]
    assert calls[0].arguments["context"] == "Vanguard's architecture notes."
    assert calls[1].arguments["bot_id"] == "pulse"
    assert "React" in calls[1].arguments["task"]


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
    # The full path must round-trip byte-for-byte, not just contain the
    # filename — see test_extract_tool_code_does_not_flatten_windows_path
    # for the regression this used to miss.
    assert calls[0].arguments["path"] == "D:\\laragon\\www\\app\\routes\\web.php"
    assert calls[0].arguments["content"].startswith("<?php")


def test_extract_tool_code_does_not_flatten_windows_path() -> None:
    """Regression: a real report showed write_file("D:\\laragon\\www\\
    team-arcane\\src\\components\\Contact.js", ...) landing on disk as a
    single flattened filename with no directory separators at all
    (laragonwwwteam-arcanesrccomponentsContact.js). Root cause: \\t, \\r,
    \\n, \\b, \\f are all *valid* Python escapes, so a naive
    ast.literal_eval on a Windows path silently swallows the leading
    letter of any segment starting with one of those letters (team-*,
    routes*, node_modules*, backend*, frontend*) into a control character,
    and every other \\<letter> just emits a DeprecationWarning while still
    eating the backslash. Every segment below hits a different one of
    those letters (\\t in team-arcane, \\s in src, \\c in components) —
    this is the exact shape that broke."""
    text = (
        "```tool_code\n"
        'write_file("D:\\laragon\\www\\team-arcane\\src\\components\\Contact.js", '
        '"export default function Contact() { return null; }")\n'
        "```\n"
    )
    calls = extract_tool_calls_from_content(text, _schema("write_file", "replace_in_file"))
    assert len(calls) == 1
    path = calls[0].arguments["path"]
    assert path == "D:\\laragon\\www\\team-arcane\\src\\components\\Contact.js"
    # No control characters snuck in anywhere (the literal bug symptom).
    assert not any(ch in path for ch in "\t\r\n\b\f")
    # And every intended directory boundary survived as an actual separator.
    normalized = path.replace("\\", "/")
    assert normalized == "D:/laragon/www/team-arcane/src/components/Contact.js"


def test_extract_tool_code_win_path_in_cwd_kwarg() -> None:
    """cwd (run_command) needs the same treatment as path — both are real
    filesystem locations, not file content."""
    text = (
        "```tool_code\n"
        'run_command(command="npm run build", cwd="D:\\laragon\\www\\team-arcane")\n'
        "```\n"
    )
    calls = extract_tool_calls_from_content(text, _schema("run_command"))
    assert len(calls) == 1
    assert calls[0].arguments["cwd"] == "D:\\laragon\\www\\team-arcane"


def test_extract_gemma_bare_tool_code_cli() -> None:
    """Real Gemma dump from activity: bare tool_code + space-separated args."""
    text = (
        "I'll change the GUI title.\n"
        "tool_code\n"
        "read_file backend/ui_style.py\n"
    )
    calls = extract_tool_calls_from_content(text, _schema("read_file", "replace_in_file"))
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments["path"] == "backend/ui_style.py"

    repl = (
        "tool_code\n"
        "replace_in_file backend/widgets.py \"title='CatCrewAI'\" "
        "\"title='CatCrewAI - The Best AI Assistant'\"\n"
    )
    calls2 = extract_tool_calls_from_content(
        repl, _schema("read_file", "replace_in_file")
    )
    assert calls2[0].name == "replace_in_file"
    assert calls2[0].arguments["path"] == "backend/widgets.py"
    assert calls2[0].arguments["old_string"] == "title='CatCrewAI'"
    assert "Best AI Assistant" in calls2[0].arguments["new_string"]


def test_extract_fenced_cli_run_command() -> None:
    text = (
        "```tool_code\n"
        "run_command npm create react-app cat-crew-ai-website --template typescript\n"
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
    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [tmp_path.resolve()])
    monkeypatch.setattr(
        "backend.services.terminal.run_foreground",
        lambda *a, **k: "exit 0\ncreated",
    )
    out = run_command("npx create-react-app cat-crew-ai-website")
    assert "Refused" not in out
    assert "created" in out.lower() or "exit 0" in out

    monkeypatch.setattr(
        "backend.services.terminal.run_background",
        lambda *a, **k: "Started in background pid=4242 terminal=abc",
    )
    bg = run_command("npm start")
    assert "background" in bg.lower()
    assert "4242" in bg
    node = run_command("node server.js")
    assert "background" in node.lower()
    assert "cwd" in run_command("cd cat-crew-ai-website").lower()
    assert "cwd" in run_command(r"cd /d D:\laragon\www\test-project").lower()


def test_visible_chat_hides_qwen_tool_dump() -> None:
    from backend.activity import visible_chat_reply

    shown = visible_chat_reply(_QWEN_DUMP, tools_used=True)
    assert "run_command" not in shown
    assert "create-react-app" not in shown
    assert "function App" not in shown
    assert "workspace" in shown.lower() or "Done" in shown


def test_visible_chat_keeps_a_short_summary() -> None:
    from backend.activity import visible_chat_reply

    text = "I wrote index.html with a hero and a capabilities list."
    assert visible_chat_reply(text, tools_used=True) == text
    assert visible_chat_reply("Hi there.", tools_used=False) == "Hi there."


def test_visible_chat_keeps_a_long_chief_report() -> None:
    from backend.activity import visible_chat_reply

    report = (
        "I assigned Heimerdinger the React layout, CatCrewAI the visual system, "
        "and Ekko the implementation. "
        "Heimerdinger proposed src/components with Hero, Services, Team, and Contact. "
        "CatCrewAI set a dark cyan/pink palette. Ekko will write the files next.\n\n"
    ) * 20
    shown = visible_chat_reply(report, tools_used=True)
    assert "Heimerdinger" in shown
    assert "Ekko" in shown


# -- Jev ("System One") precheck bypass: run_agent calls the model without tool schemas ------------


def _enable_jev(monkeypatch, decision):
    from backend.middleware import jev_gatekeeper as jg

    class _Fake:
        def decide(self, state, questions):
            return [decision]

    monkeypatch.setattr(
        jg,
        "_settings",
        lambda: {"enabled": True, "model": "jev-latest", "confidence_threshold": 0.92, "api_key": "k", "skip_tools_precheck": True},
    )
    monkeypatch.setattr(jg, "_client", lambda: _Fake())


def test_a_confident_bypass_calls_the_model_with_no_tools(monkeypatch) -> None:
    from backend.services.jev_client import Decision

    _enable_jev(monkeypatch, Decision(id="no_tools_needed", type="noul", value=True, confidence=0.99, ok=True, latency_ms=4.0))
    scripted = _Scripted("lean", [AssistantMessage(content="2 + 2 is 4.")])

    def run_tool(name: str, arguments: dict) -> str:
        raise AssertionError("no tool should run on a bypassed turn")

    result = scripted.run_agent([{"role": "user", "content": "what is 2+2?"}], _ECHO, run_tool)
    assert result == "2 + 2 is 4."
    assert len(scripted.seen) == 1
    assert scripted.tools_seen == [[]]  # the (potentially large) tool schemas were not attached


def test_a_bypass_that_yields_nothing_falls_through_to_the_normal_loop(monkeypatch) -> None:
    from backend.services.jev_client import Decision

    _enable_jev(monkeypatch, Decision(id="no_tools_needed", type="noul", value=True, confidence=0.99, ok=True, latency_ms=4.0))
    scripted = _Scripted(
        "recovers",
        [
            AssistantMessage(content=""),  # the lean, tool-less call: Jev misjudged, nothing useful came back
            AssistantMessage(content="", tool_calls=[ToolCall(name="echo", arguments={})]),
            AssistantMessage(content="done after all"),
        ],
    )
    executed: list[str] = []

    def run_tool(name: str, arguments: dict) -> str:
        executed.append(name)
        return "ok"

    result = scripted.run_agent([{"role": "user", "content": "actually needs a tool"}], _ECHO, run_tool)
    assert result == "done after all"
    assert executed == ["echo"]
    assert scripted.tools_seen[0] == []  # the bypass attempt
    assert scripted.tools_seen[1] == _ECHO  # the normal loop, tools restored


def test_jev_disabled_never_touches_the_loop(monkeypatch) -> None:
    from backend.middleware import jev_gatekeeper as jg

    monkeypatch.setattr(jg, "_settings", lambda: {"enabled": False, "model": "jev-latest", "confidence_threshold": 0.92, "api_key": ""})
    scripted = _Scripted("normal", [AssistantMessage(content="hi")])
    result = scripted.run_agent([{"role": "user", "content": "hello"}], _ECHO, lambda n, a: "ok")
    assert result == "hi"
    assert scripted.tools_seen == [_ECHO]

