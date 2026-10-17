"""One reading of "did this tool call fail", from each tool's own result format."""

from __future__ import annotations

from backend.harness.tool_result import exit_code, tool_failed
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall


def test_a_non_zero_exit_is_a_failure_and_zero_is_not() -> None:
    assert tool_failed("run_command", "exit 1 @ D:\\app\nnpm ERR! missing script: build")
    assert not tool_failed("run_command", "exit 0 @ D:\\app\nbuilt in 2.1s")
    assert exit_code("exit 127 @ /x\n") == 127


def test_timeouts_gates_and_verdicts_are_failures() -> None:
    for name, text in (
        ("run_command", "Command timed out after 120s: npm run build\n…"),
        ("write_file", "Denied by user approval gate: write_file"),
        ("run_command", "Jev gatekeeper blocked `run_command` before it ran: …"),
        ("run_command", "Refused: this command deletes a drive, home or the whole folder, so …"),
        ("run_smoke_test", "FAIL: `npm run dev` exited on its own within 8s (exit code 1)"),
        ("run_test_suite", "`npm test` -> FAIL (3 failed, 10 passed)\n\n--- output ---\nexit 1 @ x\n"),
        ("read_file", "Error: file does not exist: a.txt"),
    ):
        assert tool_failed(name, text), (name, text)


def test_ordinary_output_that_mentions_errors_is_not_a_failure() -> None:
    assert not tool_failed("read_file", "errors.ts\n  1 | export const errors = []")
    assert not tool_failed("run_smoke_test", "PASS (smoke test only): `npm run dev` was still running after 8s")
    assert not tool_failed("run_test_suite", "`pytest` -> PASS (12 passed)\n\n--- output ---\nexit 0 @ x\n")


class _Scripted(BaseProvider):
    display_name = "scripted"

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        return self._replies.pop(0)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": result}


def test_a_failed_build_keeps_restart_from_running_in_the_same_batch() -> None:
    tools = [
        {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
        for n in ("run_command", "restart_catcrew")
    ]
    ran: list[str] = []
    provider = _Scripted(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall("restart_catcrew", {"reason": "done"}, id="r1"),
                    ToolCall("run_command", {"command": "npm run build"}, id="b1"),
                ],
            ),
            AssistantMessage(content="the build is broken"),
        ]
    )

    def execute(name: str, args: dict) -> str:
        ran.append(name)
        return "exit 2 @ D:\\app\nerror TS2304" if name == "run_command" else "restarting"

    provider.run_agent([{"role": "user", "content": "ship it"}], tools, execute)
    assert ran == ["run_command"]  # exit 2 is a failure, so the restart was skipped
