"""api/routes/jev.py: where Jev runs (OpenRouter or TypeSafe), its behaviour, and metrics."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.chat_service import ChatService

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(isolated):
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url=BASE) as c:
        yield c


def test_defaults(client):
    body = client.get("/api/jev/settings").json()
    assert body["enabled"] is False
    assert body["model"] == "typesafe/jev-latest"
    assert body["confidence_threshold"] == 0.92
    assert body["api_key_configured"] is False
    assert body["key_source"] == "none"
    assert "api_key" not in body


def test_riding_on_the_openrouter_key_needs_no_jev_specific_key(client):
    """The normal case: an OpenRouter key is enough, no separate Jev key is ever entered."""
    client.put("/api/keys/openrouter", json={"key": "or-secret-key-1"})
    body = client.get("/api/jev/settings").json()
    assert body["api_key_configured"] is True
    assert body["key_source"] == "openrouter"
    assert "api_key" not in body


def test_typesafe_uses_the_typesafe_key_from_the_key_catalog(client):
    """Jev keeps no key of its own: on TypeSafe it reads the TypeSafe entry set through /api/keys."""
    client.put("/api/keys/openrouter", json={"key": "or-secret-key-1"})
    body = client.put("/api/jev/settings", json={"backend": "typesafe"}).json()
    assert body["backend"] == "typesafe" and body["model"] == "jev-latest"
    assert body["api_key_configured"] is False and body["key_source"] == "none"  # OpenRouter's key does not count
    client.put("/api/keys/typesafe", json={"key": "ts-super-secret"})
    body = client.get("/api/jev/settings").json()
    assert body["api_key_configured"] is True and body["key_source"] == "saved"
    assert "ts-super-secret" not in client.get("/api/jev/settings").text


def test_jev_takes_no_key_and_only_two_backends(client):
    client.put("/api/jev/settings", json={"api_key": "ts-sneaky"})  # ignored: keys go through /api/keys
    assert "ts-sneaky" not in client.get("/api/jev/settings").text
    assert client.put("/api/jev/settings", json={"backend": "local"}).status_code == 422
    assert client.put("/api/jev/settings", json={"backend": "openrouter"}).json()["model"] == "typesafe/jev-latest"


def test_enabling_and_tuning_the_threshold(client):
    body = client.put("/api/jev/settings", json={"enabled": True, "confidence_threshold": 0.75}).json()
    assert body["enabled"] is True and body["confidence_threshold"] == 0.75
    # model untouched
    assert body["model"] == "typesafe/jev-latest"


def test_metrics_endpoint_shape(client):
    body = client.get("/api/jev/metrics").json()
    assert set(body) == {
        "precheck_calls",
        "bypasses",
        "guard_calls",
        "blocked_tool_calls",
        "gate_calls",
        "skipped_calls",
        "interceptions",
        "tokens_saved_estimate",
        "avg_decision_latency_ms",
    }
