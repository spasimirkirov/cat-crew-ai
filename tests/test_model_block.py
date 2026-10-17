"""A model turned away (rate limit, no credit): the run stops at once and offers a switch + retry."""

from __future__ import annotations

import time
from typing import Any

import pytest

from backend.core import chat_service as cs
from backend.core.chat_service import ROOM, ChatService
from backend.harness import model_block
from backend.providers.base import AssistantMessage, BaseProvider, ToolCall
from backend.services import settings_store
from tests.test_chat_service import project  # noqa: F401 - fixture (recorder comes from conftest)

FREE = "google/gemma-4-26b-a4b-it:free"


class _RateLimited(BaseProvider):
    """Behaves like OpenRouter turning a free model away: records the block, raises."""

    display_name = "OpenRouter"
    host_id = "openrouter"
    model = FREE

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None) -> AssistantMessage:
        from backend.platform.runtime import current_project_id

        self.calls += 1
        error = model_block.ModelUnavailable(
            f"OpenRouter HTTP 429: {FREE} is temporarily rate-limited upstream.",
            host="openrouter",
            model=FREE,
            status=429,
            kind="rate_limited",
        )
        model_block.report(current_project_id(), error)
        raise error

    def tool_message(self, tool_call: ToolCall, result: str) -> dict[str, Any]:
        return {"role": "tool", "content": result}


@pytest.fixture
def svc(recorder, monkeypatch):  # noqa: F811
    service = ChatService(use_memory=False)
    service.provider = _RateLimited()
    monkeypatch.setattr(cs.provider_factory, "provider_for_turn", lambda *a, **k: service.provider)
    monkeypatch.setattr(cs.provider_factory, "caps_for_current", lambda session, *_a, **_k: ("openrouter", FREE, [], True))
    yield service
    service.close()


def _wait_idle(svc: ChatService, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if svc.current_turn() is None:
            return
        time.sleep(0.05)
    raise AssertionError("the turn did not end")


def test_a_rate_limited_model_stops_the_whole_run_and_offers_a_switch(svc, project, recorder, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    settings_store.save_role_brain("companion", "openrouter", FREE)
    settings_store.save_role_brain("coding", "openrouter", FREE)
    svc.submit(project.id, ROOM, "Build a todo app with React and a FastAPI backend")  # the full pipeline
    _wait_idle(svc)

    assert svc.provider.calls == 1  # the first phase hit the wall; no other cat walked into it
    failed = recorder.of("turn.failed")
    assert len(failed) == 1 and not recorder.of("turn.finished")
    issue = failed[0].data["model_issue"]
    assert issue["model"] == FREE and issue["host"] == "openrouter" and issue["kind"] == "rate_limited" and issue["free"] is True
    assert {r["role"] for r in issue["roles"]} >= {"chat", "coding"}
    assert model_block.pending(project.id) is None  # handed over, not left to stop the next run


def test_switching_the_model_everywhere_then_retrying_runs_the_same_message(svc, project, recorder, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
    settings_store.save_role_brain("companion", "openrouter", FREE)
    settings_store.save_role_brain("planning", "openrouter", FREE)
    settings_store.save_project_role_overrides(project.id, {"coding": {"host": "openrouter", "model": FREE}})
    svc.submit(project.id, ROOM, "fix the bug in the login endpoint")
    _wait_idle(svc)
    assert recorder.of("turn.failed")[0].data["model_issue"]["model"] == FREE

    changed = settings_store.replace_model_everywhere("openrouter", FREE, "deepseek", "deepseek-chat")
    assert set(changed) == {"chat (global)", "planning (global)", f"coding (project {project.id})"}
    assert settings_store.saved_role_brain("companion") == ("deepseek", "deepseek-chat")
    assert settings_store.project_role_overrides(project.id)["coding"] == {"host": "deepseek", "model": "deepseek-chat"}

    seen: list[str] = []
    monkeypatch.setattr(svc, "submit", lambda pid, bot, text, intent=None: seen.append(text) or {"turn_id": "t", "queued": False})
    svc.retry(project.id)
    assert seen == ["fix the bug in the login endpoint"]
    with pytest.raises(cs.ChatError):
        svc.retry(project.id)  # only once


def test_a_free_model_429_is_not_retried(isolated, monkeypatch):
    from backend.providers.openai_compat import OpenRouterProvider

    calls: list[int] = []

    class _TooMany:
        status_code = 429
        encoding = "utf-8"
        headers: dict = {}
        content = b'{"error": {"message": "rate-limited upstream"}}'

        def json(self):
            return {"error": {"message": "rate-limited upstream"}}

        def close(self):
            pass

    monkeypatch.setattr("backend.providers.openai_compat.requests.post", lambda *a, **k: calls.append(1) or _TooMany())
    with pytest.raises(model_block.ModelUnavailable) as caught:
        OpenRouterProvider(api_key="sk-or-test", model=FREE).chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 1 and caught.value.kind == "rate_limited"


def test_the_replace_endpoint_needs_a_usable_target(isolated, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app

    with TestClient(create_app(chat_service=ChatService(use_memory=False)), base_url="http://127.0.0.1:8765") as client:
        body = {"from_host": "openrouter", "from_model": FREE, "to_host": "groq", "to_model": "llama-3.3-70b-versatile"}
        assert client.post("/api/models/replace", json=body).status_code == 422  # Groq has no key
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        settings_store.save_role_brain("companion", "openrouter", FREE)
        r = client.post("/api/models/replace", json=body)
        assert r.status_code == 200 and r.json()["changed"] == ["chat (global)"]
        assert r.json()["roles"]["chat"] == {"host": "groq", "model": "llama-3.3-70b-versatile"}
        assert client.post("/api/projects/nope/retry").status_code == 404


def test_a_phase_whose_model_failed_is_recorded_as_failed_not_done(svc, project, monkeypatch):  # noqa: F811
    """A planning cat needs no tools, so its reply used to count as done -- a 429 included."""
    from backend.db import ledger

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    svc.submit(project.id, ROOM, "Build a todo app with React and a FastAPI backend")
    _wait_idle(svc)
    statuses = {t["title"]: t["status"] for t in ledger.list_tasks_for_project(project.id)}
    blueprint = next(status for title, status in statuses.items() if "blueprint" in title.lower())
    assert blueprint == "failed"


def test_a_question_reads_as_a_conversation_on_the_card(svc, project, recorder):  # noqa: F811
    from backend.core.chat_service import Turn, _Hooks

    turn = Turn(id="t_q", project_id=project.id, bot_id=ROOM, kind="room", started_at="", session={})
    hooks = _Hooks(svc, turn)
    hooks.on_tool_preview("ask_user", '{"choices": ["Livewire", "Inertia"], "prompt": "Livewire or Inertia?"}', "User answered: Livewire")
    step = recorder.of("chat.activity")[-1].data["step"]
    assert step["line"] == "Asked you: Livewire or Inertia?" and step["preview"] == "You answered: Livewire"
