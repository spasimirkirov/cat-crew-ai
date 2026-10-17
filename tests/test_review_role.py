"""The Review role: the model QA checks the work with. Unset, it follows Coding (which may follow Chat)."""

from __future__ import annotations

from backend.services import api_access, model_resolver, settings_store


def _keys() -> None:
    api_access.set_api_key("deepseek", "sk-test")
    api_access.set_api_key("anthropic", "sk-ant-test")


def test_unset_review_follows_coding_not_chat(isolated) -> None:
    _keys()
    settings_store.save_role_brain("companion", "deepseek", "deepseek-chat")
    settings_store.save_role_brain("coding", "anthropic", "claude-coder")
    eff = model_resolver.get_effective_model("", "review")
    assert (eff.host, eff.model, eff.source, eff.explicit) == ("anthropic", "claude-coder", "coding", False)
    settings_store.save_role_brain("coding", "", "")  # Coding unset too: both follow Chat
    eff = model_resolver.get_effective_model("", "review")
    assert (eff.host, eff.model, eff.explicit) == ("deepseek", "deepseek-chat", False)


def test_a_set_review_model_is_what_the_qa_phase_runs_on(isolated) -> None:
    from backend.platform.sop import _planning_provider

    _keys()
    settings_store.save_role_brain("companion", "deepseek", "deepseek-chat")
    assert _planning_provider("qa") is None  # unset: QA stays on the default (Coding) model
    settings_store.save_role_brain("review", "anthropic", "claude-reviewer")
    provider = _planning_provider("qa")
    assert provider is not None and provider.model == "claude-reviewer"
    assert _planning_provider("build") is None  # only QA uses Review


def test_the_models_api_saves_and_reports_review(isolated) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.core.chat_service import ChatService

    _keys()
    with TestClient(create_app(chat_service=ChatService(use_memory=False)), base_url="http://127.0.0.1:8765") as client:
        body = client.put("/api/models/roles", json={"review": {"host": "anthropic", "model": "claude-reviewer"}}).json()
        assert body["roles"]["review"] == {"host": "anthropic", "model": "claude-reviewer"}
        project_id = client.post("/api/projects", json={"name": "Site"}).json()["id"]
        state = client.get(f"/api/projects/{project_id}/models").json()
        assert state["effective"]["review"]["model"] == "claude-reviewer"


def test_an_old_session_does_not_keep_its_old_model(isolated) -> None:
    """Remonti's chats stayed on openai/gpt-oss-20b after the Models page moved to deepseek-v4-pro."""
    from backend.services.settings_store import resolve_chat_brain

    _keys()
    settings_store.save_role_brain("companion", "deepseek", "deepseek-v4-pro")
    stale = {"provider": "", "model": "openai/gpt-oss-20b"}
    assert resolve_chat_brain(stale) == ("deepseek", "deepseek-v4-pro")
    assert model_resolver.get_effective_model("", "coding", stale).model == "deepseek-v4-pro"
    pinned = {"provider": "anthropic", "model": "claude-x", "model_pinned": True}
    assert resolve_chat_brain(pinned) == ("anthropic", "claude-x")
