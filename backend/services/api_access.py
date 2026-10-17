"""Optional API keys (cheap / free). Stored in data/settings.json, not .env.

Keys go in through ``PUT /api/keys/{id}`` (Models -> Hosts in the UI) and are **write-only**:
no endpoint ever returns one, only whether one is set and where it comes from
(``key_source``). An environment variable is the fallback when nothing is saved.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from backend.config import BRAVE_SEARCH_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from backend.services.model_caps import classify



@dataclass(frozen=True)
class ApiSpec:
    id: str
    title: str
    subtitle: str
    signup: str
    key_field: str
    enabled_field: str
    env_names: tuple[str, ...]
    kind: str  # "llm" (a model host) | "search" | "decision" (Jev on TypeSafe)
    base_url: str = ""
    default_model: str = ""
    extra_body: dict[str, Any] | None = None
    #: Extra request headers the host wants besides ``Authorization: Bearer <key>`` (Anthropic's
    #: model listing reads ``x-api-key`` and ``anthropic-version``).
    headers: tuple[tuple[str, str], ...] = ()


CATALOG: tuple[ApiSpec, ...] = (
    ApiSpec(
        id="openrouter",
        title="OpenRouter",
        subtitle="Every cloud model (DeepSeek, Claude, Gemini, Llama, ...) through one key and one API.",
        signup="https://openrouter.ai/keys",
        key_field="openrouter_key",
        enabled_field="openrouter_enabled",
        env_names=("CATCREW_OPENROUTER_KEY", "OPENROUTER_API_KEY"),
        kind="llm",
        base_url=OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1",
        default_model=OPENROUTER_MODEL or "openrouter/auto",
    ),
    # The well-known hosts, each through its own OpenAI-compatible endpoint: one key per vendor,
    # its models listed live from GET /models.
    ApiSpec(
        id="openai",
        title="OpenAI",
        subtitle="GPT models, straight from OpenAI.",
        signup="https://platform.openai.com/api-keys",
        key_field="openai_key",
        enabled_field="openai_enabled",
        env_names=("CATCREW_OPENAI_KEY", "OPENAI_API_KEY"),
        kind="llm",
        base_url="https://api.openai.com/v1",
    ),
    ApiSpec(
        id="anthropic",
        title="Anthropic",
        subtitle="Claude models, straight from Anthropic (OpenAI-compatible endpoint).",
        signup="https://console.anthropic.com/settings/keys",
        key_field="anthropic_key",
        enabled_field="anthropic_enabled",
        env_names=("CATCREW_ANTHROPIC_KEY", "ANTHROPIC_API_KEY"),
        kind="llm",
        base_url="https://api.anthropic.com/v1",
        headers=(("anthropic-version", "2023-06-01"),),
    ),
    ApiSpec(
        id="gemini",
        title="Google Gemini",
        subtitle="Gemini models from Google AI Studio (OpenAI-compatible endpoint).",
        signup="https://aistudio.google.com/apikey",
        key_field="gemini_key",
        enabled_field="gemini_enabled",
        env_names=("CATCREW_GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
        kind="llm",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    ),
    ApiSpec(
        id="deepseek",
        title="DeepSeek",
        subtitle="DeepSeek chat and reasoning models, straight from DeepSeek.",
        signup="https://platform.deepseek.com/api_keys",
        key_field="deepseek_key",
        enabled_field="deepseek_enabled",
        env_names=("CATCREW_DEEPSEEK_KEY", "DEEPSEEK_API_KEY"),
        kind="llm",
        base_url="https://api.deepseek.com/v1",
    ),
    ApiSpec(
        id="groq",
        title="Groq",
        subtitle="Open models (Llama, Qwen, gpt-oss...) on very fast inference.",
        signup="https://console.groq.com/keys",
        key_field="groq_key",
        enabled_field="groq_enabled",
        env_names=("CATCREW_GROQ_KEY", "GROQ_API_KEY"),
        kind="llm",
        base_url="https://api.groq.com/openai/v1",
    ),
    ApiSpec(
        id="mistral",
        title="Mistral",
        subtitle="Mistral and Codestral models, straight from Mistral AI.",
        signup="https://console.mistral.ai/api-keys",
        key_field="mistral_key",
        enabled_field="mistral_enabled",
        env_names=("CATCREW_MISTRAL_KEY", "MISTRAL_API_KEY"),
        kind="llm",
        base_url="https://api.mistral.ai/v1",
    ),
    ApiSpec(
        id="xai",
        title="xAI",
        subtitle="Grok models, straight from xAI.",
        signup="https://console.x.ai",
        key_field="xai_key",
        enabled_field="xai_enabled",
        env_names=("CATCREW_XAI_KEY", "XAI_API_KEY"),
        kind="llm",
        base_url="https://api.x.ai/v1",
    ),
    # Jev, TypeSafe AI's decision model, on its official API. (Jev through OpenRouter needs no key of
    # its own: it uses the OpenRouter key above.)
    ApiSpec(
        id="typesafe",
        title="TypeSafe (Jev)",
        subtitle="Jev's official API. Needed only when Jev runs on TypeSafe; through OpenRouter it uses the OpenRouter key.",
        signup="https://typesafe.ai",
        key_field="typesafe_key",
        enabled_field="typesafe_enabled",
        env_names=("CATCREW_TYPESAFE_KEY", "TYPESAFE_API_KEY", "CATCREW_JEV_KEY"),
        kind="decision",
        base_url="https://api.typesafe.ai/v1/systemone",
        default_model="jev-latest",
    ),
    ApiSpec(
        id="brave",
        title="Brave Search",
        subtitle="Free-tier web and image hits. DuckDuckGo still works with no key.",
        signup="https://brave.com/search/api/",
        key_field="brave_key",
        enabled_field="brave_enabled",
        env_names=("CATCREW_BRAVE_KEY", "BRAVE_SEARCH_API_KEY"),
        kind="search",
    ),
)

#: Every model host is a cloud host with a key: OpenRouter (every vendor through one key) and the
#: vendors' own APIs. There is no local host.
CLOUD_HOSTS = tuple(spec.id for spec in CATALOG if spec.kind == "llm")
LOCAL_HOSTS: tuple[str, ...] = ()
CHAT_HOSTS = CLOUD_HOSTS


def spec_headers(kind: str, key: str = "") -> dict[str, str]:
    """The host's extra headers (Anthropic: its version, and the key as ``x-api-key`` too)."""
    spec = spec_for(kind)
    headers = dict(spec.headers) if spec else {}
    if spec is not None and spec.id == "anthropic" and key:
        headers["x-api-key"] = key
    return headers


def api_access_defaults() -> dict[str, Any]:
    """The ``api_access`` section of the settings file: an empty key and an enabled flag per service."""
    out: dict[str, Any] = {}
    for spec in CATALOG:
        out[spec.key_field] = ""
        out[spec.enabled_field] = True
    return out


def spec_for(kind: str) -> ApiSpec | None:
    text = str(kind or "").strip().lower()
    for item in CATALOG:
        if item.id == text:
            return item
    return None


def is_cloud_host(kind: str) -> bool:
    return str(kind or "").strip().lower() in CLOUD_HOSTS


def api_access_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    from backend.services.settings_store import load_settings

    raw = (data or load_settings()).get("api_access") or {}
    cfg = api_access_defaults()
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key not in cfg:
                continue
            if key.endswith("_enabled"):
                cfg[key] = bool(value)
            else:
                cfg[key] = str(value or "").strip()
    return cfg


def save_api_access(update: dict[str, Any]) -> dict[str, Any]:
    from backend.services.settings_store import save_settings

    cfg = api_access_settings()
    for key, value in (update or {}).items():
        if key not in cfg:
            continue
        if key.endswith("_enabled"):
            cfg[key] = bool(value)
        else:
            cfg[key] = str(value or "").strip()
    save_settings({"api_access": cfg})
    return api_access_settings()


KEY_MAX_LEN = 512


class InvalidKey(ValueError):
    """A pasted key that cannot be right. The message never contains the key."""


def clean_key(raw: Any) -> str:
    """The key to store, or :class:`InvalidKey`. Keys have no whitespace, so a pasted
    ``Bearer sk-...`` or a stray line break is rejected rather than saved and then
    failing later with an opaque 401."""
    if not isinstance(raw, str):
        raise InvalidKey("The key must be text.")
    key = raw.strip()
    if not key:
        raise InvalidKey("Paste the key first. Use Remove to clear a saved one.")
    if len(key) > KEY_MAX_LEN:
        raise InvalidKey(f"That is too long for an API key (limit {KEY_MAX_LEN} characters).")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in key):
        raise InvalidKey("A key has no spaces or line breaks. Paste only the key itself, without a Bearer prefix.")
    return key


def _env_key(spec: ApiSpec) -> str:
    if spec.id == "brave" and BRAVE_SEARCH_API_KEY.strip():
        return BRAVE_SEARCH_API_KEY.strip()
    for name in spec.env_names:
        text = str(os.environ.get(name) or "").strip()
        if text:
            return text
    return ""


def resolved_key(kind: str, data: dict[str, Any] | None = None) -> str:
    spec = spec_for(kind)
    if spec is None:
        return ""
    cfg = api_access_settings(data)
    stored = str(cfg.get(spec.key_field) or "").strip()
    return stored or _env_key(spec)


def key_source(kind: str, data: dict[str, Any] | None = None) -> str:
    """Where the key in use comes from: ``saved`` (settings), ``environment`` or ``none``."""
    spec = spec_for(kind)
    if spec is None:
        return "none"
    if str(api_access_settings(data).get(spec.key_field) or "").strip():
        return "saved"
    return "environment" if _env_key(spec) else "none"


def set_api_key(kind: str, raw: Any) -> None:
    """Validate and save the key of a catalog service. Raises :class:`InvalidKey`."""
    spec = spec_for(kind)
    if spec is None:
        raise KeyError(kind)
    save_api_access({spec.key_field: clean_key(raw)})


def clear_api_key(kind: str) -> None:
    """Forget the saved key (an environment variable, if any, keeps working)."""
    spec = spec_for(kind)
    if spec is None:
        raise KeyError(kind)
    save_api_access({spec.key_field: ""})


def key_enabled(kind: str, data: dict[str, Any] | None = None) -> bool:
    spec = spec_for(kind)
    if spec is None:
        return False
    cfg = api_access_settings(data)
    return bool(cfg.get(spec.enabled_field, True))


def has_api_key(kind: str, data: dict[str, Any] | None = None) -> bool:
    return bool(resolved_key(kind, data))


def cloud_host_ready(kind: str, data: dict[str, Any] | None = None) -> bool:
    return is_cloud_host(kind) and has_api_key(kind, data) and key_enabled(kind, data)


def ready_cloud_hosts(data: dict[str, Any] | None = None) -> list[str]:
    return [kind for kind in CLOUD_HOSTS if cloud_host_ready(kind, data)]


def default_cloud_model(kind: str) -> str:
    spec = spec_for(kind)
    if spec is None:
        return ""
    return spec.default_model


def brave_search_key(data: dict[str, Any] | None = None) -> str:
    if not key_enabled("brave", data):
        return ""
    return resolved_key("brave", data)


def compat_root(base_url: str) -> str:
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1") or root.endswith("/openai"):
        return root
    return root + "/v1"


def _openrouter_routing_body() -> dict[str, Any]:
    """The ``provider``/``plugins`` fields OpenRouter's own API documents for multi-provider
    routing and the (opt-in) context-compression plugin. Never adds a key OpenRouter would treat
    as anything but its documented meaning -- there is no real ``:auto`` model-slug suffix, so
    "route to the fastest/cheapest provider automatically" is expressed the documented way, through
    this ``provider`` object, not by rewriting the model slug."""
    from backend.services.settings_store import openrouter_settings

    cfg = openrouter_settings()
    body: dict[str, Any] = {}
    provider: dict[str, Any] = {"allow_fallbacks": cfg["allow_fallbacks"]}
    if cfg["provider_sort"]:
        provider["sort"] = cfg["provider_sort"]
    body["provider"] = provider
    if cfg["compression_enabled"]:
        body["plugins"] = [{"id": "context-compression"}]
    return body


def make_cloud_provider(kind: str, model: str = ""):
    """The provider for a cloud host: OpenRouter (``model`` is a slug like
    ``deepseek/deepseek-v4-pro``), or a vendor's own OpenAI-compatible API (``model`` is that
    vendor's id, e.g. ``gpt-4.1``, ``claude-sonnet-4-5``)."""
    from backend.providers.openai_compat import OpenAICompatProvider, OpenRouterProvider

    spec = spec_for(kind)
    if spec is None or spec.kind != "llm":
        raise ValueError(f"Unknown cloud host: {kind}")
    key = resolved_key(kind)
    if not key:
        raise RuntimeError(
            f"Missing {spec.title} API key. Add it in Models → Hosts."
        )
    name = (model or "").strip() or default_cloud_model(kind)
    extra_body = dict(spec.extra_body or {})
    if "reasoning" in classify(name):
        extra_body.pop("thinking", None)  # a reasoning model is picked to think: do not switch it off
    if kind != "openrouter":
        if not name:
            raise RuntimeError(f"No {spec.title} model is picked for this role. Choose one on the Models page.")
        return OpenAICompatProvider(
            base_url=spec.base_url,
            api_key=key,
            model=name,
            timeout=180,
            extra_body=extra_body,
            headers=spec_headers(kind, key),
            display_name=spec.title,
            host_id=spec.id,
            stream_usage=True,  # a host that does not know stream_options is asked again without it
            error_hint=f"Check the {spec.title} key in Models → Hosts.",
        )
    extra_body.update(_openrouter_routing_body())
    return OpenRouterProvider(
        api_key=key,
        model=name,
        base_url=spec.base_url,
        timeout=180,
        extra_body=extra_body,
    )
