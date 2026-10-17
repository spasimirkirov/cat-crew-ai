"""Host names, the model note, and how a role resolves to a host + model."""

from __future__ import annotations

import pytest

from backend.services.lm_runtime import format_capabilities, host_label, loaded_model_note, model_names_match, normalize_host
from backend.services.settings_store import (
    chat_pane_for,
    normalize_model_role,
    resolve_role_brain,
    save_role_brain,
    saved_role_brain,
)


def test_cloud_hosts_stay_named() -> None:
    for host in ("openrouter", "openai", "anthropic", "gemini", "deepseek", "groq", "mistral", "xai"):
        assert normalize_host(host) == host
    # There is no local model host: an old LM Studio / Ollama pick is no host at all.
    for alias in ("ollama", "lmstudio", "lm studio", "local", ""):
        assert normalize_host(alias) == ""
    assert normalize_host("comfyui") == "comfyui" and normalize_host("off") == "off"
    assert host_label("openrouter") == "OpenRouter" and host_label("gemini") == "Google Gemini"


def test_format_and_note() -> None:
    assert format_capabilities(["chat", "vision", "reasoning", "tools"]) == "tools · vision · reasoning · chat"
    note = loaded_model_note("groq", "gemma-3-12b-it-Q4_K_M", ["tools"])
    assert "gemma-3-12b-it-Q4_K_M" in note and "via Groq" in note
    assert "function calling" in note


def test_model_names_match_ignores_case_only() -> None:
    assert model_names_match("gemma-3-12b-it-Q4_K_M", "Gemma-3-12b-it-Q4_K_M")
    assert not model_names_match("gemma-3-12b-it-Q4_K_M", "qwen2.5-coder-14b")
    assert not model_names_match("", "")


def test_models_tab_has_no_chat() -> None:
    for mode in ("assistant", "developer"):
        activity, chat_full, show_chat = chat_pane_for(mode, "models")
        assert activity == "models"
        assert not chat_full
        assert not show_chat


def _brains(**over) -> dict:
    base = {"chat_host": "groq", "chat_model": "gemma-3-12b"}
    base.update(over)
    return {"brains": base, "api_access": {"groq_key": "k", "deepseek_key": "k"}}


@pytest.mark.parametrize("role", ["coding", "memory", "vision", "planning"])
def test_optional_roles_inherit_chat_then_override(role: str) -> None:
    data = _brains()
    assert resolve_role_brain(role, data=data) == ("groq", "gemma-3-12b")
    data["brains"][f"{role}_host"] = "deepseek"
    data["brains"][f"{role}_model"] = "special-model"
    assert resolve_role_brain(role, data=data) == ("deepseek", "special-model")
    assert resolve_role_brain("companion", data=data) == ("groq", "gemma-3-12b")


def test_role_aliases() -> None:
    assert normalize_model_role("chat") == "companion"
    assert normalize_model_role("planner") == "planning"
    assert normalize_model_role("vlm") == "vision"
    assert normalize_model_role("summarizer") == "memory"
    assert normalize_model_role("nonsense") == "companion"


def test_explicit_coding_ignores_session_host() -> None:
    data = _brains(coding_host="groq", coding_model="qwen2.5-coder-14b")
    host, model = resolve_role_brain("coding", {"provider": "deepseek", "model": "x"}, data)
    assert (host, model) == ("groq", "qwen2.5-coder-14b")


def test_an_old_lm_studio_pick_reads_as_unset() -> None:
    data = {"brains": {"chat_host": "lmstudio", "chat_model": "qwen", "coding_host": "lmstudio", "coding_model": "q"}}
    assert saved_role_brain("companion", data) == ("", "qwen")
    assert saved_role_brain("coding", data) == ("", "")
    assert resolve_role_brain("coding", data=data) == ("", "qwen")  # no host is set up: nothing to run on


def test_save_role_brain_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.settings_store.SETTINGS_PATH", tmp_path / "settings.json")
    for role in ("coding", "vision", "planning"):
        save_role_brain(role, "openrouter", "anthropic/claude-sonnet-5")
        assert saved_role_brain(role) == ("openrouter", "anthropic/claude-sonnet-5")
        save_role_brain(role, "", "")
        assert saved_role_brain(role) == ("", "")
    save_role_brain("planning", "openrouter", "")  # a host without a model means "not set"
    assert saved_role_brain("planning") == ("", "")
