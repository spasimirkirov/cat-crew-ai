from jinx_ai.logutil import _keep
from jinx_ai.memory import _is_missing_llm, resolve_memory_llm


def test_memory_llm_follows_local_chat_brain(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.memory.MEMORY_LLM_MODEL", "")
    monkeypatch.setattr(
        "jinx_ai.settings_store.resolve_role_brain",
        lambda role, session=None, data=None: ("local", "gemma-3-12b-it-Q4_K_M"),
    )
    assert resolve_memory_llm() == "gemma-3-12b-it-Q4_K_M"


def test_memory_llm_skips_when_chat_is_cloud(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.memory.MEMORY_LLM_MODEL", "")
    monkeypatch.setattr(
        "jinx_ai.settings_store.resolve_role_brain",
        lambda role, session=None, data=None: ("groq", "llama-3.3-70b"),
    )
    assert resolve_memory_llm() == ""


def test_memory_llm_env_override(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.memory.MEMORY_LLM_MODEL", "custom:tag")
    assert resolve_memory_llm() == "custom:tag"


def test_memory_llm_uses_explicit_memory_role(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.memory.MEMORY_LLM_MODEL", "")
    monkeypatch.setattr(
        "jinx_ai.settings_store.resolve_role_brain",
        lambda role, session=None, data=None: ("local", "qwen2.5-coder-14b-Q4_K_M"),
    )
    assert resolve_memory_llm() == "qwen2.5-coder-14b-Q4_K_M"


def test_missing_mem0_model_is_quiet() -> None:
    err = RuntimeError(
        "LLM extraction failed: model 'qwen2.5-coder:14b' not found (status code: 404)"
    )
    assert _is_missing_llm(err)
    assert not _keep(str(err))
