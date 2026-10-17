from backend.services.logutil import _keep
from backend.services.memory import _is_missing_llm, resolve_memory_llm


def test_memory_llm_follows_local_chat_brain(monkeypatch) -> None:
    monkeypatch.setattr("backend.services.memory.MEMORY_LLM_MODEL", "")
    monkeypatch.setattr(
        "backend.services.settings_store.resolve_role_brain",
        lambda role, session=None, data=None: ("groq", "gemma-3-12b-it-Q4_K_M"),
    )
    assert resolve_memory_llm() == "gemma-3-12b-it-Q4_K_M"


def test_memory_llm_follows_a_cloud_memory_model(monkeypatch) -> None:
    """Every host is a cloud host now: mem0 runs on the Memory role's model wherever it is."""
    monkeypatch.setattr("backend.services.memory.MEMORY_LLM_MODEL", "")
    monkeypatch.setattr(
        "backend.services.settings_store.resolve_role_brain",
        lambda role, session=None, data=None: ("groq", "llama-3.3-70b"),
    )
    assert resolve_memory_llm() == "llama-3.3-70b"


def test_memory_llm_env_override(monkeypatch) -> None:
    monkeypatch.setattr("backend.services.memory.MEMORY_LLM_MODEL", "custom:tag")
    assert resolve_memory_llm() == "custom:tag"


def test_memory_llm_uses_explicit_memory_role(monkeypatch) -> None:
    monkeypatch.setattr("backend.services.memory.MEMORY_LLM_MODEL", "")
    monkeypatch.setattr(
        "backend.services.settings_store.resolve_role_brain",
        lambda role, session=None, data=None: ("groq", "qwen2.5-coder-14b-Q4_K_M"),
    )
    assert resolve_memory_llm() == "qwen2.5-coder-14b-Q4_K_M"


def test_missing_mem0_model_is_quiet() -> None:
    err = RuntimeError(
        "LLM extraction failed: model 'qwen2.5-coder:14b' not found (status code: 404)"
    )
    assert _is_missing_llm(err)
    assert not _keep(str(err))


def test_memory_llm_uses_the_projects_memory_override(isolated, monkeypatch) -> None:
    from backend.platform.runtime import reset_project_id, set_project_id
    from backend.services import model_resolver, settings_store

    monkeypatch.setattr("backend.services.memory.MEMORY_LLM_MODEL", "")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    settings_store.save_role_brain("memory", "groq", "global-mem")
    model_resolver.set_project_overrides("p1", {"memory": {"host": "groq", "model": "project-mem"}})
    assert resolve_memory_llm() == "global-mem"  # no project in scope
    assert resolve_memory_llm("p1") == "project-mem"
    token = set_project_id("p1")
    try:
        assert resolve_memory_llm() == "project-mem"  # the current turn's project
    finally:
        reset_project_id(token)
    assert resolve_memory_llm("p2") == "global-mem"


def test_self_modifier_uses_the_projects_coding_override(isolated, monkeypatch) -> None:
    from backend.platform.runtime import reset_project_id, set_project_id
    from backend.services import model_resolver, self_modifier, settings_store

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-openrouter")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    settings_store.save_role_brain("coding", "groq", "global-coder")
    model_resolver.set_project_overrides("p1", {"coding": {"host": "openrouter", "model": "anthropic/claude-x"}})
    assert self_modifier._coding_brain() == ("groq", "global-coder")
    seen: list[tuple[str, str]] = []

    class _P:
        def chat(self, messages, tools=None):
            from backend.providers.base import AssistantMessage

            return AssistantMessage(content="fixed")

    def fake(host, model):
        seen.append((host, model))
        return _P()

    monkeypatch.setattr("backend.core.provider_factory.provider_from_brain", fake)
    token = set_project_id("p1")
    try:
        assert self_modifier._coding_brain() == ("openrouter", "anthropic/claude-x")
        assert self_modifier._ask_local("fix it") == "fixed"
    finally:
        reset_project_id(token)
    assert seen == [("openrouter", "anthropic/claude-x")]
