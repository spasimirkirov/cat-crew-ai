"""The context window per provider, and the compression threshold derived from it."""

from __future__ import annotations

import pytest

from backend import config
from backend.services import compaction, model_discovery, provider_limits
from backend.services.lm_runtime import LmModel
from backend.services.lm_runtime import HostSnapshot
from backend.services.provider_limits import get_model_context_limit, limits_for_provider, threshold_for


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_TOKEN_LIMIT", 0)
    provider_limits.clear_cache()
    model_discovery.clear_cache()
    yield
    provider_limits.clear_cache()
    model_discovery.clear_cache()


def _pair(host: str, model: str = "") -> tuple[int, int]:
    row = get_model_context_limit(host, model)
    return row["max_tokens"], row["compression_threshold"]


def test_the_four_clouds_have_their_documented_windows():
    assert _pair("deepseek", "deepseek-v4-pro") == (64_000, 54_000)
    assert _pair("gemini", "gemini-2.5-flash") == (1_000_000, 850_000)
    assert _pair("anthropic", "claude-sonnet") == (200_000, 170_000)
    assert _pair("openai", "gpt-5") == (128_000, 110_000)
    assert _pair("DeepSeek") == (64_000, 54_000)  # case does not matter


def test_the_threshold_is_about_85_percent_of_the_window():
    assert threshold_for(64_000) == 54_400 and threshold_for(1_000_000) == 850_000
    for host in ("deepseek", "gemini", "anthropic", "openai"):
        window, threshold = _pair(host)
        assert 0.84 <= threshold / window <= 0.86


def test_groq_and_openrouter_follow_the_model():
    assert _pair("groq", "llama-3.3-70b-versatile")[0] == 128_000
    assert _pair("groq", "some-small-model")[0] == 32_768
    assert _pair("openrouter", "google/gemini-2.5-pro") == (1_000_000, 850_000)
    assert _pair("openrouter", "anthropic/claude-sonnet") == (200_000, 170_000)
    assert _pair("openrouter", "mystery/model")[0] == 64_000


def test_an_unknown_host_gets_a_small_safe_window():
    assert _pair("brand-new-host", "x") == (16_384, 14_000)


def _snapshot(monkeypatch, models: list[LmModel]) -> None:
    snap = HostSnapshot(kind="groq", online=True, models=models)
    monkeypatch.setattr(model_discovery, "cached_snapshot", lambda kind, force=False: snap)
    monkeypatch.setattr(model_discovery, "peek_snapshot", lambda kind: snap)


def test_the_environment_can_force_one_threshold(monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_TOKEN_LIMIT", 20_000)
    row = get_model_context_limit("gemini", "x")
    assert row["compression_threshold"] == 20_000 and row["max_tokens"] >= 20_000 and row["source"] == "env"


def test_it_never_raises(monkeypatch):
    monkeypatch.setattr(provider_limits, "_groq", lambda *a, **k: 1 / 0)
    row = get_model_context_limit("groq", "x")
    assert row["max_tokens"] == 16_384
    assert limits_for_provider(object())["max_tokens"] > 0


class _P:
    def __init__(self, host_id: str, model: str = "m") -> None:
        self.host_id, self.model = host_id, model


def test_the_budget_asks_the_model_that_will_be_called(monkeypatch):
    """The same conversation is left alone on a 1M model and compressed on a small one."""
    monkeypatch.setattr(compaction, "COMPACT_KEEP", 4)
    turns = [{"role": "system", "content": "s"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 4_000} for i in range(20)
    ]  # ~20,000 tokens

    class _Sum:
        host_id, model = "openrouter", "memory"

        def chat(self, messages, tools=None):
            from backend.providers.base import AssistantMessage

            return AssistantMessage(content="## Goal\nshort")

    big = compaction.ContextBudget(summarizer=lambda: (_Sum(), "mem"))
    assert big.apply(list(turns), _P("gemini")) is False and (big.max_tokens, big.limit) == (1_000_000, 850_000)
    assert big.apply(list(turns), _P("deepseek")) is False and big.max_tokens == 64_000  # 20k < 54k
    small = compaction.ContextBudget(summarizer=lambda: (_Sum(), "mem"))
    messages = list(turns)
    assert small.apply(messages, _P("brand-new-host")) is True  # 20k >= 14k
    assert (small.max_tokens, small.limit) == (16_384, 14_000)
    assert compaction.estimate_tokens(messages) < 14_000


def test_it_compresses_exactly_at_the_threshold_not_before(monkeypatch):
    class _Sum:
        host_id, model = "x", "m"

        def chat(self, messages, tools=None):
            from backend.providers.base import AssistantMessage

            return AssistantMessage(content="s")

    monkeypatch.setattr(compaction, "COMPACT_KEEP", 2)

    def chat_of(tokens: int):
        each = 400
        n = tokens // each
        return [{"role": "system", "content": ""}] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * (each * 4)} for i in range(n)
        ]

    below = chat_of(13_600)
    at = chat_of(14_000)
    budget = compaction.ContextBudget(summarizer=lambda: (_Sum(), "mem"))
    assert compaction.estimate_tokens(below) < 14_000 <= compaction.estimate_tokens(at)
    assert budget.apply(below, _P("unknown-host")) is False
    assert budget.apply(at, _P("unknown-host")) is True
