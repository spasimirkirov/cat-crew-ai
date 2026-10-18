"""Fallback role: a model turned away (rate limit, no credit) hands the run to the Fallback model."""

from __future__ import annotations

import pytest

from backend.core import provider_factory
from backend.harness import model_block
from backend.harness.loop import run_agent
from backend.platform.runtime import reset_project_id, set_project_id
from backend.providers.base import AssistantMessage, BaseProvider, ToolCall
from backend.services import settings_store
from backend.services.model_resolver import get_effective_model


class _Blocked(BaseProvider):
    host_id = "openrouter"
    model = "google/gemma:free"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None) -> AssistantMessage:
        self.calls += 1
        error = model_block.ModelUnavailable(
            "HTTP 429", host=self.host_id, model=self.model, status=429, kind="rate_limited"
        )
        model_block.report("p1", error)
        raise error

    def tool_message(self, tool_call: ToolCall, result: str) -> dict:
        return {"role": "tool", "content": result}


class _Backup(BaseProvider):
    host_id = "groq"
    model = "qwen-backup"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None) -> AssistantMessage:
        self.calls += 1
        return AssistantMessage(content="answered by the fallback")

    def tool_message(self, tool_call: ToolCall, result: str) -> dict:
        return {"role": "tool", "content": result}


@pytest.fixture
def project(isolated, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    token = set_project_id("p1")
    yield "p1"
    reset_project_id(token)
    model_block.reset()


def test_fallback_is_unset_by_default(project) -> None:
    eff = get_effective_model(project, "fallback")
    assert (eff.host, eff.model, eff.explicit) == ("", "", False)


def test_without_a_fallback_the_block_stops_the_run(project) -> None:
    with pytest.raises(model_block.ModelUnavailable):
        run_agent(_Blocked(), [{"role": "user", "content": "hi"}], [], lambda n, a: "")
    assert model_block.pending(project) is not None


def test_a_blocked_model_hands_over_to_the_fallback_and_stays_handed_over(project, monkeypatch) -> None:
    settings_store.save_role_brain("fallback", "groq", "qwen-backup")
    backup = _Backup()
    built: list[tuple[str, str]] = []

    def make(host, model):
        built.append((host, model))
        return backup

    monkeypatch.setattr(provider_factory, "provider_from_brain", make)
    blocked = _Blocked()
    out = run_agent(blocked, [{"role": "user", "content": "hi"}], [], lambda n, a: "")
    assert out == "answered by the fallback"
    assert built == [("groq", "qwen-backup")] and blocked.calls == 1
    assert model_block.pending(project) is None  # the pipeline must not stop between phases
    # The next phase starts on the blocked model: it goes straight to the fallback this time.
    again = _Blocked()
    assert run_agent(again, [{"role": "user", "content": "next"}], [], lambda n, a: "") == "answered by the fallback"
    assert again.calls == 0


def test_the_fallback_is_never_the_model_that_was_turned_away(project, monkeypatch) -> None:
    settings_store.save_role_brain("fallback", "openrouter", "google/gemma:free")
    monkeypatch.setattr(provider_factory, "provider_from_brain", lambda h, m: _Backup())
    with pytest.raises(model_block.ModelUnavailable):
        run_agent(_Blocked(), [{"role": "user", "content": "hi"}], [], lambda n, a: "")
