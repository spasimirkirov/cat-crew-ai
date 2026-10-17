"""Project model overrides: project pick -> global pick, per role, applied by the provider factory."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core import provider_factory
from backend.core.chat_service import ChatService
from backend.services import model_resolver, settings_store
from backend.services.model_resolver import get_effective_model


@pytest.fixture
def cfg(isolated, monkeypatch):
    """Global picks: Chat on Groq, Coding on OpenRouter."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-openrouter")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-groq")
    settings_store.save_role_brain("companion", "groq", "qwen-chat")
    settings_store.save_role_brain("coding", "openrouter", "deepseek/deepseek-v4-pro")
    return isolated


def test_without_an_override_the_global_pick_is_used(cfg) -> None:
    eff = get_effective_model("p1", "coding")
    assert (eff.host, eff.model, eff.source, eff.explicit) == ("openrouter", "deepseek/deepseek-v4-pro", "global", True)
    chat = get_effective_model("p1", "chat")  # the wire name works as well as the settings name
    assert (chat.host, chat.model) == ("groq", "qwen-chat")


def test_a_project_override_wins_for_its_role_only(cfg) -> None:
    model_resolver.set_project_overrides("p1", {"coding": {"host": "openrouter", "model": "anthropic/claude-x"}})
    assert get_effective_model("p1", "coding").model == "anthropic/claude-x"
    assert get_effective_model("p1", "coding").source == "project"
    assert get_effective_model("p1", "chat").host == "groq"  # other roles are untouched
    assert get_effective_model("p2", "coding").host == "openrouter"  # and so are other projects
    assert get_effective_model("p2", "coding").model == "deepseek/deepseek-v4-pro"


def test_inherit_clears_the_override_and_it_follows_the_global_setting_again(cfg) -> None:
    model_resolver.set_project_overrides("p1", {"coding": {"host": "openrouter", "model": "anthropic/claude-x"}})
    model_resolver.set_project_overrides("p1", {"coding": {"host": "inherit", "model": ""}})
    assert get_effective_model("p1", "coding").source == "global"
    assert model_resolver.overrides_for_project("p1") == {}
    settings_store.save_role_brain("coding", "openrouter", "deepseek/deepseek-other")  # a later global change reaches the project
    assert get_effective_model("p1", "coding").model == "deepseek/deepseek-other"


def test_an_unset_role_follows_the_projects_chat_override(cfg) -> None:
    settings_store.save_role_brain("memory", "", "")  # Memory unset globally: follows Chat
    model_resolver.set_project_overrides("p1", {"chat": {"host": "openrouter", "model": "anthropic/claude-chat"}})
    eff = get_effective_model("p1", "memory")
    assert (eff.host, eff.model, eff.explicit) == ("openrouter", "anthropic/claude-chat", False)
    assert get_effective_model("p1", "vision").explicit is False  # follows Chat: "do not switch models"
    model_resolver.set_project_overrides("p1", {"vision": {"host": "openrouter", "model": "v"}})
    assert get_effective_model("p1", "vision").explicit is True  # its own pick: switch


def test_an_override_whose_host_lost_its_key_falls_back_instead_of_stranding_the_project(cfg, monkeypatch) -> None:
    model_resolver.set_project_overrides("p1", {"coding": {"host": "openrouter", "model": "anthropic/claude-x"}})
    monkeypatch.delenv("OPENROUTER_API_KEY")
    eff = get_effective_model("p1", "coding")
    # OpenRouter is the only cloud host: losing its key strands both the override and the global
    # pick, so Coding falls all the way back to Chat's host (LM Studio needs no key).
    assert (eff.host, eff.source) == ("groq", "global")


def test_the_key_is_available_to_callers_but_never_shown(cfg) -> None:
    eff = get_effective_model("p1", "coding")
    is_the_test_key = eff.api_key == "sk-test-openrouter"  # a plain bool, so a failure never prints a key
    assert is_the_test_key
    assert "sk-test" not in repr(eff) and "api_key" not in eff.to_dict()


def test_the_provider_factory_builds_the_projects_model(cfg, monkeypatch) -> None:
    built: list[tuple[str, str]] = []
    monkeypatch.setattr(provider_factory, "provider_from_brain", lambda host, model: built.append((host, model)) or object())
    model_resolver.set_project_overrides("p1", {"coding": {"host": "openrouter", "model": "anthropic/claude-x"}})
    provider_factory.provider_for_turn({}, "p1", room=True)
    provider_factory.provider_for_turn({}, "p2", room=True)
    provider_factory.provider_for_turn({}, "p1", room=True, role="companion")  # Cat Crew's free chat: the Chat role
    assert built == [
        ("openrouter", "anthropic/claude-x"),
        ("openrouter", "deepseek/deepseek-v4-pro"),
        ("groq", "qwen-chat"),
    ]
    assert provider_factory.provider_if_explicit("vision", "p1") is None  # unset: no switch
    model_resolver.set_project_overrides("p1", {"vision": {"host": "openrouter", "model": "vis"}})
    provider_factory.provider_if_explicit("vision", "p1")
    assert built[-1] == ("openrouter", "vis") and provider_factory.provider_if_explicit("vision", "p2") is None


def test_memory_compression_and_the_planning_phase_resolve_per_project(cfg, monkeypatch) -> None:
    from backend.platform import runtime
    from backend.platform.sop import _planning_provider
    from backend.services import compaction

    built: list[tuple[str, str]] = []
    monkeypatch.setattr(provider_factory, "provider_from_brain", lambda host, model: built.append((host, model)) or object())
    model_resolver.set_project_overrides(
        "p1", {"memory": {"host": "openrouter", "model": "anthropic/sum"}, "planning": {"host": "openrouter", "model": "deepseek/plan"}}
    )
    token = runtime.set_project_id("p1")
    try:
        compaction.summarizer_provider()
    finally:
        runtime.reset_project_id(token)
    _planning_provider("architecture", "p1")
    assert built == [("openrouter", "anthropic/sum"), ("openrouter", "deepseek/plan")]


# ---- API ---------------------------------------------------------------------------------------


@pytest.fixture
def client(cfg):
    with TestClient(create_app(chat_service=ChatService(use_memory=False)), base_url="http://127.0.0.1") as c:
        yield c


def _project(client, tmp_path) -> str:
    r = client.post("/api/projects", json={"name": "Alpha", "folders": [str(tmp_path / "ws")]})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_project_models_api_roundtrip(client, tmp_path) -> None:
    pid = _project(client, tmp_path)
    state = client.get(f"/api/projects/{pid}/models").json()
    assert state["overrides"] == {}
    assert state["global"]["coding"] == {"host": "openrouter", "model": "deepseek/deepseek-v4-pro"}
    assert state["effective"]["coding"]["source"] == "global"
    assert "sk-test" not in str(state)

    r = client.put(f"/api/projects/{pid}/models", json={"roles": {"coding": {"host": "openrouter", "model": "anthropic/claude-x"}}})
    assert r.status_code == 200
    assert r.json()["overrides"] == {"coding": {"host": "openrouter", "model": "anthropic/claude-x"}}
    assert r.json()["effective"]["coding"] == {"host": "openrouter", "model": "anthropic/claude-x", "source": "project", "explicit": True}
    # the "Use Global Setting" label keeps showing the global pick while the project overrides it
    assert r.json()["global_effective"]["coding"]["host"] == "openrouter"
    assert r.json()["global_effective"]["coding"]["model"] == "deepseek/deepseek-v4-pro"
    assert r.json()["global_effective"]["memory"]["source"] == "chat"  # unset globally: follows Chat

    r = client.put(f"/api/projects/{pid}/models", json={"roles": {"coding": {"host": "inherit"}, "chat": None}})
    assert r.json()["overrides"] == {} and r.json()["effective"]["coding"]["source"] == "global"

    # global picks untouched by all of this
    assert client.get("/api/models").json()["roles"]["coding"] == {"host": "openrouter", "model": "deepseek/deepseek-v4-pro"}


def test_project_models_api_validation_and_cleanup(client, tmp_path) -> None:
    pid = _project(client, tmp_path)
    assert client.get("/api/projects/nope/models").status_code == 404
    assert client.put(f"/api/projects/{pid}/models", json={"roles": {"telepathy": {"host": "groq", "model": "m"}}}).status_code == 422
    assert client.put(f"/api/projects/{pid}/models", json={"roles": {"coding": {"host": "nowhere", "model": "m"}}}).status_code == 422
    assert client.put(f"/api/projects/{pid}/models", json={"roles": {"coding": {"host": "groq", "model": " "}}}).status_code == 422
    assert client.put(f"/api/projects/{pid}/models", json={"roles": {"images": {"host": "groq", "model": "m"}}}).status_code == 422
    client.put(f"/api/projects/{pid}/models", json={"roles": {"coding": {"host": "groq", "model": "m"}}})
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert settings_store.project_role_overrides(pid) == {}  # nothing left behind


def test_project_images_override_accepts_either_comfyui_or_openrouter(client, tmp_path) -> None:
    """Images used to be locked to ComfyUI everywhere; OpenRouter is now a real second host for it."""
    pid = _project(client, tmp_path)
    r = client.put(f"/api/projects/{pid}/models", json={"roles": {"images": {"host": "comfyui", "model": "sd_xl_turbo.safetensors"}}})
    assert r.status_code == 200
    assert r.json()["overrides"]["images"] == {"host": "comfyui", "model": "sd_xl_turbo.safetensors"}

    r = client.put(f"/api/projects/{pid}/models", json={"roles": {"images": {"host": "openrouter", "model": "google/gemini-2.5-flash-image"}}})
    assert r.status_code == 200
    assert r.json()["overrides"]["images"] == {"host": "openrouter", "model": "google/gemini-2.5-flash-image"}

    # still not just any host
    assert client.put(f"/api/projects/{pid}/models", json={"roles": {"images": {"host": "groq", "model": "m"}}}).status_code == 422
