"""api/routes/openrouter.py + services/api_access._openrouter_routing_body: provider routing and
the opt-in context-compression plugin."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.chat_service import ChatService
from backend.services import api_access, settings_store

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(isolated):
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url=BASE) as c:
        yield c


def test_defaults(client):
    body = client.get("/api/openrouter/settings").json()
    assert body["provider_sort"] == ""
    assert body["allow_fallbacks"] is True
    assert body["compression_enabled"] is False
    assert body["free_tier_enabled"] is False
    assert body["free_tier_model"] == "meta-llama/llama-3.3-70b-instruct:free"


def test_setting_a_sort_and_disabling_fallbacks(client):
    body = client.put("/api/openrouter/settings", json={"provider_sort": "throughput", "allow_fallbacks": False}).json()
    assert body["provider_sort"] == "throughput" and body["allow_fallbacks"] is False


def test_an_unknown_sort_is_rejected(client):
    resp = client.put("/api/openrouter/settings", json={"provider_sort": "cheapest"})
    assert resp.status_code == 422


def test_empty_string_sort_clears_it_back_to_the_default(client):
    client.put("/api/openrouter/settings", json={"provider_sort": "price"})
    body = client.put("/api/openrouter/settings", json={"provider_sort": ""}).json()
    assert body["provider_sort"] == ""


def test_enabling_compression(client):
    body = client.put("/api/openrouter/settings", json={"compression_enabled": True}).json()
    assert body["compression_enabled"] is True


def test_free_tier_model_cannot_be_blanked(client):
    resp = client.put("/api/openrouter/settings", json={"free_tier_model": "   "})
    assert resp.status_code == 422


# -- the request body OpenRouter actually receives -------------------------------------------------


def test_routing_body_is_a_default_no_op_when_untouched(isolated):
    body = api_access._openrouter_routing_body()
    assert body == {"provider": {"allow_fallbacks": True}}
    assert "plugins" not in body


def test_routing_body_reflects_saved_settings(isolated):
    settings_store.save_openrouter_settings({"provider_sort": "price", "allow_fallbacks": False, "compression_enabled": True})
    body = api_access._openrouter_routing_body()
    assert body["provider"] == {"sort": "price", "allow_fallbacks": False}
    assert body["plugins"] == [{"id": "context-compression"}]


def test_make_cloud_provider_carries_the_routing_body_into_extra_body(isolated, monkeypatch):
    monkeypatch.setattr(api_access, "resolved_key", lambda kind, data=None: "sk-test")
    settings_store.save_openrouter_settings({"provider_sort": "latency"})
    provider = api_access.make_cloud_provider("openrouter", "deepseek/deepseek-v4-pro")
    assert provider.extra_body["provider"] == {"sort": "latency", "allow_fallbacks": True}
