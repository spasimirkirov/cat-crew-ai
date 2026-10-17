"""Capability tags come from what a host reports, then from the model's name."""

from __future__ import annotations

import pytest

from backend.services.model_caps import classify, role_fit_warning


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("gpt-4o", ["vision", "tools", "chat"]),
        ("gemini-2.5-flash", ["vision", "tools", "chat"]),
        ("gemini-1.5-pro", ["vision", "tools", "chat"]),
        ("claude-3-5-sonnet-latest", ["vision", "tools", "chat"]),
        ("qwen2-vl-7b-instruct", ["vision", "tools", "chat"]),
        ("llava-v1.6-mistral-7b", ["vision", "chat"]),
        ("deepseek-chat", ["tools", "chat"]),
        ("deepseek-reasoner", ["reasoning", "tools", "chat"]),
        ("deepseek-r1-distill-llama-70b", ["reasoning", "chat"]),
        ("o3-mini", ["reasoning", "tools", "chat"]),
        ("qwen3-30b-a3b-thinking-2507", ["reasoning", "tools", "chat"]),
        ("llama-3.3-70b-versatile", ["tools", "chat"]),
        ("mistral-7b-instruct", ["tools", "chat"]),
    ],
)
def test_names_are_classified(name: str, expected: list[str]) -> None:
    assert classify(name) == expected


def test_r1_must_be_a_whole_word() -> None:
    assert "reasoning" not in classify("gpt-4r1x")
    assert "reasoning" in classify("some-model-r1")


@pytest.mark.parametrize("name", ["text-embedding-3-small", "nomic-embed-text-v1.5", "bge-m3"])
def test_embedding_models_are_not_chat_models(name: str) -> None:
    assert classify(name) == ["embed"]


@pytest.mark.parametrize("name", ["flux-schnell", "sdxl-turbo", "gemini-2.5-flash-image", "dall-e-3"])
def test_image_models_are_not_chat_models(name: str) -> None:
    assert classify(name) == ["image"]


@pytest.mark.parametrize("name", ["whisper-large-v3", "gemini-2.5-flash-preview-tts", "llama-guard-3-8b"])
def test_speech_and_safety_models_are_skipped(name: str) -> None:
    assert classify(name) == []


def test_host_metadata_beats_the_name() -> None:
    # LM Studio says a "gemma3" build has no vision projector, and that another model has no tool use.
    assert "vision" not in classify("gemma-3-12b-it", {"vision": False})
    assert "vision" in classify("plain-name-model", {"vision": True})
    assert "tools" not in classify("mistral-7b-instruct", {"tools": False})
    assert classify("anything", {"kind": "embeddings"}) == ["embed"]


def test_role_fit_warnings() -> None:
    assert role_fit_warning("vision", ["tools", "chat"])
    assert not role_fit_warning("vision", ["vision", "chat"])
    assert role_fit_warning("planning", ["tools", "chat"])
    assert not role_fit_warning("planning", ["reasoning"])
    assert role_fit_warning("coding", ["chat"])
    assert role_fit_warning("chat", []) == ""
    assert role_fit_warning("memory", []) == ""
