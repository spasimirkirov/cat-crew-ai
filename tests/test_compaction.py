from jinx_ai.compaction import (
    compact_messages,
    dead_ends,
    estimate_chars,
    files_touched,
)
from jinx_ai.providers.base import AssistantMessage, BaseProvider, Message, ToolCall


class _FakeProvider(BaseProvider):
    display_name = "fake"

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> AssistantMessage:
        return AssistantMessage(
            content=(
                "## Goal\nShip the tree.\n"
                "## Constraints\nKeep Python only.\n"
                "## Files touched\n- jinx_ai/session_tree.py\n"
                "## Decisions\nBranch, do not delete.\n"
                "## Errors / dead ends\n- missed a parent id\n"
                "## Status\nIn progress.\n"
                "## Next steps\n- add tests"
            )
        )

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "content": result}


def test_files_touched_from_tool_calls_and_text() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "jinx_ai/chat_window.py"}',
                    }
                }
            ],
        },
        {
            "role": "tool",
            "content": 'Error: missing "path": "C:\\\\tmp\\\\dead.py"',
        },
    ]
    found = files_touched(messages)
    assert "jinx_ai/chat_window.py" in found
    assert any(item.endswith("dead.py") for item in found)


def test_dead_ends_collects_tool_errors() -> None:
    notes = dead_ends(
        [
            {"role": "tool", "content": "Error: file not found\nstack"},
            {"role": "assistant", "content": "error: still broken"},
            {"role": "user", "content": "please continue"},
        ]
    )
    assert notes[0].startswith("Error: file not found")
    assert any("still broken" in item.lower() for item in notes)


def test_compact_messages_inserts_structured_summary(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.compaction.COMPACT_CHARS", 80)
    monkeypatch.setattr("jinx_ai.compaction.COMPACT_KEEP", 2)
    messages: list[Message] = [{"role": "system", "content": "sys"}]
    for i in range(6):
        messages.append({"role": "user", "content": f"task {i} " + ("x" * 80)})
        messages.append({"role": "assistant", "content": f"ok {i} " + ("y" * 80)})
    assert estimate_chars(messages) > 80
    out = compact_messages(_FakeProvider(), messages)
    assert out is not None
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    blob = out[1]["content"]
    assert "## Goal" in blob
    assert "## Next steps" in blob
    assert "Files touched" in blob
    assert out[-2]["role"] == "user"
    assert out[-1]["role"] == "assistant"
    assert len(out) < len(messages)
    assert estimate_chars(out) < estimate_chars(messages)


def test_compact_skips_short_chats() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    assert compact_messages(_FakeProvider(), messages) is None
