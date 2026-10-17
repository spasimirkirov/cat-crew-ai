from jinx_ai.api_access import (
    brave_search_key,
    cloud_host_ready,
    compat_root,
    huggingface_token,
    ready_cloud_hosts,
    resolved_key,
)
from jinx_ai.lm_runtime import parse_openai_models
from jinx_ai.providers.openai_compat import _api_root
from jinx_ai.settings_store import enabled_hosts, resolve_chat_brain


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


def test_huggingface_token_respects_toggle(monkeypatch) -> None:
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "JINX_HF_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    data = {
        "api_access": {
            "huggingface_key": "hf-test",
            "huggingface_enabled": True,
        }
    }
    assert huggingface_token(data) == "hf-test"
    data["api_access"]["huggingface_enabled"] = False
    assert huggingface_token(data) == ""


def test_cloud_host_needs_key_and_toggle(monkeypatch) -> None:
    for name in (
        "DEEPSEEK_API_KEY",
        "JINX_DEEPSEEK_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "JINX_GEMINI_KEY",
        "GROQ_API_KEY",
        "JINX_GROQ_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("jinx_ai.api_access.DEEPSEEK_API_KEY", "")
    data = {
        "brains": {
            "local_enabled": True,
            "chat_host": "local",
        },
        "api_access": {
            "deepseek_key": "sk-test",
            "deepseek_enabled": True,
            "gemini_key": "",
            "gemini_enabled": True,
        },
    }
    assert cloud_host_ready("deepseek", data)
    assert not cloud_host_ready("gemini", data)
    assert ready_cloud_hosts(data) == ["deepseek"]
    assert enabled_hosts(data) == ["local", "deepseek"]
    host, model = resolve_chat_brain({"provider": "deepseek", "model": ""}, data)
    assert host == "deepseek"
    assert model


def test_resolved_key_prefers_settings() -> None:
    data = {"api_access": {"deepseek_key": "from-settings"}}
    assert resolved_key("deepseek", data) == "from-settings"


def test_gemini_openai_root() -> None:
    url = "https://generativelanguage.googleapis.com/v1beta/openai"
    assert compat_root(url) == url
    assert _api_root(url) == url
    assert _api_root("https://api.deepseek.com") == "https://api.deepseek.com/v1"


def test_parse_openai_models_skips_embeddings() -> None:
    models = parse_openai_models(
        {
            "data": [
                {"id": "models/gemini-2.5-flash"},
                {"id": "text-embedding-3-small"},
                {"id": "whisper-large-v3"},
            ]
        },
        allow="gemini",
    )
    assert [item.name for item in models] == ["gemini-2.5-flash"]
    assert "tools" in models[0].capabilities
