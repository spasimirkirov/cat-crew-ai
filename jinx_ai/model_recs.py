"""Hugging Face GGUF families sized to this GPU (same catalog as AI Models → Hugging Face Hub)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import BASE_DIR
from .gguf_browser import HfFamily, search_families, vram_budget_gb
from .hardware import HardwareInfo, probe_hardware

CACHE_PATH = BASE_DIR / "data" / "model_recs.json"
TTL_SEC = 12 * 3600
MAX_RECS = 8

FALLBACK_ITEMS = [
    {
        "name": "bartowski/gemma-3-12b-it-GGUF",
        "family": "gemma-3-12b-it",
        "size_gb": 8.0,
        "pulls": 0,
        "why": "Trusted GGUF. Pull from AI Models → Hugging Face Hub and pick a Q4_K_M that fits VRAM.",
        "capabilities": ["tools", "vision"],
    },
    {
        "name": "unsloth/Qwen2.5-Coder-14B-Instruct-GGUF",
        "family": "Qwen2.5-Coder-14B",
        "size_gb": 9.0,
        "pulls": 0,
        "why": "Coding GGUF from a trusted author. Pick a quant that fits ~70% of VRAM.",
        "capabilities": ["tools"],
    },
]


def load_cache() -> dict[str, Any] | None:
    try:
        if CACHE_PATH.is_file():
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_cache(payload: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_is_fresh(payload: dict[str, Any] | None) -> bool:
    if not payload or not payload.get("items"):
        return False
    raw = str(payload.get("fetched_at") or "")
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - when).total_seconds()
    return 0 <= age < TTL_SEC


def fetch_hf_families(query: str = "gguf") -> list[HfFamily]:
    return search_families(query, limit=24)


def _item_from_family(item: HfFamily, hw: HardwareInfo) -> dict[str, Any]:
    budget = vram_budget_gb(hw.vram_gb or 8.0)
    trusted = "Trusted author. " if item.trusted else ""
    why = (
        f"{trusted}Hugging Face GGUF. Open AI Models → Hugging Face Hub, pick a quant around "
        f"Q4_K_M that fits ~{budget:.0f} GB ({hw.gpu or 'this GPU'})."
    )
    return {
        "name": item.repo,
        "family": item.title,
        "size_gb": 0,
        "pulls": item.downloads,
        "blurb": item.blurb,
        "why": why,
        "capabilities": ["tools", "chat"],
    }


def refresh_recommendations(*, force: bool = False) -> dict[str, Any]:
    hw = probe_hardware(ttl=0)
    cached = load_cache()
    if not force and cache_is_fresh(cached):
        cached = dict(cached or {})
        cached["hardware"] = hw.to_dict()
        return cached

    source = "huggingface.co/api/models?filter=gguf"
    families: list[HfFamily] = []
    try:
        families = fetch_hf_families()
    except Exception as exc:  # noqa: BLE001
        source = f"offline fallback ({exc})"
        if cached and cached.get("items"):
            cached = dict(cached)
            cached["hardware"] = hw.to_dict()
            cached["source"] = source
            save_cache(cached)
            return cached
        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "hardware": hw.to_dict(),
            "items": list(FALLBACK_ITEMS),
        }
        save_cache(payload)
        return payload

    items = [_item_from_family(item, hw) for item in families[:MAX_RECS]]
    if not items:
        items = list(FALLBACK_ITEMS)
        source = source + " + built-in catalog"
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "hardware": hw.to_dict(),
        "items": items,
    }
    save_cache(payload)
    return payload


def format_recommendations(payload: dict[str, Any] | None = None) -> str:
    data = payload or load_cache() or refresh_recommendations()
    hw_raw = data.get("hardware") if isinstance(data, dict) else None
    if isinstance(hw_raw, dict):
        hw = HardwareInfo(
            cpu=str(hw_raw.get("cpu") or ""),
            cores=int(hw_raw.get("cores") or 1),
            ram_gb=float(hw_raw.get("ram_gb") or 0),
            gpu=str(hw_raw.get("gpu") or ""),
            vram_gb=float(hw_raw.get("vram_gb") or 0),
        )
        lines = [hw.prompt_line()]
    else:
        lines = [probe_hardware().prompt_line()]
    source = str((data or {}).get("source") or "")
    when = str((data or {}).get("fetched_at") or "")
    if source:
        lines.append(f"Source: {source}" + (f" ({when})" if when else ""))
    items = list((data or {}).get("items") or [])
    if not items:
        lines.append("No GGUF families found. Search on AI Models → Hugging Face Hub.")
        return "\n".join(lines)
    lines.append("Recommended Hugging Face GGUF families (open Hub, then pick a quant):")
    for i, item in enumerate(items, 1):
        name = str(item.get("name") or "")
        gb = item.get("size_gb")
        why = str(item.get("why") or "").strip()
        caps = " · ".join(item.get("capabilities") or [])
        size = f"  (~{gb:g} GB)" if gb else ""
        extra = f"  {caps}" if caps else ""
        lines.append(f"{i}. {name}{size}{extra}")
        if why:
            lines.append(f"   {why}")
    lines.append("Pull from AI Models → Hugging Face Hub.")
    return "\n".join(lines)
