"""Live model discovery: ask each host what it offers right now.

* Every host -- **OpenRouter** and the vendors' own APIs (OpenAI, Anthropic, Gemini, DeepSeek,
  Groq, Mistral, xAI) -- speaks the OpenAI shape, ``GET /models``; a ``context_length`` /
  ``context_window`` field is read when the host sends one, and OpenRouter's ``pricing``.

Every fetch returns :class:`LmModel` rows whose ``capabilities`` come from
:mod:`backend.services.model_caps`. Failures never carry a URL, a key or a raw
exception: a host that cannot answer is an offline snapshot with a short
``error_kind`` (``no_key``, ``auth``, ``unreachable`` or ``error``) that the UI
and the start-up check can act on.

Snapshots are cached for a short time so a page load, a turn and the start-up
check do not each hit the network; ``force=True`` bypasses the cache.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from backend.services.lm_runtime import HostSnapshot, LmModel, normalize_host
from backend.services.model_caps import classify

CLOUD_TIMEOUT_S = 6.0
CACHE_TTL_S = 30.0
_MAX_MODELS = 200


class DiscoveryError(Exception):
    """A host could not list its models. ``kind`` is machine-readable, the message is safe to show."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _strip(name: Any, prefix: str = "") -> str:
    text = str(name or "").strip()
    return text[len(prefix) :] if prefix and text.startswith(prefix) else text


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _get(url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None, timeout: float) -> Any:
    """GET as JSON, turning every failure into a :class:`DiscoveryError` with a safe message."""
    try:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.Timeout:
        raise DiscoveryError("unreachable", "did not answer in time") from None
    except requests.ConnectionError:
        raise DiscoveryError("unreachable", "could not be reached") from None
    except requests.RequestException:
        raise DiscoveryError("error", "the request failed") from None
    if response.status_code in (401, 403):
        raise DiscoveryError("auth", "the API key was rejected")
    if response.status_code == 404:
        raise DiscoveryError("error", "does not offer a model list at this address")
    if response.status_code >= 400:
        raise DiscoveryError("error", f"answered with HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise DiscoveryError("error", "did not return JSON") from None


def _dedupe(models: list[LmModel]) -> list[LmModel]:
    seen: set[str] = set()
    out: list[LmModel] = []
    for model in models:
        key = model.name.lower()
        if model.name and key not in seen:
            seen.add(key)
            out.append(model)
    return out[:_MAX_MODELS]


def _keep(name: str, meta: dict[str, Any] | None = None) -> list[str]:
    """Capabilities for a chat model, or ``[]`` for anything that is not one (embeddings, speech, images)."""
    caps = classify(name, meta)
    return [] if not caps or caps[0] in {"embed", "image"} else caps


# --------------------------------------------------------------------------
# OpenAI-shaped /models answers
# --------------------------------------------------------------------------


def _per_1m(value: Any) -> float | None:
    """OpenRouter prices in ``pricing`` are USD *per token* as a decimal string -- scale to
    USD per 1M tokens for display, ``None`` when absent or not a number."""
    if value is None:
        return None
    try:
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def _pricing(name: str, info: dict[str, Any]) -> dict[str, Any]:
    """OpenRouter-only: the ``pricing`` object on a ``/v1/models`` row, and whether the id is a
    ``:free`` variant. A no-op ``{}`` for hosts that don't send ``pricing``."""
    pricing = info.get("pricing")
    if not isinstance(pricing, dict):
        return {}
    prompt = _per_1m(pricing.get("prompt"))
    completion = _per_1m(pricing.get("completion"))
    free = name.endswith(":free") or (prompt == 0 and completion == 0)
    return {
        "price_prompt_per_1m": prompt,
        "price_completion_per_1m": completion,
        "is_free": free,
        "supports_prompt_cache": pricing.get("input_cache_read") not in (None, ""),
    }


def _reported_caps(info: dict[str, Any]) -> dict[str, bool]:
    """What the host itself says a model can do. OpenRouter lists ``supported_parameters`` ("tools",
    "reasoning") and ``architecture.input_modalities`` ("image") on every row; without reading them
    the badges were guessed from the name, and deepseek-v4-pro -- a reasoning model -- showed none.
    Absent fields stay absent, so a host that says nothing falls back to the name-based guess."""
    meta: dict[str, bool] = {}
    params = info.get("supported_parameters")
    if isinstance(params, list):
        names = {str(p).lower() for p in params}
        meta["tools"] = "tools" in names
        meta["reasoning"] = bool(names & {"reasoning", "include_reasoning", "reasoning_effort"})
    arch = info.get("architecture")
    modalities = arch.get("input_modalities") if isinstance(arch, dict) else None
    if isinstance(modalities, list):
        meta["vision"] = "image" in {str(m).lower() for m in modalities}
    return meta


def parse_openai_models(payload: Any) -> list[LmModel]:
    """Rows of an OpenAI-shaped ``/models`` answer as chat models. Retired (``active: false``)
    models, embeddings, speech and image models are dropped. OpenRouter additionally sends a
    ``pricing`` object per row, read into price/free/cache-support fields when present."""
    rows: list[Any] = []
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    elif isinstance(payload, list):
        rows = payload
    models: list[LmModel] = []
    for row in rows:
        if isinstance(row, str):
            name, info = _strip(row, "models/"), {}
        elif isinstance(row, dict):
            name, info = _strip(row.get("id") or row.get("name"), "models/"), row
        else:
            continue
        if not name or info.get("active") is False:
            continue
        caps = _keep(name, _reported_caps(info))
        if not caps:
            continue
        models.append(
            LmModel(
                name=name,
                loaded=True,
                capabilities=caps,
                context_length=_int(info.get("context_window") or info.get("context_length") or info.get("max_context_length")),
                **_pricing(name, info),
            )
        )
    return _dedupe(models)


def fetch_openai_compat(base_url: str, api_key: str, extra_headers: dict[str, str] | None = None) -> list[LmModel]:
    from backend.services.api_access import compat_root

    payload = _get(
        f"{compat_root(base_url)}/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json", **(extra_headers or {})},
        timeout=CLOUD_TIMEOUT_S,
    )
    return parse_openai_models(payload)


def parse_openrouter_image_models(payload: Any) -> list[LmModel]:
    """OpenRouter's ``/v1/models?output_modalities=image`` rows, as image-generation models for the
    Images role's own picker. The mirror image of :func:`parse_openai_models`'s chat-only filter
    (which drops these same rows): kept as a separate parser rather than a flag there, since the two
    lists mean different things and are never shown in the same picker."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    models: list[LmModel] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = _strip(row.get("id") or row.get("name"))
        if not name or row.get("active") is False:
            continue
        models.append(LmModel(name=name, loaded=True, capabilities=["image"], **_pricing(name, row)))
    return _dedupe(models)


def fetch_openrouter_image_models(base_url: str, api_key: str) -> list[LmModel]:
    from backend.services.api_access import compat_root

    payload = _get(
        f"{compat_root(base_url)}/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        params={"output_modalities": "image"},
        timeout=CLOUD_TIMEOUT_S,
    )
    return parse_openrouter_image_models(payload)


# --------------------------------------------------------------------------
# Probing and cache
# --------------------------------------------------------------------------


def probe(kind: str, *, variant: str = "") -> HostSnapshot:
    """One live snapshot of a host. Never raises: a failure is an offline snapshot with ``error_kind``.

    ``variant="image"`` asks OpenRouter for its image-generation models instead of its chat models --
    used only by the Images role's own picker, never mixed into the plain snapshot."""
    from backend.services.api_access import is_cloud_host, resolved_key, spec_for, spec_headers

    host = normalize_host(kind)
    snap = HostSnapshot(kind=host)
    try:
        if variant == "image":
            if host != "openrouter":
                raise DiscoveryError("error", "does not offer image-generation models")
            spec = spec_for(host)
            key = resolved_key(host)
            if spec is None or not key:
                raise DiscoveryError("no_key", "has no API key")
            models = fetch_openrouter_image_models(spec.base_url, key)
        elif is_cloud_host(host):
            spec = spec_for(host)
            key = resolved_key(host)
            if spec is None or not key:
                raise DiscoveryError("no_key", "has no API key")
            models = fetch_openai_compat(spec.base_url, key, spec_headers(host, key))
        else:
            raise DiscoveryError("error", "is not a model host")
    except DiscoveryError as exc:
        snap.error, snap.error_kind = exc.message, exc.kind
    except Exception:  # noqa: BLE001 - a dead or odd host must never break a page, a turn or start-up
        snap.error, snap.error_kind = "could not be checked", "error"
    else:
        snap.online = True
        snap.models = models
        snap.loaded_names = [m.name for m in models if m.loaded]
    snap.checked_at = time.time()
    return snap


_lock = threading.Lock()
_cache: dict[str, tuple[float, HostSnapshot]] = {}


def cached_snapshot(kind: str, *, force: bool = False, variant: str = "") -> HostSnapshot:
    host = normalize_host(kind)
    cache_key = f"{host}#{variant}" if variant else host
    now = time.monotonic()
    if not force:
        with _lock:
            hit = _cache.get(cache_key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    snap = probe(host, variant=variant)
    with _lock:
        _cache[cache_key] = (time.monotonic(), snap)
    return snap


def peek_snapshot(kind: str) -> HostSnapshot | None:
    """The cached snapshot of a host, however old, or ``None``. Never touches the network."""
    with _lock:
        hit = _cache.get(normalize_host(kind))
    return hit[1] if hit else None


def clear_cache(kind: str | None = None) -> None:
    """Forget cached snapshots (a key, a URL or a model choice changed)."""
    with _lock:
        if kind is None:
            _cache.clear()
        else:
            _cache.pop(normalize_host(kind), None)
