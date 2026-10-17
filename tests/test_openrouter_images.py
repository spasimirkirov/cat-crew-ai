"""backend/services/openrouter_images.py: image generation over OpenRouter's Unified Image API --
the alternative to local ComfyUI for the Images role. Rides on the same OpenRouter key already
configured for chat, hits a dedicated ``/images`` endpoint, and always gets base64 back."""

from __future__ import annotations

import base64

import pytest
import requests

from backend.services import openrouter_images as oi


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


_PNG_B64 = base64.b64encode(b"not-really-a-png").decode()


def _set_key(monkeypatch, key="or-secret"):
    monkeypatch.setattr("backend.services.api_access.resolved_key", lambda kind, data=None: key if kind == "openrouter" else "")


def test_empty_prompt_raises_value_error(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    with pytest.raises(ValueError):
        oi.generate_image("   ", "google/gemini-2.5-flash-image")


def test_no_model_raises_runtime_error(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)
    with pytest.raises(RuntimeError, match="model"):
        oi.generate_image("a cat", "")


def test_no_key_raises_runtime_error_without_calling_the_network(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    monkeypatch.setattr("backend.services.api_access.resolved_key", lambda kind, data=None: "")

    def fail_post(*a, **k):
        raise AssertionError("must not hit the network with no key")

    monkeypatch.setattr(oi.requests, "post", fail_post)
    with pytest.raises(RuntimeError, match="key"):
        oi.generate_image("a cat", "google/gemini-2.5-flash-image")


def test_request_shape_matches_the_documented_images_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp({"data": [{"b64_json": _PNG_B64, "media_type": "image/png"}]})

    monkeypatch.setattr(oi.requests, "post", fake_post)
    out = oi.generate_image(
        "a cyan orb",
        "google/gemini-2.5-flash-image",
        negative="blurry",
        width=512,
        height=512,
        seed=7,
    )
    assert captured["url"] == "https://openrouter.ai/api/v1/images"
    assert captured["headers"]["Authorization"] == "Bearer or-secret"
    assert captured["json"]["model"] == "google/gemini-2.5-flash-image"
    assert "a cyan orb" in captured["json"]["prompt"]
    assert "blurry" in captured["json"]["prompt"]  # no negative field on this API: folded into the prompt
    assert captured["json"]["size"] == "512x512"
    assert captured["json"]["seed"] == 7
    assert out.exists()
    assert out.read_bytes() == b"not-really-a-png"
    assert out.suffix == ".png"


def test_no_width_or_height_omits_size(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _Resp({"data": [{"b64_json": _PNG_B64, "media_type": "image/png"}]})

    monkeypatch.setattr(oi.requests, "post", fake_post)
    oi.generate_image("a cyan orb", "google/gemini-2.5-flash-image")
    assert "size" not in captured["json"]
    assert "seed" not in captured["json"]


def test_401_becomes_a_safe_runtime_error(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)
    monkeypatch.setattr(oi.requests, "post", lambda *a, **k: _Resp({"error": "bad key"}, status=401))
    with pytest.raises(RuntimeError) as exc:
        oi.generate_image("a cat", "google/gemini-2.5-flash-image")
    assert "or-secret" not in str(exc.value)


def test_error_status_message_is_captured_and_key_free(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)
    monkeypatch.setattr(oi.requests, "post", lambda *a, **k: _Resp({"error": {"message": "model overloaded"}}, status=500))
    with pytest.raises(RuntimeError, match="model overloaded"):
        oi.generate_image("a cat", "google/gemini-2.5-flash-image")


def test_no_image_data_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)
    monkeypatch.setattr(oi.requests, "post", lambda *a, **k: _Resp({"data": [{}]}))
    with pytest.raises(RuntimeError, match="no image"):
        oi.generate_image("a cat", "google/gemini-2.5-flash-image")


def test_empty_data_list_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)
    monkeypatch.setattr(oi.requests, "post", lambda *a, **k: _Resp({"data": []}))
    with pytest.raises(RuntimeError, match="no image"):
        oi.generate_image("a cat", "google/gemini-2.5-flash-image")


def test_network_failure_becomes_a_safe_runtime_error(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)

    def fake_post(*a, **k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(oi.requests, "post", fake_post)
    with pytest.raises(RuntimeError, match="reach OpenRouter"):
        oi.generate_image("a cat", "google/gemini-2.5-flash-image")


def test_timeout_becomes_a_safe_runtime_error(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)

    def fake_post(*a, **k):
        raise requests.Timeout("too slow")

    monkeypatch.setattr(oi.requests, "post", fake_post)
    with pytest.raises(RuntimeError, match="did not answer in time"):
        oi.generate_image("a cat", "google/gemini-2.5-flash-image")


def test_malformed_json_is_a_safe_runtime_error(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)

    class _BadJson(_Resp):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(oi.requests, "post", lambda *a, **k: _BadJson({}))
    with pytest.raises(RuntimeError, match="not valid JSON"):
        oi.generate_image("a cat", "google/gemini-2.5-flash-image")


def test_media_type_picks_the_file_extension(monkeypatch, tmp_path):
    monkeypatch.setattr(oi, "GENERATED_DIR", tmp_path)
    _set_key(monkeypatch)
    monkeypatch.setattr(oi.requests, "post", lambda *a, **k: _Resp({"data": [{"b64_json": _PNG_B64, "media_type": "image/webp"}]}))
    out = oi.generate_image("a cat", "google/gemini-2.5-flash-image")
    assert out.suffix == ".webp"


def test_format_saved_formats_image_files_as_markdown():
    out = oi.format_saved([__import__("pathlib").Path("/tmp/x.png")], caption="a cat")
    assert "Saved image:" in out and "![a cat]" in out


def test_format_saved_with_no_paths():
    assert "no files" in oi.format_saved([])
