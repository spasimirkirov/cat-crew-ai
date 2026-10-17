from backend.services.api_access import brave_search_key, cloud_host_ready, compat_root, ready_cloud_hosts, resolved_key
from backend.services.model_discovery import parse_openai_models
from backend.providers.openai_compat import _api_root
from backend.services.settings_store import enabled_hosts, resolve_chat_brain


def test_brave_key_respects_toggle() -> None:
    data = {
        "api_access": {
            "brave_key": "bs-test",
            "brave_enabled": True,
        }
    }
    assert brave_search_key(data) == "bs-test"
    data["api_access"]["brave_enabled"] = False
    assert brave_search_key(data) == ""


def test_cloud_host_needs_key_and_toggle(monkeypatch) -> None:
    for name in ("OPENROUTER_API_KEY", "CATCREW_OPENROUTER_KEY"):
        monkeypatch.delenv(name, raising=False)
    data = {
        "brains": {
            "chat_host": "openrouter",
        },
        "api_access": {
            "openrouter_key": "sk-test",
            "openrouter_enabled": True,
        },
    }
    assert cloud_host_ready("openrouter", data)
    data["api_access"]["openrouter_enabled"] = False
    assert not cloud_host_ready("openrouter", data)
    data["api_access"]["openrouter_enabled"] = True
    assert ready_cloud_hosts(data) == ["openrouter"]
    assert enabled_hosts(data) == ["openrouter"]  # cloud hosts with a key; there is no local host
    assert enabled_hosts({"api_access": {}}) == []
    host, model = resolve_chat_brain({"provider": "openrouter", "model": ""}, data)
    assert host == "openrouter"
    assert model


def test_resolved_key_prefers_settings() -> None:
    data = {"api_access": {"openrouter_key": "from-settings"}}
    assert resolved_key("openrouter", data) == "from-settings"


def test_gemini_openai_root() -> None:
    url = "https://generativelanguage.googleapis.com/v1beta/openai"
    assert compat_root(url) == url
    assert _api_root(url) == url
    assert _api_root("https://api.deepseek.com") == "https://api.deepseek.com/v1"


def test_parse_openai_models_keeps_only_active_chat_models() -> None:
    models = parse_openai_models(
        {
            "data": [
                {"id": "models/gemini-2.5-flash"},
                {"id": "text-embedding-3-small"},
                {"id": "whisper-large-v3"},
                {"id": "retired-model", "active": False},
                {"id": "llama-3.3-70b-versatile", "active": True, "context_window": 131072},
            ]
        }
    )
    assert [item.name for item in models] == ["gemini-2.5-flash", "llama-3.3-70b-versatile"]
    assert "tools" in models[0].capabilities
    assert models[1].context_length == 131072


def test_parse_openai_models_reads_openrouter_pricing() -> None:
    models = parse_openai_models(
        {
            "data": [
                {
                    "id": "deepseek/deepseek-v4-pro",
                    "context_length": 128_000,
                    "pricing": {"prompt": "0.0000009", "completion": "0.0000027", "input_cache_read": "0.00000009"},
                },
                {"id": "meta-llama/llama-3.3-70b-instruct:free", "context_length": 8_000, "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "llama-3.3-70b-versatile", "active": True, "context_window": 131072},  # no pricing (not OpenRouter)
            ]
        }
    )
    paid, free, unpriced = models
    assert paid.price_prompt_per_1m == 0.9 and paid.price_completion_per_1m == 2.7
    assert paid.is_free is False and paid.supports_prompt_cache is True
    assert free.is_free is True and free.price_prompt_per_1m == 0.0
    assert unpriced.price_prompt_per_1m is None and unpriced.is_free is False and unpriced.supports_prompt_cache is False
