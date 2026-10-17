"""App Exit must free GPU children (llama-server + Comfy)."""

from __future__ import annotations

from jinx_ai.app import shutdown_owned_runtimes


def test_shutdown_owned_runtimes_stops_llama_and_comfy(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        "jinx_ai.llama_server.stop_server",
        lambda: calls.append("llama") or "Stopped llama-server",
    )
    monkeypatch.setattr(
        "jinx_ai.comfyui.stop_comfyui",
        lambda: calls.append("comfy") or "Stopped ComfyUI",
    )

    shutdown_owned_runtimes()
    assert calls == ["llama", "comfy"]


def test_shutdown_owned_runtimes_survives_errors(monkeypatch) -> None:
    calls: list[str] = []

    def boom() -> str:
        raise RuntimeError("nope")

    monkeypatch.setattr("jinx_ai.llama_server.stop_server", boom)
    monkeypatch.setattr(
        "jinx_ai.comfyui.stop_comfyui",
        lambda: calls.append("comfy") or "ok",
    )

    shutdown_owned_runtimes()
    assert calls == ["comfy"]
