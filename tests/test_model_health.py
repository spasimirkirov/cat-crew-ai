"""Start-up validation of the models picked for each role, and the model API around it."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.chat_service import ChatService
from backend.services import api_access, model_discovery, model_health, settings_store
from backend.services.lm_runtime import HostSnapshot, LmModel

BASE = "http://127.0.0.1:8765"


def _snap(kind: str, names: list[str], online: bool = True, error_kind: str = "") -> HostSnapshot:
    return HostSnapshot(
        kind=kind,
        online=online,
        error_kind=error_kind,
        error="" if online else "could not be reached",
        models=[LmModel(name=n, loaded=True, capabilities=["chat", "tools"]) for n in names],
        checked_at=time.time(),
    )


@pytest.fixture
def world(isolated, monkeypatch):
    """No real keys or hosts: tests describe the hosts they want."""
    for spec in api_access.CATALOG:
        for name in spec.env_names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api_access, "BRAVE_SEARCH_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")  # the host the role tests run on
    model_health.invalidate()
    model_discovery.clear_cache()
    hosts: dict[str, HostSnapshot] = {}
    probed: list[str] = []

    def fake(host, force=False, variant=""):
        probed.append(host)
        return hosts.get(host, _snap(host, [], online=False, error_kind="unreachable"))

    monkeypatch.setattr(model_discovery, "cached_snapshot", fake)
    world = type("World", (), {"hosts": hosts, "probed": probed})
    yield world
    model_health.invalidate()


def _issues(report):
    return {(i.role, i.reason): i for i in report.issues}


def test_nothing_picked_means_nothing_to_check(world) -> None:
    report = model_health.check()
    assert report.ok and report.issues == [] and world.probed == []


def test_a_model_that_is_still_listed_is_fine(world) -> None:
    settings_store.save_role_brain("companion", "groq", "qwen2.5-7b")
    world.hosts["groq"] = _snap("groq", ["Qwen2.5-7B", "other"])  # names match ignoring case
    report = model_health.check()
    assert report.ok
    assert report.hosts["groq"].online and report.hosts["groq"].model_count == 2


def test_a_retired_model_is_reported_by_role_and_name(world) -> None:
    settings_store.save_settings({"api_access": {"openrouter_key": "k"}})
    settings_store.save_role_brain("memory", "openrouter", "google/gemini-1.0-pro")
    world.hosts["openrouter"] = _snap("openrouter", ["deepseek/deepseek-v4-pro", "anthropic/claude-sonnet-5"])
    report = model_health.check()
    issue = _issues(report)[("memory", "missing")]
    assert issue.model == "google/gemini-1.0-pro" and issue.role_label == "Memory" and issue.host == "openrouter"
    assert issue.message == "The model 'google/gemini-1.0-pro' assigned to 'Memory' is no longer available on OpenRouter."


def test_offline_host_is_told_apart_from_a_missing_model(world) -> None:
    settings_store.save_role_brain("companion", "groq", "qwen2.5-7b")
    settings_store.save_role_brain("coding", "groq", "qwen2.5-coder")
    world.hosts["groq"] = _snap("groq", [], online=False, error_kind="unreachable")
    report = model_health.check()
    assert {i.reason for i in report.issues} == {"offline"}
    assert {i.role for i in report.issues} == {"chat", "coding"}
    assert "Groq is offline" in report.issues[0].message
    assert world.probed.count("groq") == 1  # one probe per host, however many roles use it


def test_a_cloud_host_without_a_key_is_not_probed(world) -> None:
    settings_store.save_role_brain("planning", "openrouter", "deepseek/deepseek-reasoner")
    report = model_health.check()
    assert _issues(report)[("planning", "no_key")].message.endswith("needs a OpenRouter API key, and none is set.")
    assert world.probed == []


def test_a_disabled_host_is_reported(world) -> None:
    settings_store.save_settings({"api_access": {"openrouter_key": "k", "openrouter_enabled": False}})
    settings_store.save_role_brain("vision", "openrouter", "meta-llama/llama-4-scout")
    assert _issues(model_health.check())[("vision", "disabled")].host_label == "OpenRouter"


def test_a_rejected_key_counts_as_offline(world) -> None:
    settings_store.save_settings({"api_access": {"openrouter_key": "bad"}})
    settings_store.save_role_brain("coding", "openrouter", "anthropic/claude-sonnet-5")
    world.hosts["openrouter"] = _snap("openrouter", [], online=False, error_kind="auth")
    assert ("coding", "offline") in _issues(model_health.check())


def test_report_is_cached_and_a_refresh_or_invalidate_recomputes(world) -> None:
    settings_store.save_role_brain("companion", "groq", "m")
    world.hosts["groq"] = _snap("groq", ["m"])
    assert model_health.current().ok
    world.hosts["groq"] = _snap("groq", [])
    assert model_health.current().ok  # cached
    assert not model_health.current(refresh=True).ok
    world.hosts["groq"] = _snap("groq", ["m"])
    model_health.invalidate()
    assert model_health.current().ok


# -- the API ------------------------------------------------------------------------------------


@pytest.fixture
def client(world):
    with TestClient(create_app(chat_service=ChatService(use_memory=False)), base_url=BASE) as c:
        yield c


def test_health_endpoint_shape(client, world) -> None:
    settings_store.save_role_brain("memory", "groq", "gone-model")
    world.hosts["groq"] = _snap("groq", ["other"])
    body = client.get("/api/models/health", params={"refresh": "true"}).json()
    assert body["ok"] is False
    assert body["hosts"]["groq"] == {"online": True, "error_kind": "", "model_count": 1}
    assert body["issues"] == [
        {
            "role": "memory",
            "role_label": "Memory",
            "host": "groq",
            "host_label": "Groq",
            "model": "gone-model",
            "reason": "missing",
            "message": "The model 'gone-model' assigned to 'Memory' is no longer available on Groq.",
        }
    ]
    assert isinstance(body["checked_at"], float)


def test_saving_roles_invalidates_the_report(client, world) -> None:
    world.hosts["groq"] = _snap("groq", ["good"])
    assert client.get("/api/models/health").json()["ok"] is True
    r = client.put("/api/models/roles", json={"vision": {"host": "groq", "model": "missing-vlm"}})
    assert r.status_code == 200 and r.json()["roles"]["vision"] == {"host": "groq", "model": "missing-vlm"}
    assert r.json()["roles"]["planning"] == {"host": "", "model": ""}
    body = client.get("/api/models/health").json()  # no refresh flag: the save alone made it re-check
    assert [i["role"] for i in body["issues"]] == ["vision"]


def test_refresh_models_endpoint(client, world) -> None:
    world.hosts["groq"] = HostSnapshot(
        kind="groq",
        online=True,
        models=[LmModel(name="qwen2-vl-7b", loaded=True, capabilities=["vision", "tools", "chat"], context_length=32768, display_name="Qwen2 VL")],
        checked_at=123.0,
    )
    r = client.post("/api/hosts/groq/refresh-models")
    assert r.status_code == 200
    assert r.json() == {
        "host": "groq",
        "online": True,
        "error": "",
        "error_kind": "",
        "checked_at": 123.0,
        "models": [
            {
                "name": "qwen2-vl-7b",
                "display_name": "Qwen2 VL",
                "loaded": True,
                "capabilities": ["vision", "tools", "chat"],
                "context_length": 32768,
                "price_prompt_per_1m": None,
                "price_completion_per_1m": None,
                "is_free": False,
                "supports_prompt_cache": False,
            }
        ],
    }
    assert client.post("/api/hosts/nope/refresh-models").status_code == 404
    assert client.get("/api/models/hosts/nope").status_code == 404
    assert client.get("/api/models/hosts/groq").json()["models"][0]["name"] == "qwen2-vl-7b"


def test_image_query_param_asks_for_the_image_variant(client, world, monkeypatch) -> None:
    """``?image=true`` on the host-models endpoints is the Images role's own picker: a completely
    different model list behind the same host id, kept in its own cache slot."""
    calls = []

    def fake(host, force=False, variant=""):
        calls.append((host, force, variant))
        return _snap(host, ["google/gemini-2.5-flash-image"] if variant == "image" else ["deepseek/deepseek-v4-pro"])

    monkeypatch.setattr(model_discovery, "cached_snapshot", fake)

    r = client.get("/api/models/hosts/openrouter")
    assert r.json()["models"][0]["name"] == "deepseek/deepseek-v4-pro"
    r = client.get("/api/models/hosts/openrouter", params={"image": "true"})
    assert r.json()["models"][0]["name"] == "google/gemini-2.5-flash-image"

    r = client.post("/api/hosts/openrouter/refresh-models", params={"image": "true"})
    assert r.json()["models"][0]["name"] == "google/gemini-2.5-flash-image"

    assert calls == [
        ("openrouter", False, ""),
        ("openrouter", False, "image"),
        ("openrouter", True, "image"),
    ]


def test_new_roles_and_host_in_models_state(client) -> None:
    state = client.get("/api/models").json()
    assert set(state["roles"]) == {"chat", "coding", "memory", "vision", "planning", "review", "fallback", "images"}
    assert [h["id"] for h in state["hosts"]] == ["openrouter", "openai", "anthropic", "gemini", "deepseek", "groq", "mistral", "xai"]


def test_retired_settings_are_purged_from_an_existing_file(isolated) -> None:
    path = settings_store.SETTINGS_PATH
    path.write_text(json.dumps({"api_access": {"openrouter_key": "keep-me", "huggingface_key": "hf_SECRET", "huggingface_enabled": True}}), encoding="utf-8")
    assert settings_store.purge_retired_settings() is True
    text = path.read_text(encoding="utf-8")
    assert "huggingface" not in text and "hf_SECRET" not in text
    assert json.loads(text)["api_access"]["openrouter_key"] == "keep-me"
    assert settings_store.purge_retired_settings() is False  # nothing left to purge
