"""The model hosts (OpenRouter and the vendors' own APIs), their error messages, and Images: Off."""

from __future__ import annotations

import pytest

from backend.services import api_access, settings_store


def test_a_vendor_host_gets_its_own_provider(isolated) -> None:
    api_access.set_api_key("anthropic", "sk-ant-test")
    provider = api_access.make_cloud_provider("anthropic", "claude-sonnet-4-5")
    assert provider.api_root == "https://api.anthropic.com/v1" and provider.model == "claude-sonnet-4-5"
    assert provider.host_id == "anthropic" and provider.display_name == "Anthropic"
    headers = provider._headers_for_call()
    assert headers["Authorization"] == "Bearer sk-ant-test" and headers["anthropic-version"] == "2023-06-01"


def test_a_vendor_host_needs_a_model_picked(isolated) -> None:
    api_access.set_api_key("groq", "gsk-test")
    with pytest.raises(RuntimeError, match="No Groq model is picked"):
        api_access.make_cloud_provider("groq", "")


def test_no_host_at_all_says_what_to_do(isolated) -> None:
    from backend.core.provider_factory import provider_from_brain

    with pytest.raises(RuntimeError, match="Add a key under Models"):
        provider_from_brain("", "qwen")
    with pytest.raises(RuntimeError):
        provider_from_brain("lmstudio", "qwen")  # retired: not a host


def test_a_429_on_a_free_model_explains_the_free_limits_not_the_key(isolated, monkeypatch) -> None:
    from backend.providers.openai_compat import OpenRouterProvider

    class _TooMany:
        status_code = 429
        encoding = "utf-8"
        headers: dict = {}
        content = (
            b'{"error": {"message": "Provider returned error", "code": 429, '
            b'"metadata": {"raw": "google/gemma is temporarily rate-limited upstream", "provider_name": "Google AI Studio"}}}'
        )

        def json(self):
            import json

            return json.loads(self.content)

        def close(self):
            pass

    monkeypatch.setattr("backend.providers.openai_compat.requests.post", lambda *a, **k: _TooMany())
    monkeypatch.setattr("backend.providers.openai_compat._sleep_unless", lambda abort, seconds: False)
    provider = OpenRouterProvider(api_key="sk-or-test", model="google/gemma-4-26b-a4b-it:free")
    with pytest.raises(RuntimeError) as caught:
        provider.stream_chat([{"role": "user", "content": "hi"}])
    text = str(caught.value)
    assert "temporarily rate-limited upstream" in text and "Google AI Studio" in text  # the host's own reason
    assert "free model" in text and "Check the OpenRouter key" not in text


def test_images_can_be_switched_off(isolated) -> None:
    from backend.tools import execute_tool

    settings_store.save_role_brain("image", "off", "anything")
    assert settings_store.saved_role_brain("image") == ("off", "")
    out = execute_tool("generate_image", {"prompt": "a cat"})
    assert out.startswith("Image generation is turned off")
    settings_store.save_role_brain("image", "comfyui", "")
    assert settings_store.saved_role_brain("image")[0] == "comfyui"


def test_the_models_api_accepts_images_off(isolated) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.core.chat_service import ChatService

    with TestClient(create_app(chat_service=ChatService(use_memory=False)), base_url="http://127.0.0.1:8765") as client:
        body = client.put("/api/models/roles", json={"images": {"host": "off", "model": ""}}).json()
        assert body["roles"]["images"] == {"host": "off", "model": ""}
        assert client.put("/api/models/roles", json={"coding": {"host": "lmstudio", "model": "m"}}).status_code == 422


def test_vision_can_be_switched_off_for_a_chat_model_that_cannot_see(isolated) -> None:
    from backend.services import model_resolver
    from backend.tool_functions import qa_tools

    api_access.set_api_key("deepseek", "sk-test")
    settings_store.save_role_brain("companion", "deepseek", "deepseek-chat")
    settings_store.save_role_brain("vision", "off", "anything")
    assert settings_store.saved_role_brain("vision") == ("off", "")
    eff = model_resolver.get_effective_model("", "vision")
    assert eff.host == "off" and eff.model == ""  # off, not "follows Chat"
    assert qa_tools.browser_look("is it readable?").startswith("Vision is turned off")
    settings_store.save_role_brain("vision", "", "")  # back to "Not set": follows Chat again
    assert model_resolver.get_effective_model("", "vision").host == "deepseek"


def test_the_models_api_accepts_vision_off_globally_and_per_project(isolated) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.core.chat_service import ChatService
    with TestClient(create_app(chat_service=ChatService(use_memory=False)), base_url="http://127.0.0.1:8765") as client:
        project_id = client.post("/api/projects", json={"name": "Site"}).json()["id"]
        body = client.put("/api/models/roles", json={"vision": {"host": "off", "model": ""}}).json()
        assert body["roles"]["vision"] == {"host": "off", "model": ""}
        client.put("/api/models/roles", json={"vision": {"host": "", "model": ""}})
        body = client.put(f"/api/projects/{project_id}/models", json={"roles": {"vision": {"host": "off", "model": ""}}}).json()
        assert body["overrides"]["vision"] == {"host": "off", "model": ""}
        assert body["effective"]["vision"]["host"] == "off"


def test_capabilities_come_from_what_openrouter_reports_not_the_name() -> None:
    """deepseek-v4-pro is a reasoning model, but nothing in its name says so."""
    from backend.services.model_discovery import parse_openai_models

    payload = {"data": [
        {"id": "deepseek/deepseek-v4-pro", "supported_parameters": ["tools", "reasoning", "include_reasoning"],
         "architecture": {"input_modalities": ["text"]}},
        {"id": "some/vision-free-text-model", "supported_parameters": ["temperature"],
         "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "vendor/plain-model"},  # says nothing: guessed from the name, as before
    ]}
    caps = {m.name: m.capabilities for m in parse_openai_models(payload)}
    assert "reasoning" in caps["deepseek/deepseek-v4-pro"] and "tools" in caps["deepseek/deepseek-v4-pro"]
    assert "vision" not in caps["deepseek/deepseek-v4-pro"]
    assert "vision" in caps["some/vision-free-text-model"] and "tools" not in caps["some/vision-free-text-model"]
    assert caps["vendor/plain-model"] == ["tools", "chat"]
