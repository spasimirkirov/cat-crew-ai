"""Optional API keys (cheap / free). Stored in data/settings.json, not .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .config import BRAVE_SEARCH_API_KEY, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

CLOUD_HOSTS = ("deepseek", "gemini", "groq")
LOCAL_HOSTS = ("local",)
CHAT_HOSTS = LOCAL_HOSTS + CLOUD_HOSTS


@dataclass(frozen=True)
class ApiSpec:
    id: str
    title: str
    subtitle: str
    signup: str
    key_field: str
    enabled_field: str
    env_names: tuple[str, ...]
    kind: str  # "llm" | "search"
    unlocks: str
    base_url: str = ""
    default_model: str = ""
    fallback_models: tuple[str, ...] = ()
    extra_body: dict[str, Any] | None = None


CATALOG: tuple[ApiSpec, ...] = (
    ApiSpec(
        id="deepseek",
        title="DeepSeek",
        subtitle="Very cheap chat models with tools. Local GGUFs stay available.",
        signup="https://platform.deepseek.com/api_keys",
        key_field="deepseek_key",
        enabled_field="deepseek_enabled",
        env_names=("DEEPSEEK_API_KEY", "JINX_DEEPSEEK_KEY"),
        kind="llm",
        unlocks="Chat host in AI Models → General",
        base_url=DEEPSEEK_BASE_URL or "https://api.deepseek.com",
        default_model=DEEPSEEK_MODEL or "deepseek-chat",
        fallback_models=("deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro"),
        extra_body={"thinking": {"type": "disabled"}},
    ),
    ApiSpec(
        id="gemini",
        title="Google AI Studio",
        subtitle="Free Gemini models (rate-limited). OpenAI-compatible endpoint.",
        signup="https://aistudio.google.com/apikey",
        key_field="gemini_key",
        enabled_field="gemini_enabled",
        env_names=("GEMINI_API_KEY", "GOOGLE_API_KEY", "JINX_GEMINI_KEY"),
        kind="llm",
        unlocks="Chat host in AI Models → General",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.5-flash",
        fallback_models=("gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"),
    ),
    ApiSpec(
        id="groq",
        title="Groq",
        subtitle="Free, fast OpenAI-compatible chat. Rate-limited free tier.",
        signup="https://console.groq.com/keys",
        key_field="groq_key",
        enabled_field="groq_enabled",
        env_names=("GROQ_API_KEY", "JINX_GROQ_KEY"),
        kind="llm",
        unlocks="Chat host in AI Models → General",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        fallback_models=(
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-20b",
        ),
    ),
    ApiSpec(
        id="brave",
        title="Brave Search",
        subtitle="Free-tier web and image hits. DuckDuckGo still works with no key.",
        signup="https://brave.com/search/api/",
        key_field="brave_key",
        enabled_field="brave_enabled",
        env_names=("JINX_BRAVE_KEY", "BRAVE_SEARCH_API_KEY"),
        kind="search",
        unlocks="Extra web_search and image_search results",
    ),
    ApiSpec(
        id="huggingface",
        title="Hugging Face",
        subtitle="Read token for gated GGUFs. Public models work with no key.",
        signup="https://huggingface.co/settings/tokens",
        key_field="huggingface_key",
        enabled_field="huggingface_enabled",
        env_names=("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "JINX_HF_TOKEN"),
        kind="search",
        unlocks="Gated downloads on AI Models → Hugging Face Hub",
    ),
)

_DEFAULTS: dict[str, Any] = {}
for _spec in CATALOG:
    _DEFAULTS[_spec.key_field] = ""
    _DEFAULTS[_spec.enabled_field] = True


def spec_for(kind: str) -> ApiSpec | None:
    text = str(kind or "").strip().lower()
    for item in CATALOG:
        if item.id == text:
            return item
    return None


def is_cloud_host(kind: str) -> bool:
    return str(kind or "").strip().lower() in CLOUD_HOSTS


def api_access_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    from .settings_store import load_settings

    raw = (data or load_settings()).get("api_access") or {}
    cfg = dict(_DEFAULTS)
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
    from .settings_store import save_settings

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


def _env_key(spec: ApiSpec) -> str:
    if spec.id == "deepseek" and DEEPSEEK_API_KEY.strip():
        return DEEPSEEK_API_KEY.strip()
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
    return spec.default_model or (spec.fallback_models[0] if spec.fallback_models else "")


def huggingface_token(data: dict[str, Any] | None = None) -> str:
    if not key_enabled("huggingface", data):
        return ""
    return resolved_key("huggingface", data)


def brave_search_key(data: dict[str, Any] | None = None) -> str:
    if not key_enabled("brave", data):
        return ""
    return resolved_key("brave", data)


def compat_root(base_url: str) -> str:
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1") or root.endswith("/openai"):
        return root
    return root + "/v1"


def make_cloud_provider(kind: str, model: str = ""):
    from .providers.openai_compat import OpenAICompatProvider

    spec = spec_for(kind)
    if spec is None or spec.kind != "llm":
        raise ValueError(f"Unknown cloud host: {kind}")
    key = resolved_key(kind)
    if not key:
        raise RuntimeError(
            f"Missing {spec.title} API key. Add it in Settings → API Access."
        )
    name = (model or "").strip() or default_cloud_model(kind)
    return OpenAICompatProvider(
        base_url=spec.base_url,
        api_key=key,
        model=name,
        timeout=180,
        extra_body=dict(spec.extra_body or {}),
        display_name=spec.title,
        error_hint=f"Check the {spec.title} key in Settings → API Access.",
    )
