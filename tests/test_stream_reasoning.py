"""The streaming provider keeps a reasoning model's thoughts across a tool loop, closes only its own
stream, and reads gpt-oss tool calls that a host passed through as text."""

from __future__ import annotations

import json
import threading

from backend.providers.base import extract_tool_calls_from_content
from backend.providers.openai_compat import OpenAICompatProvider


class _Stream:
    def __init__(self, chunks: list[dict], *, gate: threading.Event | None = None) -> None:
        self.status_code = 200
        self.encoding = "utf-8"
        self.closed = False
        self._lines = [f"data: {json.dumps(c)}".encode() for c in chunks] + [b"data: [DONE]"]
        self._gate = gate

    def iter_lines(self, decode_unicode=False):
        for index, line in enumerate(self._lines):
            if index == 1 and self._gate is not None:
                self._gate.wait(2)
            if self.closed:
                raise AttributeError("'NoneType' object has no attribute 'read'")
            yield line

    def close(self) -> None:
        self.closed = True


def _tool_chunks(reasoning_key: str = "reasoning") -> list[dict]:
    return [
        {"choices": [{"delta": {reasoning_key: "I should read "}}]},
        {"choices": [{"delta": {reasoning_key: "routes/web.php first."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": '{"path": "routes/web.php"}'}}]}, "finish_reason": "tool_calls"}]},
    ]


def _provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(base_url="https://example.test/v1", api_key="k", model="openai/gpt-oss-20b")


def test_reasoning_is_handed_back_with_the_tool_round(monkeypatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_post", lambda *a, **k: _Stream(_tool_chunks()))
    reply = provider.stream_chat([{"role": "user", "content": "go"}], tools=[])
    assert reply.tool_calls[0].name == "read_file"
    assert reply.raw["reasoning"] == "I should read routes/web.php first."


def test_deepseek_style_reasoning_goes_back_in_its_own_field(monkeypatch) -> None:
    provider = _provider()
    monkeypatch.setattr(provider, "_post", lambda *a, **k: _Stream(_tool_chunks("reasoning_content")))
    reply = provider.stream_chat([{"role": "user", "content": "go"}], tools=[])
    assert reply.raw["reasoning_content"] == "I should read routes/web.php first."
    assert "reasoning" not in reply.raw


def test_openrouter_reasoning_details_are_stitched_back_together(monkeypatch) -> None:
    chunks = [
        {"choices": [{"delta": {"reasoning_details": [{"type": "reasoning.text", "index": 0, "text": "Read "}]}}]},
        {"choices": [{"delta": {"reasoning_details": [{"type": "reasoning.text", "index": 0, "text": "the routes.", "signature": "sig"}]}}]},
        _tool_chunks()[2],
    ]
    provider = _provider()
    monkeypatch.setattr(provider, "_post", lambda *a, **k: _Stream(chunks))
    reply = provider.stream_chat([{"role": "user", "content": "go"}], tools=[])
    assert reply.raw["reasoning_details"] == [{"type": "reasoning.text", "index": 0, "text": "Read the routes.", "signature": "sig"}]


def test_a_plain_answer_carries_no_reasoning_back(monkeypatch) -> None:
    chunks = [{"choices": [{"delta": {"reasoning": "hm"}}]}, {"choices": [{"delta": {"content": "Done."}, "finish_reason": "stop"}]}]
    provider = _provider()
    monkeypatch.setattr(provider, "_post", lambda *a, **k: _Stream(chunks))
    reply = provider.stream_chat([{"role": "user", "content": "go"}], tools=[])
    assert reply.content == "Done." and "reasoning" not in reply.raw


def test_one_call_ending_does_not_cut_off_another_call_on_the_same_provider(monkeypatch) -> None:
    """A shared ``_response`` slot let one call's cleanup close another's stream mid-read."""
    provider = _provider()
    gate = threading.Event()
    slow = _Stream([{"choices": [{"delta": {"content": "slow "}}]}, {"choices": [{"delta": {"content": "reply"}}]}], gate=gate)
    fast = _Stream([{"choices": [{"delta": {"content": "fast"}}]}])
    streams = iter([slow, fast])
    monkeypatch.setattr(provider, "_post", lambda *a, **k: next(streams))
    result: dict[str, str] = {}

    def run_slow() -> None:
        result["slow"] = provider.stream_chat([{"role": "user", "content": "a"}]).content or ""

    worker = threading.Thread(target=run_slow)
    worker.start()
    result["fast"] = provider.stream_chat([{"role": "user", "content": "b"}]).content or ""
    gate.set()
    worker.join(5)
    assert result == {"fast": "fast", "slow": "slow reply"}


def test_stop_closes_the_stream_and_ends_the_call_quietly(monkeypatch) -> None:
    provider = _provider()
    gate = threading.Event()
    stream = _Stream([{"choices": [{"delta": {"content": "partial"}}]}, {"choices": [{"delta": {"content": " more"}}]}], gate=gate)
    monkeypatch.setattr(provider, "_post", lambda *a, **k: stream)
    result: dict[str, str | None] = {}
    worker = threading.Thread(target=lambda: result.setdefault("text", provider.stream_chat([{"role": "user", "content": "a"}]).content))
    worker.start()
    while not provider._streams:
        pass
    provider.close_stream()
    gate.set()
    worker.join(5)
    assert result["text"] == "partial"  # no AttributeError, no "failed:"


def test_gpt_oss_tool_calls_leaked_as_text_are_run() -> None:
    tools = [{"type": "function", "function": {"name": n}} for n in ("run_smoke_test", "read_file")]
    leaked = '<|channel|>commentary to=functions.read_file <|constrain|>json<|message|>{"path": "a.php"}<|call|>'
    calls = extract_tool_calls_from_content(leaked, tools, strict=True)
    assert [(c.name, c.arguments) for c in calls] == [("read_file", {"path": "a.php"})]
    garbled = "<|channel|>commentary to=functions.run_smoke_test<|constrain|>??]<|call|>"
    assert [c.name for c in extract_tool_calls_from_content(garbled, tools, strict=True)] == ["run_smoke_test"]
    assert extract_tool_calls_from_content("to=functions.rm_rf <|call|>", tools, strict=True) == []
