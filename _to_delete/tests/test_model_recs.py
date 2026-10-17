from pathlib import Path

from backend.services.gguf_browser import HfFamily
from backend.services.hardware import HardwareInfo
from backend.services.model_recs import format_recommendations, refresh_recommendations


_RTX_16 = HardwareInfo(
    cpu="AMD Ryzen 9",
    cores=16,
    ram_gb=32.0,
    gpu="NVIDIA GeForce RTX 5080",
    vram_gb=16.0,
)


def test_format_and_refresh_cache(tmp_path: Path, monkeypatch) -> None:
    import backend.services.model_recs as recs

    monkeypatch.setattr(recs, "CACHE_PATH", tmp_path / "model_recs.json")
    monkeypatch.setattr(recs, "probe_hardware", lambda ttl=0: _RTX_16)
    monkeypatch.setattr(
        recs,
        "fetch_hf_families",
        lambda query="gguf": [
            HfFamily(
                repo="bartowski/gemma-3-12b-it-GGUF",
                title="gemma-3-12b-it-GGUF",
                downloads=90_000,
                author="bartowski",
                trusted=True,
            )
        ],
    )
    payload = refresh_recommendations(force=True)
    assert (tmp_path / "model_recs.json").is_file()
    assert payload["items"]
    text = format_recommendations(payload)
    assert "This PC:" in text
    assert "bartowski/gemma-3-12b-it-GGUF" in text
    assert "Hugging Face Hub" in text


def test_tools_read_cache(monkeypatch) -> None:
    payload = {
        "fetched_at": "2026-09-09T00:00:00+00:00",
        "source": "test",
        "hardware": _RTX_16.to_dict(),
        "items": [
            {
                "name": "bartowski/gemma-3-12b-it-GGUF",
                "size_gb": 8.8,
                "why": "Fits this GPU.",
                "capabilities": ["tools", "vision"],
            }
        ],
    }
    monkeypatch.setattr("backend.services.model_recs.load_cache", lambda: payload)
    from backend.tools import execute_tool, tools_schema_for

    text = execute_tool("recommend_models", {})
    assert "bartowski/gemma-3-12b-it-GGUF" in text
    hw = execute_tool("hardware_info", {})
    assert "GPU:" in hw or "CPU:" in hw
    names = {item["function"]["name"] for item in tools_schema_for("assistant")}
    assert "hardware_info" in names
    assert "recommend_models" in names
