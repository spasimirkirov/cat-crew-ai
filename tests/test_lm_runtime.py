from jinx_ai.lm_runtime import (
    format_capabilities,
    infer_capabilities,
    loaded_model_note,
    normalize_capabilities,
    normalize_host,
    parse_pull_progress,
)
from jinx_ai.settings_store import (
    chat_pane_for,
    enabled_hosts,
    list_role_models,
    resolve_chat_brain,
    resolve_role_brain,
    save_role_brain,
    saved_role_brain,
)


def test_cloud_hosts_stay_named() -> None:
    assert normalize_host("deepseek") == "deepseek"
    assert normalize_host("gemini") == "gemini"
    assert normalize_host("groq") == "groq"
    assert normalize_host("openrouter") == "local"
    assert normalize_host("openai") == "local"
    assert normalize_host("lmstudio") == "local"
    assert normalize_host("ollama") == "local"


def test_format_and_note() -> None:
    assert format_capabilities(["vision", "tools", "chat"]) == "tools · vision · chat"
    note = loaded_model_note("local", "gemma-3-12b-it-Q4_K_M", ["tools"])
    assert "gemma-3-12b-it-Q4_K_M" in note
    assert "function calling" in note
    assert "Image generation" in note


def test_enabled_hosts_and_resolve(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.api_access.DEEPSEEK_API_KEY", "")
    monkeypatch.setattr("jinx_ai.api_access.BRAVE_SEARCH_API_KEY", "")
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
    data = {
        "brains": {
            "local_enabled": True,
            "chat_host": "ollama",
            "chat_model": "ignored",
        }
    }
    assert enabled_hosts(data) == ["local"]
    host, model = resolve_chat_brain(
        {"provider": "deepseek", "model": "gemma-3-12b-it-Q4_K_M"},
        data,
    )
    assert host == "local"
    assert model == "gemma-3-12b-it-Q4_K_M"


def test_models_tab_has_no_chat() -> None:
    for mode in ("assistant", "developer"):
        activity, chat_full, show_chat = chat_pane_for(mode, "models")
        assert activity == "models"
        assert not chat_full
        assert not show_chat


def test_download_queue_and_model_name_match() -> None:
    from jinx_ai.lm_runtime import (
        HostSnapshot,
        dequeue_download,
        enqueue_download,
        inject_pulled_model,
        model_names_match,
        snapshot_has_model,
    )

    assert model_names_match("gemma-3-12b-it-Q4_K_M", "Gemma-3-12b-it-Q4_K_M")
    assert model_names_match(
        "bartowski/gemma#gemma-3-12b-it-Q4_K_M.gguf",
        "gemma-3-12b-it-Q4_K_M",
    )
    assert model_names_match(
        "bartowski/gemma#gemma-3-12b-it-Q4_K_M.gguf",
        "gemma-3-12b-it-Q4_K_M.gguf.part",
    )
    assert not model_names_match("gemma-3-12b-it-Q4_K_M", "qwen2.5-coder-14b")

    status, queued = enqueue_download("", [], "repo#a.gguf")
    assert status == "start"
    status, queued = enqueue_download("repo#a.gguf", [], "repo#b.gguf")
    assert status == "queued"
    assert queued == ["repo#b.gguf"]
    status, queued = enqueue_download("repo#a.gguf", ["repo#b.gguf"], "repo#b.gguf")
    assert status == "duplicate"
    assert dequeue_download(["repo#b.gguf", "repo#c.gguf"], "repo#b.gguf") == ["repo#c.gguf"]

    snap = HostSnapshot(kind="local")
    assert inject_pulled_model(snap, "repo#ministral-Q4_K_M.gguf")
    assert snapshot_has_model(snap, "ministral-Q4_K_M")
    assert not inject_pulled_model(snap, "ministral-Q4_K_M")
    assert snap.models[0].name == "ministral-Q4_K_M"


def test_pull_progress_has_percent_and_indeterminate() -> None:
    busy = parse_pull_progress({"status": "pulling manifest"})
    assert busy["indeterminate"] is True
    assert busy["percent"] == 0
    chunk = parse_pull_progress(
        {"status": "downloading", "completed": 50, "total": 200}
    )
    assert chunk["percent"] == 25
    assert chunk["indeterminate"] is False
    done = parse_pull_progress({"status": "success", "percent": 100})
    assert done["percent"] == 100


def test_delete_model_needs_a_name() -> None:
    from jinx_ai.lm_runtime import delete_model

    try:
        delete_model("local", "  ")
    except ValueError as exc:
        assert "name" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")


def test_normalize_capabilities_dict() -> None:
    assert normalize_capabilities({"trained_for_tool_use": True, "vision": True}) == [
        "tools",
        "vision",
    ]
    assert infer_capabilities("flux-schnell") == ["image"]


def _clear_cloud_env(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.api_access.DEEPSEEK_API_KEY", "")
    monkeypatch.setattr("jinx_ai.api_access.BRAVE_SEARCH_API_KEY", "")
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


def test_coding_role_inherits_then_overrides() -> None:
    data = {
        "brains": {
            "local_enabled": True,
            "chat_host": "ollama",
            "chat_model": "gemma-3-12b-it-Q4_K_M",
            "coding_host": "",
            "coding_model": "",
        }
    }
    assert resolve_role_brain("coding", data=data) == ("local", "gemma-3-12b-it-Q4_K_M")
    data["brains"]["coding_host"] = "local"
    data["brains"]["coding_model"] = "qwen2.5-coder-14b-Q4_K_M"
    assert resolve_role_brain("coding", data=data) == ("local", "qwen2.5-coder-14b-Q4_K_M")
    assert resolve_role_brain("companion", data=data) == ("local", "gemma-3-12b-it-Q4_K_M")
    assert resolve_role_brain("memory", data=data) == ("local", "gemma-3-12b-it-Q4_K_M")


def test_explicit_coding_ignores_session_host() -> None:
    data = {
        "brains": {
            "local_enabled": True,
            "chat_host": "local",
            "chat_model": "gemma-3-12b-it-Q4_K_M",
            "coding_host": "local",
            "coding_model": "qwen2.5-coder-14b-Q4_K_M",
        }
    }
    host, model = resolve_role_brain(
        "coding",
        {"provider": "ollama", "model": "gemma-3-12b-it-Q4_K_M"},
        data,
    )
    assert host == "local"
    assert model == "qwen2.5-coder-14b-Q4_K_M"


def test_save_role_brain_inherit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.settings_store.SETTINGS_PATH", tmp_path / "settings.json")
    save_role_brain("coding", "ollama", "qwen2.5-coder-14b-Q4_K_M")
    assert saved_role_brain("coding") == ("local", "qwen2.5-coder-14b-Q4_K_M")
    save_role_brain("coding", "", "")
    assert saved_role_brain("coding") == ("", "")


def test_list_role_models_local_and_cloud_fallback(monkeypatch) -> None:
    _clear_cloud_env(monkeypatch)
    data = {
        "brains": {
            "local_enabled": True,
            "chat_host": "local",
            "chat_model": "gemma-3-12b-it-Q4_K_M",
        },
        "api_access": {
            "deepseek_key": "sk-test",
            "deepseek_enabled": True,
            "gemini_key": "",
            "gemini_enabled": False,
            "groq_key": "",
            "groq_enabled": False,
        },
    }
    rows = list_role_models(
        {"local": ["gemma-3-12b-it-Q4_K_M", "qwen2.5-coder-14b-Q4_K_M"], "deepseek": []},
        "companion",
        data,
    )
    assert ("local", "gemma-3-12b-it-Q4_K_M") in rows
    assert ("local", "qwen2.5-coder-14b-Q4_K_M") in rows
    assert any(host == "deepseek" for host, _name in rows)
    assert list_role_models({"local": ["gemma-3-12b-it-Q4_K_M"]}, "memory", data) == [
        ("local", "gemma-3-12b-it-Q4_K_M")
    ]
    images = list_role_models({"comfyui": ["sdxl.safetensors"]}, "image", data)
    assert images == [("comfyui", "sdxl.safetensors")]
