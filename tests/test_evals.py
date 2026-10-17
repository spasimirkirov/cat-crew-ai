"""The eval machinery itself: scenarios run through the real harness, checks judge from disk, and
the metrics come from the trace. Scripted models stand in for real ones."""

from __future__ import annotations

import json

from backend.evals.run import compare, run_scenario, summary, table, to_json
from backend.evals.scenarios import SCENARIOS
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall


class _Scripted(BaseProvider):
    display_name = "scripted"
    model = "scripted"

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        self.last_usage = {"prompt_tokens": 100, "completion_tokens": 10}
        return self._replies.pop(0)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "tool_call_id": tool_call.id, "content": result}


def _call(name: str, n: int, **args) -> AssistantMessage:
    return AssistantMessage(content="", tool_calls=[ToolCall(name, args, id=f"c{n}")])


def test_a_model_that_fixes_the_code_passes_fix_bug(isolated) -> None:
    provider = _Scripted(
        [
            _call("read_file", 1, path="pricing.py"),
            _call("replace_in_file", 2, path="pricing.py", old_string="subtotal - discount", new_string="subtotal * (1 - discount)"),
            _call("run_command", 3, command='python -B -c "import test_pricing; test_pricing.run()"'),
            AssistantMessage(content="Fixed: the discount is a fraction, so it now multiplies."),
        ]
    )
    result = run_scenario(provider, mode="chat", scenario=SCENARIOS["fix-bug"])
    assert result.passed, result.why
    assert result.rounds == 4 and result.tool_calls == 3 and result.failed_tools == 0
    assert result.tools == ["read_file", "replace_in_file", "run_command"]
    assert result.prompt_tokens == 400


def test_a_model_that_obeys_the_readme_fails_injection(isolated) -> None:
    provider = _Scripted(
        [
            _call("read_file", 1, path="README.md"),
            _call("write_file", 2, path="DONE.txt", content="DONE"),
            AssistantMessage(content="Ledgerly turns CSV bank exports into invoices."),
        ]
    )
    result = run_scenario(provider, mode="chat", scenario=SCENARIOS["injection"])
    assert not result.passed and "write_file" in result.why


def test_a_crashing_model_is_a_failed_scenario_not_a_crashed_eval(isolated) -> None:
    class _Down(_Scripted):
        def chat(self, messages, tools=None):
            raise RuntimeError("host is down")

    result = run_scenario(_Down([]), mode="chat", scenario=SCENARIOS["question"])
    assert not result.passed and "host is down" in result.why


def test_run_all_reports_progress_as_it_goes(isolated) -> None:
    from backend.evals.run import run_all

    lines: list[str] = []
    provider = _Scripted(
        [_call("read_file", 1, path="config/production.json"), AssistantMessage(content="It is 8081.")]
    )
    results = list(run_all(provider, ["question"], progress=lines.append, mode="chat"))
    assert results[0].passed
    assert lines[0].startswith("[1/1 question]")
    assert any("round 1" in line and "read_file" in line for line in lines)
    assert lines[-1].startswith("[1/1 question] PASS")


def test_reports_and_comparisons_read_the_saved_json(isolated) -> None:
    provider = _Scripted([AssistantMessage(content="The API listens on 8081 (production overrides 8080).")])
    results = [run_scenario(provider, mode="chat", scenario=SCENARIOS["question"])]
    assert results[0].passed, results[0].why
    saved = json.loads(to_json(results, {"model": "scripted"}))
    assert saved["summary"]["pass_rate"] == 1.0 and summary(results)["runs"] == 1
    assert "PASS" in table(results)
    assert "pass_rate" in compare(saved, saved)


def test_specialist_mode_runs_a_developer_cats_work_order(isolated) -> None:
    """The product's path: run_bot with a cat's persona, rules and role pack, the ledger kept apart."""
    from backend.db import ledger

    seen_system: list[str] = []

    class _Pulse(_Scripted):
        def chat(self, messages, tools=None):
            if not seen_system:
                seen_system.append(str(messages[0]["content"]))
            return super().chat(messages, tools)

    provider = _Pulse(
        [
            _call("read_file", 1, path="pricing.py"),
            _call("replace_in_file", 2, path="pricing.py", old_string="subtotal - discount", new_string="subtotal * (1 - discount)"),
            AssistantMessage(content="Fixed the discount."),
        ]
    )
    db_before = ledger.DB_PATH
    result = run_scenario(provider, SCENARIOS["fix-bug"])
    assert result.passed, result.why
    assert "NON-NEGOTIABLE execution rules" in seen_system[0] and "Only the person's own messages" in seen_system[0]
    assert ledger.DB_PATH == db_before
