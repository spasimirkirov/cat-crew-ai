"""harness/tool_meta.py: every shipped tool is classified, and the guards read that one table."""

from __future__ import annotations

import importlib
import inspect

from backend.harness import tool_meta
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall

_ROLE_MODULES = ("aegis_tools", "forge_tools", "lead_tools", "pulse_tools", "qa_tools")


def _shipped_tool_names() -> set[str]:
    from backend.harness.deferred import deferred_tool_schemas
    from backend.platform.chief import chief_tool_schema
    from backend.tools import TOOLS_SCHEMA

    names = {t["function"]["name"] for t in TOOLS_SCHEMA}
    names |= {t["function"]["name"] for t in chief_tool_schema()}
    names |= {t["function"]["name"] for t in deferred_tool_schemas()}
    for module_name in _ROLE_MODULES:
        module = importlib.import_module(f"backend.tool_functions.{module_name}")
        names |= {
            name
            for name, fn in inspect.getmembers(module, inspect.isfunction)
            if fn.__module__ == module.__name__ and not name.startswith("_")
        }
    return names


def test_every_shipped_tool_is_classified(isolated) -> None:
    """Add a tool, classify it in harness/tool_meta.py: approval, loop guard and untrusted marking
    all read that entry. An unclassified tool would be gated as one that acts."""
    missing = sorted(name for name in _shipped_tool_names() if not tool_meta.is_known(name))
    assert not missing, f"classify these in backend/harness/tool_meta.py: {missing}"


def test_an_unknown_tool_is_gated_and_changes_the_world(isolated) -> None:
    from backend.harness.approvals import needs_approval, set_approval_required
    from backend.harness.loop_guard import changes_world

    set_approval_required(True)
    assert needs_approval("marketplace_wipe_everything")
    assert changes_world("marketplace_wipe_everything")
    assert not needs_approval("read_file") and not needs_approval("browser_click")
    assert needs_approval("execute_terminal_cmd") and needs_approval("write_file_patch")


class _Scripted(BaseProvider):
    display_name = "scripted"

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        return self._replies.pop(0)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": result}


def _tail(n: int) -> AssistantMessage:
    return AssistantMessage(content="", tool_calls=[ToolCall("read_terminal", {"session_id": "dev"}, id=f"t{n}")])


def test_a_dev_server_terminal_can_be_checked_again_but_not_forever() -> None:
    """read_terminal reads logs that move on their own: asking twice is waiting, not looping."""
    tools = [{"type": "function", "function": {"name": "read_terminal", "parameters": {"type": "object"}}}]
    ran: list[int] = []
    provider = _Scripted([_tail(i) for i in range(6)] + [AssistantMessage(content="server is up")])
    provider.run_agent([{"role": "user", "content": "start it"}], tools, lambda n, a: ran.append(1) or f"log {len(ran)}")
    assert len(ran) == 3  # three looks while nothing else changed, then the repeat guard steps in


_PARALLEL_TOOLS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object"}}}
    for n in ("read_file", "web_fetch", "write_file", "browser_snapshot")
]


def test_independent_reads_run_together_and_keep_their_order() -> None:
    import threading
    import time

    from backend.tools import reset_tool_allowlist, set_tool_allowlist

    started: list[str] = []
    seen_allow: list[object] = []
    lock = threading.Lock()

    def execute(name: str, args: dict) -> str:
        from backend.tools import _tool_allow

        with lock:
            started.append(args["path"])
            seen_allow.append(_tool_allow.get())
        time.sleep(0.3)
        return f"body of {args['path']}"

    reads = [ToolCall("read_file", {"path": p}, id=f"r{i}") for i, p in enumerate(("a", "b", "c"))]
    provider = _Scripted([AssistantMessage(content="", tool_calls=reads), AssistantMessage(content="read")])
    messages: list[Message] = [{"role": "user", "content": "read three files"}]
    token = set_tool_allowlist(frozenset({"read_file"}))
    try:
        began = time.monotonic()
        provider.run_agent(messages, _PARALLEL_TOOLS, execute)
        elapsed = time.monotonic() - began
    finally:
        reset_tool_allowlist(token)
    assert elapsed < 0.75, elapsed  # three 0.3 s reads at once, not one after another
    tool_results = [m["content"] for m in messages if m.get("role") == "tool"]
    assert tool_results == ["body of a", "body of b", "body of c"]
    assert all(allow == frozenset({"read_file"}) for allow in seen_allow)  # the turn's context followed


def test_a_read_after_a_write_waits_for_the_write() -> None:
    order: list[str] = []

    def execute(name: str, args: dict) -> str:
        order.append(f"{name}:{args['path']}")
        return "ok"

    calls = [
        ToolCall("read_file", {"path": "a"}, id="r1"),
        ToolCall("write_file", {"path": "a"}, id="w1"),
        ToolCall("read_file", {"path": "a2"}, id="r2"),
        ToolCall("browser_snapshot", {"path": "page"}, id="b1"),
    ]
    provider = _Scripted([AssistantMessage(content="", tool_calls=calls), AssistantMessage(content="done")])
    provider.run_agent([{"role": "user", "content": "go"}], _PARALLEL_TOOLS, execute)
    assert order == ["read_file:a", "write_file:a", "read_file:a2", "browser_snapshot:page"]
