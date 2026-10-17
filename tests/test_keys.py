"""API keys are write-only: they go in through PUT /api/keys/{id} and never come back out."""

from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.chat_service import ChatService
from backend.services import api_access, settings_store

BASE = "http://127.0.0.1:8765"
SECRET = "sk-TOPSECRET-abcdef0123456789"
IDS = ["openrouter", "openai", "anthropic", "gemini", "deepseek", "groq", "mistral", "xai", "typesafe", "brave"]


@pytest.fixture
def client(isolated, monkeypatch):
    # a developer machine may have real keys in its environment: the tests must not see them
    for spec in api_access.CATALOG:
        for name in spec.env_names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api_access, "BRAVE_SEARCH_API_KEY", "")
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url=BASE) as c:
        yield c


def _by_id(rows):
    return {r["id"]: r for r in rows}


def test_lists_every_key_with_no_secret_fields(client):
    rows = client.get("/api/keys").json()
    assert [r["id"] for r in rows] == IDS
    for r in rows:
        assert set(r) == {"id", "title", "subtitle", "group", "configured", "source", "signup"}
        assert r["configured"] is False and r["source"] == "none"
    groups = {r["id"]: r["group"] for r in rows}
    assert all(groups[h] == "host" for h in ("openrouter", "openai", "anthropic", "gemini", "deepseek", "groq", "mistral", "xai"))
    assert groups["brave"] == groups["typesafe"] == "service"
    assert "huggingface" not in groups and "lmstudio" not in groups


def test_saving_a_key_enables_the_host_and_never_echoes_it(client):
    before = _by_id(client.get("/api/models").json()["hosts"])["openrouter"]
    assert before["configured"] is False and before["key_source"] == "none" and before["enabled"] is False

    r = client.put("/api/keys/openrouter", json={"key": f"  {SECRET}  "})  # surrounding blanks are trimmed
    assert r.status_code == 200
    assert r.json()["configured"] is True and r.json()["source"] == "saved"
    assert SECRET not in r.text

    for path in ("/api/keys", "/api/models", "/api/bootstrap"):
        assert SECRET not in client.get(path).text, path
    after = _by_id(client.get("/api/models").json()["hosts"])["openrouter"]
    assert after["configured"] is True and after["key_source"] == "saved" and after["enabled"] is True

    # it is used by the engine, and persisted in the local settings file only
    assert api_access.resolved_key("openrouter") == SECRET
    assert settings_store.load_settings()["api_access"]["openrouter_key"] == SECRET


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_settings_file_is_owner_only(client):
    client.put("/api/keys/openrouter", json={"key": SECRET})
    mode = stat.S_IMODE(settings_store.SETTINGS_PATH.stat().st_mode)
    assert mode & 0o077 == 0


def test_removing_a_key_forgets_it(client):
    client.put("/api/keys/openrouter", json={"key": SECRET})
    r = client.delete("/api/keys/openrouter")
    assert r.status_code == 200 and r.json()["configured"] is False and r.json()["source"] == "none"
    assert api_access.resolved_key("openrouter") == ""
    assert _by_id(client.get("/api/models").json()["hosts"])["openrouter"]["configured"] is False
    assert client.delete("/api/keys/openrouter").status_code == 200  # idempotent


def test_environment_key_is_reported_as_such_and_survives_remove(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    row = _by_id(client.get("/api/keys").json())["openrouter"]
    assert row["configured"] is True and row["source"] == "environment"
    assert SECRET not in client.get("/api/keys").text

    client.put("/api/keys/openrouter", json={"key": "saved-key-1"})
    assert _by_id(client.get("/api/keys").json())["openrouter"]["source"] == "saved"
    assert api_access.resolved_key("openrouter") == "saved-key-1"  # a saved key wins over the environment
    row = client.delete("/api/keys/openrouter").json()
    assert row["configured"] is True and row["source"] == "environment"


def test_vendor_hosts_and_services_keep_their_keys_in_one_place(client):
    """Every key -- a vendor host, Brave, TypeSafe (Jev) -- goes through /api/keys into one catalog."""
    r = client.put("/api/keys/anthropic", json={"key": SECRET})
    assert r.status_code == 200 and r.json()["configured"] is True and SECRET not in r.text
    assert api_access.resolved_key("anthropic") == SECRET
    host = _by_id(client.get("/api/models").json()["hosts"])["anthropic"]
    assert host["configured"] is True and host["key_source"] == "saved" and host["enabled"] is True
    assert client.delete("/api/keys/anthropic").json()["configured"] is False

    assert client.put("/api/keys/typesafe", json={"key": "ts-key-1"}).json()["configured"] is True
    assert api_access.resolved_key("typesafe") == "ts-key-1"
    assert "typesafe" not in _by_id(client.get("/api/models").json()["hosts"])  # a service, not a model host
    assert client.put("/api/keys/lmstudio", json={"key": "x"}).status_code == 404  # there is no local host

    assert client.put("/api/keys/brave", json={"key": "brave-key-1"}).json()["configured"] is True
    assert api_access.brave_search_key() == "brave-key-1"
    assert client.put("/api/keys/huggingface", json={"key": "hf_abc123"}).status_code == 404  # retired


@pytest.mark.parametrize(
    "payload",
    [
        {"key": ""},
        {"key": "   "},
        {"key": "Bearer SECRETPART-xyz"},
        {"key": "line1\nSECRETPART-xyz"},
        {"key": "x" * 600},
        {"key": 12345},
        {"key": ["SECRETPART-xyz"]},
        {},
    ],
)
def test_bad_keys_are_rejected_and_never_echoed(client, payload):
    r = client.put("/api/keys/openrouter", json=payload)
    assert r.status_code == 422
    assert "SECRETPART" not in r.text and "x" * 50 not in r.text
    assert _by_id(client.get("/api/keys").json())["openrouter"]["configured"] is False


def test_malformed_bodies_and_unknown_ids(client):
    assert client.put("/api/keys/openrouter", content="not json", headers={"content-type": "application/json"}).status_code == 422
    r = client.put("/api/keys/openrouter", json=["SECRETPART-xyz"])
    assert r.status_code == 422 and "SECRETPART" not in r.text
    assert client.put("/api/keys/bogus", json={"key": SECRET}).status_code == 404
    assert client.delete("/api/keys/bogus").status_code == 404
    assert client.put("/api/keys/chief", json={"key": SECRET}).status_code == 404  # the orchestrator has no key


def test_a_missing_key_message_points_at_the_page_that_exists(client):
    with pytest.raises(RuntimeError, match=r"Models → Hosts"):
        api_access.make_cloud_provider("openrouter")


def test_keys_endpoints_are_local_only(client):
    r = client.put("/api/keys/openrouter", json={"key": SECRET}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert api_access.resolved_key("openrouter") == ""
