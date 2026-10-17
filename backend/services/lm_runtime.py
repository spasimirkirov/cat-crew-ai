"""Model hosts: names, snapshot types and the note a turn gets about its model.

Talking to the hosts (OpenRouter and the vendors' own APIs) lives in
:mod:`backend.services.model_discovery`; what a model can do is decided in
:mod:`backend.services.model_caps`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LOCAL_HOSTS: tuple[str, ...] = ()  # every model host is a cloud host

# Order capabilities are listed in: most useful first.
_CAP_ORDER = ("tools", "vision", "reasoning", "embed", "image", "chat")


@dataclass
class LmModel:
    name: str
    loaded: bool = False
    capabilities: list[str] = field(default_factory=list)
    context_length: int = 0
    loaded_context_length: int = 0  # kept for wire compatibility; cloud hosts report context_length
    display_name: str = ""
    extra: str = ""
    #: USD per 1M tokens, from OpenRouter's own `/v1/models` `pricing` object. ``None`` when the
    #: host does not report pricing (any host but OpenRouter).
    price_prompt_per_1m: float | None = None
    price_completion_per_1m: float | None = None
    #: ``True`` when the model id ends with ``:free`` and its pricing is zero.
    is_free: bool = False
    #: ``True`` when OpenRouter reports a non-null `pricing.input_cache_read` -- the model can serve
    #: cached-prompt reads at a discount (see ``config.OPENROUTER_PROMPT_CACHING``).
    supports_prompt_cache: bool = False


@dataclass
class HostSnapshot:
    kind: str
    online: bool = False
    error: str = ""
    error_kind: str = ""  # "" | "no_key" | "auth" | "unreachable" | "error"
    checked_at: float = 0.0
    models: list[LmModel] = field(default_factory=list)
    loaded_names: list[str] = field(default_factory=list)


def normalize_host(kind: str | None) -> str:
    """A host id as stored and probed: a cloud host's id, ``comfyui``, ``off``, or ``""`` for anything
    else (an old ``lmstudio`` / ``ollama`` pick included: there is no local model host any more)."""
    from backend.services.api_access import CLOUD_HOSTS

    text = str(kind or "").strip().lower()
    if text in CLOUD_HOSTS:
        return text
    if text in {"comfyui", "comfy"}:
        return "comfyui"
    if text == "off":
        return "off"
    return ""


def host_label(kind: str) -> str:
    from backend.services.api_access import spec_for

    text = str(kind or "").strip().lower()
    spec = spec_for(text)
    if spec is not None:
        return spec.title
    return {"comfyui": "ComfyUI", "searxng": "SearXNG", "off": "Off"}.get(text, text or "no host")


def format_capabilities(caps: list[str] | None) -> str:
    seen = {str(item) for item in (caps or [])}
    return " · ".join(item for item in _CAP_ORDER if item in seen)


def loaded_model_note(host: str, model: str, caps: list[str] | None) -> str:
    name = (model or "").strip() or "unknown"
    label = host_label(host)
    bits = format_capabilities(caps)
    parts = [f"Chat model: {name} via {label}."]
    if bits:
        parts.append(f"Reported capabilities: {bits}.")
    if "tools" in (caps or []):
        parts.append("Use function calling for tools.")
    else:
        parts.append(
            "This model may not support tool calling; if a tool fails, answer in text."
        )
    if "vision" in (caps or []):
        parts.append(
            "The weights support vision; CatCrewAI does not attach images to chat yet."
        )
    else:
        parts.append("This model does not report vision.")
    return " ".join(parts)


def model_names_match(left: str, right: str) -> bool:
    a = (left or "").strip().lower()
    b = (right or "").strip().lower()
    return bool(a and b and a == b)


def snapshot_has_model(snap: HostSnapshot, name: str) -> bool:
    return any(model_names_match(item.name, name) for item in snap.models)
