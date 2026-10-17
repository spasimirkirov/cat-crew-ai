"""Context window per host and model: how big a request may get, and where compaction starts.

``get_model_context_limit(host, model)`` returns ``{"max_tokens", "compression_threshold", "source"}``
with ``source`` one of ``env`` (``CATCREW_CONTEXT_TOKENS`` forces a size), ``provider`` (the table
below, by host or -- on OpenRouter -- by the vendor in the model slug) or ``default`` (nothing better
is known: a conservative mid-size window). Every host is a cloud host now; nothing is probed.
"""

from __future__ import annotations

import threading
import time
from typing import Any

THRESHOLD_RATIO = 0.85

#: host -> (max_tokens, compression_threshold)
CLOUD_LIMITS: dict[str, tuple[int, int]] = {
    "deepseek": (64_000, 54_000),
    "gemini": (1_000_000, 850_000),
    "anthropic": (200_000, 170_000),
    "openai": (128_000, 110_000),
    "mistral": (128_000, 108_000),
    "xai": (128_000, 108_000),
}
#: A host or model we know nothing about: a conservative small window (compacting early is cheap;
#: overflowing a window fails the request).
UNKNOWN_DEFAULT: tuple[int, int] = (16_384, 14_000)
LOCAL_DEFAULT = UNKNOWN_DEFAULT  # kept for callers that still import the old name
#: OpenRouter names its models ``<vendor>/<model>``: the vendor tells the window.
_VENDOR_HOSTS = {"deepseek": "deepseek", "google": "gemini", "anthropic": "anthropic", "openai": "openai"}
_MEMO_TTL_S = 60.0
_memo: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def threshold_for(max_tokens: int) -> int:
    """The summarize-at size for a window: 85 %."""
    return max(1, int(int(max_tokens) * THRESHOLD_RATIO))


def _row(max_tokens: int, threshold: int, source: str) -> dict[str, Any]:
    return {"max_tokens": int(max_tokens), "compression_threshold": int(threshold), "source": source}


def _from_pair(pair: tuple[int, int], source: str) -> dict[str, Any]:
    return _row(pair[0], pair[1], source)


def _env_override() -> dict[str, Any] | None:
    try:
        from backend.config import CONTEXT_TOKEN_LIMIT

        forced = int(CONTEXT_TOKEN_LIMIT or 0)
    except Exception:  # noqa: BLE001
        return None
    if forced <= 0:
        return None
    return _row(max(forced, round(forced / THRESHOLD_RATIO)), forced, "env")


def _groq(model: str) -> tuple[int, int]:
    name = model.lower()
    if any(tag in name for tag in ("llama-3.1", "llama-3.3", "llama3-", "gpt-oss", "llama-4")):
        return (128_000, 108_000)
    return (32_768, 27_800)


def get_model_context_limit(host: str, model: str = "", *, fetch: bool = True) -> dict[str, Any]:
    """``{"max_tokens", "compression_threshold", "source"}`` for a host and model. Never raises.

    ``fetch`` is kept for callers; the table needs no network."""
    try:
        forced = _env_override()
        if forced is not None:
            return forced
        h = str(host or "").strip().lower()
        m = str(model or "").strip()
        if h in CLOUD_LIMITS:
            return _from_pair(CLOUD_LIMITS[h], "provider")
        if h == "groq":
            return _from_pair(_groq(m), "provider")
        if h == "openrouter":
            vendor = _VENDOR_HOSTS.get(m.split("/", 1)[0].lower()) if "/" in m else None
            return _from_pair(CLOUD_LIMITS[vendor], "provider") if vendor else _from_pair((64_000, 54_000), "default")
        return _from_pair(UNKNOWN_DEFAULT, "default")
    except Exception:  # noqa: BLE001
        return _from_pair(UNKNOWN_DEFAULT, "default")


def limits_for_provider(provider: Any, *, fetch: bool = True) -> dict[str, Any]:
    """The limits of the model a provider object talks to (``host_id`` and ``model`` attributes)."""
    host = str(getattr(provider, "host_id", "") or "")
    model = str(getattr(provider, "model", "") or "")
    return get_model_context_limit(host, model, fetch=fetch)


def clear_cache() -> None:
    with _lock:
        _memo.clear()
