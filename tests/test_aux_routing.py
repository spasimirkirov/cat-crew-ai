"""services/aux_routing.py: optional free-tier routing for lightweight background calls."""

from __future__ import annotations

from backend.services import aux_routing, settings_store


def test_untouched_by_default(isolated):
    assert aux_routing.route("openrouter", "deepseek/deepseek-v4-pro") == "deepseek/deepseek-v4-pro"


def test_never_touches_a_local_host(isolated):
    settings_store.save_openrouter_settings({"free_tier_enabled": True})
    assert aux_routing.route("lmstudio", "qwen-chat") == "qwen-chat"


def test_routes_to_the_configured_free_model_when_enabled(isolated):
    settings_store.save_openrouter_settings({"free_tier_enabled": True, "free_tier_model": "google/gemini-2.5-flash:free"})
    assert aux_routing.route("openrouter", "deepseek/deepseek-v4-pro") == "google/gemini-2.5-flash:free"


def test_summarizer_provider_uses_the_free_tier_model(isolated, monkeypatch):
    from backend.services import compaction, model_resolver
    from backend.services.model_resolver import EffectiveModel

    settings_store.save_openrouter_settings({"free_tier_enabled": True, "free_tier_model": "meta-llama/llama-3.3-70b-instruct:free"})
    monkeypatch.setattr(model_resolver, "get_effective_model", lambda *a, **k: EffectiveModel(host="openrouter", model="deepseek/deepseek-v4-pro", source="global"))

    seen = {}

    class _Fake:
        display_name = "OpenRouter"

    def fake_provider_from_brain(host, model):
        seen["host"], seen["model"] = host, model
        return _Fake()

    import backend.core.provider_factory as provider_factory

    monkeypatch.setattr(provider_factory, "provider_from_brain", fake_provider_from_brain)
    result = compaction.summarizer_provider()
    assert result is not None
    assert seen["model"] == "meta-llama/llama-3.3-70b-instruct:free"
