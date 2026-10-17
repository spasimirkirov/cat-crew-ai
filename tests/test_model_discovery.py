"""Live model discovery: what each provider's own list says, and how failures are reported."""

from __future__ import annotations

import pytest
import requests

from backend.services import model_discovery as md
from backend.services.model_discovery import (
    DiscoveryError,
    parse_openai_models,
    parse_openrouter_image_models,
)

KEY = "sk-SUPERSECRET-0123456789"


def _by_name(models):
    return {m.name: m for m in models}


def test_openrouter_uses_the_openai_shaped_list() -> None:
    """OpenRouter (the one cloud host) is fetched with fetch_openai_compat, same as LM Studio's fallback."""
    models = _by_name(
        parse_openai_models(
            {
                "data": [
                    {"id": "deepseek/deepseek-v4-pro", "context_length": 64000},
                    {"id": "anthropic/claude-sonnet-4", "context_length": 200000},
                    {"id": "text-embedding-3-small", "context_length": 8191},
                    {"id": "retired-model", "active": False},
                ]
            }
        )
    )
    assert "retired-model" not in models
    assert models["deepseek/deepseek-v4-pro"].context_length == 64000
    assert models["anthropic/claude-sonnet-4"].context_length == 200000


class _Resp:
    def __init__(self, status: int = 200, payload=None, json_error: bool = False):
        self.status_code = status
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (requests.ConnectionError(f"HTTPConnectionPool(host='x', port=1): {KEY}"), "unreachable"),
        (requests.Timeout(f"slow {KEY}"), "unreachable"),
        (requests.RequestException(f"odd {KEY}"), "error"),
        (_Resp(401), "auth"),
        (_Resp(403), "auth"),
        (_Resp(404), "error"),
        (_Resp(500), "error"),
        (_Resp(200, json_error=True), "error"),
    ],
)
def test_failures_are_classified_and_never_leak(monkeypatch, outcome, kind) -> None:
    def fake_get(url, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(md.requests, "get", fake_get)
    with pytest.raises(DiscoveryError) as caught:
        md._get("https://example.test/models", headers={"Authorization": f"Bearer {KEY}"}, timeout=1)
    assert caught.value.kind == kind
    assert KEY not in caught.value.message and "example.test" not in caught.value.message


def test_openrouter_sends_the_key_in_a_header_not_the_url(monkeypatch) -> None:
    seen = []

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append((url, dict(headers or {}), dict(params or {})))
        return _Resp(200, {"data": [{"id": "deepseek/deepseek-v4-pro", "context_length": 64000}]})

    monkeypatch.setattr(md.requests, "get", fake_get)
    md.fetch_openai_compat("https://openrouter.ai/api/v1", KEY)
    (url, headers, params), = seen
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in url and KEY not in str(params)


def test_openrouter_image_models_are_parsed_with_the_image_capability() -> None:
    """The mirror of the chat-model parser: same /models shape, filtered to image-generation rows."""
    models = _by_name(
        parse_openrouter_image_models(
            {
                "data": [
                    {"id": "google/gemini-2.5-flash-image", "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "black-forest-labs/flux-1.1-pro"},
                    {"id": "retired-image-model", "active": False},
                ]
            }
        )
    )
    assert set(models) == {"google/gemini-2.5-flash-image", "black-forest-labs/flux-1.1-pro"}
    assert "image" in models["google/gemini-2.5-flash-image"].capabilities


def test_fetch_openrouter_image_models_asks_for_the_image_modality_filter(monkeypatch) -> None:
    seen = []

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append((url, dict(headers or {}), dict(params or {})))
        return _Resp(200, {"data": [{"id": "google/gemini-2.5-flash-image"}]})

    monkeypatch.setattr(md.requests, "get", fake_get)
    models = md.fetch_openrouter_image_models("https://openrouter.ai/api/v1", KEY)
    (url, headers, params), = seen
    assert url == "https://openrouter.ai/api/v1/models"
    assert params == {"output_modalities": "image"}
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert [m.name for m in models] == ["google/gemini-2.5-flash-image"]


def test_probe_with_image_variant_requires_openrouter_and_a_key(isolated, monkeypatch) -> None:
    # Any other host: image variant is simply not offered.
    snap = md.probe("groq", variant="image")
    assert (snap.online, snap.error_kind) == (False, "error")

    for spec_env in ("OPENROUTER_API_KEY", "CATCREW_OPENROUTER_KEY"):
        monkeypatch.delenv(spec_env, raising=False)
    snap = md.probe("openrouter", variant="image")
    assert (snap.online, snap.error_kind) == (False, "no_key")

    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setattr(md, "fetch_openrouter_image_models", lambda *a, **k: [md.LmModel(name="google/gemini-2.5-flash-image", capabilities=["image"])])
    snap = md.probe("openrouter", variant="image")
    assert snap.online and [m.name for m in snap.models] == ["google/gemini-2.5-flash-image"]

    # The plain (chat) probe is untouched by the image variant existing.
    monkeypatch.setattr(md, "fetch_openai_compat", lambda *a, **k: [md.LmModel(name="deepseek/deepseek-v4-pro")])
    snap = md.probe("openrouter")
    assert snap.online and [m.name for m in snap.models] == ["deepseek/deepseek-v4-pro"]


def test_cached_snapshot_keys_the_image_variant_separately_from_the_plain_host(isolated, monkeypatch) -> None:
    md.clear_cache()
    calls = []

    def fake_probe(kind, *, variant=""):
        calls.append((kind, variant))
        return md.HostSnapshot(kind=kind, online=True)

    monkeypatch.setattr(md, "probe", fake_probe)
    md.cached_snapshot("openrouter")
    md.cached_snapshot("openrouter", variant="image")
    assert calls == [("openrouter", ""), ("openrouter", "image")]
    # Both are now cached independently: neither call re-probes.
    md.cached_snapshot("openrouter")
    md.cached_snapshot("openrouter", variant="image")
    assert len(calls) == 2
    md.clear_cache()


def test_probe_reports_offline_snapshots_without_raising(isolated, monkeypatch) -> None:
    for spec_env in ("OPENROUTER_API_KEY", "CATCREW_OPENROUTER_KEY"):
        monkeypatch.delenv(spec_env, raising=False)
    snap = md.probe("openrouter")
    assert (snap.online, snap.error_kind) == (False, "no_key")

    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setattr(md, "fetch_openai_compat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"boom {KEY}")))
    snap = md.probe("groq")
    assert (snap.online, snap.error_kind) == (False, "error")
    assert KEY not in snap.error

    monkeypatch.setattr(md, "fetch_openai_compat", lambda *a, **k: parse_openai_models({"data": [{"id": "llama-3.3-70b-versatile"}]}))
    snap = md.probe("groq")
    assert snap.online and snap.loaded_names == ["llama-3.3-70b-versatile"] and snap.checked_at > 0

    # There is no local model host any more: an old LM Studio pick is not a host.
    assert (md.probe("lmstudio").online, md.probe("lmstudio").error_kind) == (False, "error")


def test_cache_holds_until_forced_or_cleared(isolated, monkeypatch) -> None:
    md.clear_cache()
    hits = []

    def fake_probe(kind, *, variant=""):
        hits.append(kind)
        return md.HostSnapshot(kind=kind, online=True)

    monkeypatch.setattr(md, "probe", fake_probe)
    md.cached_snapshot("groq")
    md.cached_snapshot("groq")
    assert len(hits) == 1
    md.cached_snapshot("groq", force=True)
    assert len(hits) == 2
    md.clear_cache("groq")
    md.cached_snapshot("groq")
    assert len(hits) == 3
    md.clear_cache()


def test_every_vendor_host_lists_its_models_with_its_own_headers(isolated, monkeypatch) -> None:
    """OpenAI, Anthropic, Gemini, DeepSeek, Groq, Mistral and xAI: GET /models on each one's own API."""
    from backend.services import api_access

    seen: dict[str, dict] = {}

    def fake_get(url, headers=None, **kwargs):
        seen[url] = dict(headers or {})
        return _Resp(200, {"data": [{"id": "models/gemini-2.5-flash"}, {"id": "claude-sonnet-4-5"}, {"id": "text-embedding-3-small"}]})

    monkeypatch.setattr(md.requests, "get", fake_get)
    for host, env in (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("groq", "GROQ_API_KEY")):
        monkeypatch.setenv(env, KEY)
        snap = md.probe(host)
        assert snap.online, (host, snap.error)
        assert {m.name for m in snap.models} == {"gemini-2.5-flash", "claude-sonnet-4-5"}  # prefix stripped, embeddings dropped
    assert "https://api.openai.com/v1/models" in seen
    assert "https://generativelanguage.googleapis.com/v1beta/openai/models" in seen
    anthropic = seen["https://api.anthropic.com/v1/models"]
    assert anthropic["x-api-key"] == KEY and anthropic["anthropic-version"] == "2023-06-01"
    assert set(api_access.CLOUD_HOSTS) >= {"openrouter", "openai", "anthropic", "gemini", "deepseek", "groq", "mistral", "xai"}
