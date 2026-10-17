from backend.services.compaction import ContextBudget, compact_messages, dead_ends, estimate_chars, estimate_tokens, files_touched
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall


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
                "## Files touched\n- backend/services/session_tree.py\n"
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
                        "arguments": '{"path": "backend/chat_window.py"}',
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
    assert "backend/chat_window.py" in found
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
    monkeypatch.setattr("backend.services.compaction.COMPACT_KEEP", 2)
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


def _long_chat(turns: int = 8, filler: int = 400) -> list[Message]:
    messages: list[Message] = [{"role": "system", "content": "sys"}]
    for i in range(turns):
        messages.append({"role": "user", "content": f"task {i} " + ("x" * filler)})
        messages.append({"role": "assistant", "content": f"ok {i} " + ("y" * filler)})
    return messages


class _Counting(_FakeProvider):
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        return super().chat(messages, tools)


def test_estimate_tokens_is_chars_over_four() -> None:
    assert estimate_tokens([{"role": "user", "content": "x" * 400}]) == 100
    assert estimate_tokens([]) == 0


def test_budget_leaves_a_small_context_alone() -> None:
    provider = _Counting()
    messages = _long_chat(turns=2, filler=20)
    before = list(messages)
    assert ContextBudget(limit=8000, summarizer=lambda: (provider, "cheap")).apply(messages) is False
    assert messages == before and provider.calls == 0


def test_budget_compresses_in_place_past_the_limit_and_reports(monkeypatch) -> None:
    monkeypatch.setattr("backend.services.compaction.COMPACT_KEEP", 4)
    provider = _Counting()
    seen = []
    messages = _long_chat()
    ident = id(messages)
    budget = ContextBudget(limit=500, on_compressed=lambda b, a, m: seen.append((b, a, m)), summarizer=lambda: (provider, "Gemini · flash"))
    assert estimate_tokens(messages) > 500
    assert budget.apply(messages) is True
    assert id(messages) == ident  # the caller's own list was shortened
    assert provider.calls == 1
    assert messages[0]["role"] == "system" and "compacted earlier conversation" in messages[1]["content"]
    assert messages[-1]["content"].startswith("ok 7")  # the most recent turns are kept verbatim
    (before, after, model), = seen
    assert before > after and model == "Gemini · flash"


def test_a_summary_never_splits_a_tool_call_from_its_result(monkeypatch) -> None:
    monkeypatch.setattr("backend.services.compaction.COMPACT_KEEP", 4)
    messages: list[Message] = [{"role": "system", "content": "sys"}]
    for i in range(3):
        messages.append({"role": "user", "content": f"task {i} " + "x" * 300})
        messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "result " + "y" * 300})
        messages.append({"role": "assistant", "content": f"done {i}"})
    out = compact_messages(_FakeProvider(), messages)
    assert out is not None
    recent = out[3:]  # after system, summary, acknowledgement
    assert recent[0]["role"] != "tool"
    for index, message in enumerate(recent):
        if message["role"] == "tool":
            assert recent[index - 1].get("tool_calls"), "a tool result must follow the assistant turn that asked for it"


def test_budget_does_not_retry_until_the_context_grows(monkeypatch) -> None:
    monkeypatch.setattr("backend.services.compaction.COMPACT_KEEP", 4)

    class _Failing(_Counting):
        def chat(self, messages, tools=None):
            self.calls += 1
            raise RuntimeError("memory model is down")

    provider = _Failing()
    messages = _long_chat()
    snapshot = list(messages)
    budget = ContextBudget(limit=500, summarizer=lambda: (provider, "x"))
    assert budget.apply(messages) is False and messages == snapshot  # failure: nothing lost
    assert budget.apply(messages) is False
    assert provider.calls == 1  # not retried every round
    messages.extend(_long_chat(turns=4)[1:])  # the chat grew by more than a quarter
    budget.apply(messages)
    assert provider.calls == 2


def test_no_summarizer_means_no_change() -> None:
    messages = _long_chat()
    snapshot = list(messages)
    assert ContextBudget(limit=500, summarizer=lambda: None).apply(messages) is False
    assert messages == snapshot


def test_run_agent_shortens_the_context_before_calling_the_model(monkeypatch) -> None:
    """The hook lives in run_agent, so every runner (chat, room bots, subagents) gets it."""
    monkeypatch.setattr("backend.services.compaction.COMPACT_KEEP", 4)
    monkeypatch.setattr("backend.config.CONTEXT_TOKEN_LIMIT", 500)
    summarizer = _Counting()
    monkeypatch.setattr("backend.services.compaction.summarizer_provider", lambda: (summarizer, "Memory model"))
    sent: list[int] = []

    class _Model(_FakeProvider):
        def stream_chat(self, messages, tools=None, on_text=None, abort=None):
            sent.append(estimate_tokens(messages))
            return AssistantMessage(content="final answer")

    reported = []
    reply = _Model().run_agent(_long_chat(), [], lambda n, a: "", on_compressed=lambda b, a, m: reported.append(m))
    assert reply == "final answer"
    assert summarizer.calls == 1 and reported == ["Memory model"]
    assert sent[0] < 500 * 1.5  # what the model saw is the summary plus the recent turns


def test_keep_tokens_keeps_the_newest_messages_that_fit() -> None:
    """Token-based keep: one huge old tool result is summarised, the small recent turns stay."""
    small = "x" * 400  # ~100 tokens each
    messages: list[Message] = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "the task"},
        {"role": "assistant", "content": "a" * 40_000},  # ~10k tokens, old
    ]
    for i in range(12):
        messages.append({"role": "user" if i % 2 else "assistant", "content": f"{i} {small}"})
    out = compact_messages(_FakeProvider(), messages, keep_tokens=800)
    assert out is not None
    recent = out[3:]
    # ~800 tokens of ~100-token messages: about 8 kept, never the 10k one.
    assert 6 <= len(recent) <= 9
    assert recent[-1]["content"].startswith("11 ")
    assert all(len(m["content"]) < 1_000 for m in recent)


def test_keep_tokens_always_keeps_a_few_messages_and_the_tool_pair() -> None:
    messages: list[Message] = [{"role": "user", "content": "task"}]
    for i in range(6):
        messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": "{}"}}]})
        messages.append({"role": "tool", "content": "y" * 20_000})
    out = compact_messages(_FakeProvider(), messages, keep_tokens=10)
    assert out is not None
    recent = out[2:]
    assert len(recent) >= 4
    assert recent[0]["role"] == "assistant"  # a tool result never starts the recent part


def test_budget_keep_tokens_is_capped_at_half_the_threshold(monkeypatch) -> None:
    monkeypatch.setattr("backend.services.compaction.COMPACT_KEEP_TOKENS", 20_000)
    assert ContextBudget.keep_tokens(170_000) == 20_000
    assert ContextBudget.keep_tokens(14_000) == 7_000
    monkeypatch.setattr("backend.services.compaction.COMPACT_KEEP_TOKENS", 0)
    assert ContextBudget.keep_tokens(170_000) == 0
