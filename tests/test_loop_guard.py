"""The tool-loop guards must stop real loops without stopping real progress."""

from backend.harness.loop_guard import WorldClock, changes_world
from backend.platform.sop import _append_prior, _qa_verdict_failed
from backend.platform.toolset import builtin_names_for_role
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall
from backend.services.compaction import (
    ASSIGNMENT_HEAD,
    COMPACTED_MARK,
    ContextBudget,
    compact_messages,
)
import backend.services.pending_restart as _pending_restart


def setup_function() -> None:
    _pending_restart.reset_runtime_state()


def teardown_function() -> None:
    _pending_restart.reset_runtime_state()


class _Scripted(BaseProvider):
    display_name = "scripted"

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        return self._replies.pop(0)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


def _call(name: str, **arguments) -> AssistantMessage:
    return AssistantMessage(content="", tool_calls=[ToolCall(name=name, arguments=arguments)])


_TOOLS = [
    {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
    for n in ("run_command", "write_file")
]


def test_world_clock_ticks_only_for_changing_tools() -> None:
    clock = WorldClock()
    clock.note_executed("read_file")
    clock.note_executed("update_todo")
    assert clock.version == 0
    clock.note_executed("write_file")
    assert clock.version == 1
    assert changes_world("run_command") and changes_world("some_unknown_role_tool")


def test_same_call_is_allowed_again_after_a_file_changed() -> None:
    builds: list[int] = []

    def execute(name: str, args: dict) -> str:
        if name == "run_command":
            builds.append(1)
            return "Error: build failed" if len(builds) == 1 else "build ok"
        return "written"

    provider = _Scripted(
        [
            _call("run_command", command="npm run build"),
            _call("write_file", path="src/App.jsx", content="fixed"),
            _call("run_command", command="npm run build"),
            AssistantMessage(content="PASS build ok"),
        ]
    )
    out = provider.run_agent([{"role": "user", "content": "go"}], _TOOLS, execute)
    assert out == "PASS build ok"
    assert len(builds) == 2  # the rebuild after the fix really ran


def test_unchanged_repeat_is_skipped_and_ends_with_a_useful_message() -> None:
    ran: list[int] = []

    def execute(name: str, args: dict) -> str:
        ran.append(1)
        return "Error: build failed: missing dependency"

    same = _call("run_command", command="npm run build")
    provider = _Scripted([same, same, same, same, AssistantMessage(content="unreachable")])
    out = provider.run_agent([{"role": "user", "content": "go"}], _TOOLS, execute)
    assert len(ran) == 1  # nothing changed, so it was never re-run
    assert out.startswith("I got stuck")
    assert "run_command" in out and "missing dependency" in out


def test_stuck_turn_does_not_read_as_a_qa_pass() -> None:
    assert _qa_verdict_failed("I got stuck repeating the same failed tool (browser_check: x).")
    assert _qa_verdict_failed("FAIL build broke")
    assert not _qa_verdict_failed("PASS all good")


def test_designer_can_build_and_track_its_work() -> None:
    names = builtin_names_for_role("designer")
    for needed in ("run_command", "update_todo", "search_files", "create_directory"):
        assert needed in names


def test_handoff_is_bounded_and_drops_the_oldest_first() -> None:
    prior = ""
    for i in range(12):
        prior = _append_prior(prior, f"phase {i} " + "x" * 3000)
    assert len(prior) <= 4_600
    assert "phase 11" in prior and "phase 0 " not in prior
    assert "Computer after this turn" not in _append_prior("", "done\n\nComputer after this turn: D:\\a\n  src/")


def _long_run(n: int = 20) -> list[Message]:
    msgs: list[Message] = [
        {"role": "system", "content": "rules " * 50},
        {"role": "user", "content": "ASSIGNMENT: restyle the hero, task_id T-42"},
    ]
    for i in range(n):
        msgs.append({"role": "assistant", "content": f"step {i}"})
        msgs.append({"role": "user", "content": f"go on {i}"})
    return msgs


class _Summarizer(BaseProvider):
    display_name = "sum"

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        return AssistantMessage(content="## Goal\nrestyle\n## Status\nhalf done")

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "content": result}


def test_compaction_keeps_the_assignment_verbatim_even_after_two_rounds() -> None:
    first = compact_messages(_Summarizer(), _long_run())
    assert first is not None
    text = first[1]["content"]
    assert text.startswith(ASSIGNMENT_HEAD) and "task_id T-42" in text and COMPACTED_MARK in text
    grown = first + [
        m for i in range(20) for m in (
            {"role": "assistant", "content": f"more {i}"},
            {"role": "user", "content": f"again {i}"},
        )
    ]
    second = compact_messages(_Summarizer(), grown)
    assert second is not None
    text2 = second[1]["content"]
    assert text2.count(ASSIGNMENT_HEAD) == 1 and text2.count("task_id T-42") == 1
    assert text2.count(COMPACTED_MARK) == 1


def test_budget_does_not_count_the_system_prompt() -> None:
    big_system = {"role": "system", "content": "s" * 40_000}  # ~10k tokens
    msgs = [big_system, {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert ContextBudget(limit=2_000, summarizer=lambda: None).apply(msgs) is False


def test_qa_verdict_is_read_through_the_engine_wrappers() -> None:
    """The runner wraps a QA reply in a banner, a "[Name / role]" header and a directory
    listing; the FAIL used to hide behind that header and no rejection reached the developer."""
    wrapped = "=== QA VERDICT — Aegis ===\n[Aegis / qa]\n{}\n\nComputer after this turn: /x\n=== END QA VERDICT ==="
    assert _qa_verdict_failed(wrapped.format("FAIL header still overlaps"))
    assert _qa_verdict_failed(wrapped.format("I got stuck repeating the same failed tool (x)."))
    assert _qa_verdict_failed(wrapped.format("**Verdict: FAIL** two criteria"))
    assert not _qa_verdict_failed(wrapped.format("PASS both criteria hold. Nothing to FAIL on."))


# ---- a model that says nothing must not end the turn silently --------------------------------------


def _run(provider: _Scripted, messages: list | None = None) -> str:
    return provider.run_agent(messages if messages is not None else [{"role": "user", "content": "hi"}], _TOOLS, lambda name, args: "ok", max_rounds=6)


def test_an_empty_reply_is_asked_again_once_and_the_answer_is_used() -> None:
    provider = _Scripted([AssistantMessage(content=""), AssistantMessage(content="Here is the answer.")])
    messages = [{"role": "user", "content": "why is the nav so big?"}]
    assert _run(provider, messages) == "Here is the answer."
    assert "reply was empty" in str(messages[-1]["content"]) or any("reply was empty" in str(m.get("content")) for m in messages)


def test_a_model_that_stays_silent_gets_an_honest_message_not_an_empty_turn() -> None:
    provider = _Scripted([AssistantMessage(content=None), AssistantMessage(content="  ")])
    provider.model = "deepseek-flash"
    provider.last_finish = "length"
    provider.last_reasoning_chars = 900
    text = _run(provider)
    assert text.strip() and "deepseek-flash" in text and "empty reply" in text
    assert "finish: length" in text and "900 characters of reasoning" in text


def test_an_empty_reply_after_a_tool_round_is_also_caught() -> None:
    provider = _Scripted([_call("run_command"), AssistantMessage(content=""), AssistantMessage(content="Done.")])
    assert _run(provider) == "Done."
