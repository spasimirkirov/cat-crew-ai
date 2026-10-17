"""Simple settings in data/settings.json (does not touch .env)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import (
    AGENT_MAX_ROUNDS,
    AVATAR_WINDOW_SIZE,
    BASE_DIR,
    CHARACTER_SIZE_PRESETS,
    COMMAND_TIMEOUT,
)

SETTINGS_PATH = BASE_DIR / "data" / "settings.json"

_DEFAULT_CHARACTER_WINDOW: dict[str, Any] = {
    "size_preset": "default",
    "width": AVATAR_WINDOW_SIZE[0],
    "height": AVATAR_WINDOW_SIZE[1],
    "visible": True,
    "x": None,
    "y": None,
}

_DEFAULT_LOOK: dict[str, Any] = {
    "transparent_bg": False,
    "hide_border": False,
    "orb_style": "orb",
    "hologram": False,
    "body_debug": False,
}

_ORB_STYLES = ("orb", "humanoid")

_DEFAULT_LOCALE: dict[str, Any] = {
    "home_city": "",
}

_DEFAULT_BRAINS: dict[str, Any] = {
    "local_enabled": True,
    "chat_host": "local",
    "chat_model": "",
    "coding_host": "",
    "coding_model": "",
    "memory_host": "",
    "memory_model": "",
    "image_host": "comfyui",
    "image_model": "",
    "models_dir": "",
    "searxng_url": "",
    "comfyui_url": "",
    "comfyui_root": "",
    "comfyui_enabled": True,
    "comfyui_autostop": True,
    "comfyui_width": 0,
    "comfyui_height": 0,
    "comfyui_steps": 0,
}

_DEFAULT_AUDIO: dict[str, Any] = {
    "input_device": "",
    "output_device": "",
    "output_volume": 80,
    "mic_muted": False,
}

_COMMAND_TIMEOUT_RANGE = (15, 900)
_MAX_ROUNDS_RANGE = (0, 80)

_DEFAULT_AGENT: dict[str, Any] = {
    "command_timeout": COMMAND_TIMEOUT,
    "max_rounds": AGENT_MAX_ROUNDS,
    "tool_approval": False,
}

_LIFE_INTERVAL = (30, 600)
_IDLE_SLEEP_RANGE = (5, 120)

_DEFAULT_LIFE: dict[str, Any] = {
    "enabled": True,
    "interval_sec": 90,
    "sleep_hour": 22,
    "wake_hour": 7,
    "idle_sleep_min": 15,
    "mind_every": 5,
}

_CHAT_HOSTS = {"local", "deepseek", "gemini", "groq"}
_MODEL_ROLES = ("companion", "coding", "memory", "image")
_INHERIT_ROLES = {"coding", "memory"}

_DEFAULT_API_ACCESS: dict[str, Any] = {
    "deepseek_key": "",
    "deepseek_enabled": True,
    "gemini_key": "",
    "gemini_enabled": True,
    "groq_key": "",
    "groq_enabled": True,
    "brave_key": "",
    "brave_enabled": True,
    "huggingface_key": "",
    "huggingface_enabled": True,
}

_DEFAULTS: dict[str, Any] = {
    "speak": True,
    "character_window": dict(_DEFAULT_CHARACTER_WINDOW),
    "audio": dict(_DEFAULT_AUDIO),
    "look": dict(_DEFAULT_LOOK),
    "locale": dict(_DEFAULT_LOCALE),
    "brains": dict(_DEFAULT_BRAINS),
    "agent": dict(_DEFAULT_AGENT),
    "life": dict(_DEFAULT_LIFE),
    "api_access": dict(_DEFAULT_API_ACCESS),
    "app_mode": "developer",
}


def _merge_section(defaults: dict[str, Any], raw: Any) -> dict[str, Any]:
    out = dict(defaults)
    if isinstance(raw, dict):
        out.update(raw)
    return out


def load_settings() -> dict[str, Any]:
    data = dict(_DEFAULTS)
    data["character_window"] = dict(_DEFAULT_CHARACTER_WINDOW)
    data["audio"] = dict(_DEFAULT_AUDIO)
    data["look"] = dict(_DEFAULT_LOOK)
    data["locale"] = dict(_DEFAULT_LOCALE)
    data["brains"] = dict(_DEFAULT_BRAINS)
    data["agent"] = dict(_DEFAULT_AGENT)
    data["life"] = dict(_DEFAULT_LIFE)
    data["api_access"] = dict(_DEFAULT_API_ACCESS)
    try:
        if SETTINGS_PATH.is_file():
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cw = loaded.pop("character_window", None)
                audio = loaded.pop("audio", None)
                look = loaded.pop("look", None)
                locale = loaded.pop("locale", None)
                brains = loaded.pop("brains", None)
                agent = loaded.pop("agent", None)
                life = loaded.pop("life", None)
                api_access = loaded.pop("api_access", None)
                data.update(loaded)
                data["character_window"] = _merge_section(_DEFAULT_CHARACTER_WINDOW, cw)
                data["audio"] = _merge_section(_DEFAULT_AUDIO, audio)
                data["look"] = _merge_section(_DEFAULT_LOOK, look)
                data["locale"] = _merge_section(_DEFAULT_LOCALE, locale)
                data["brains"] = _merge_section(_DEFAULT_BRAINS, brains)
                data["agent"] = _merge_section(_DEFAULT_AGENT, agent)
                data["life"] = _merge_section(_DEFAULT_LIFE, life)
                data["api_access"] = _merge_section(_DEFAULT_API_ACCESS, api_access)
    except (OSError, json.JSONDecodeError):
        pass
    return data


def save_settings(update: dict[str, Any]) -> dict[str, Any]:
    data = load_settings()
    if "character_window" in update and isinstance(update["character_window"], dict):
        data["character_window"] = _merge_section(
            data.get("character_window") or _DEFAULT_CHARACTER_WINDOW,
            update["character_window"],
        )
        update = {k: v for k, v in update.items() if k != "character_window"}
    if "audio" in update and isinstance(update["audio"], dict):
        data["audio"] = _merge_section(data.get("audio") or _DEFAULT_AUDIO, update["audio"])
        update = {k: v for k, v in update.items() if k != "audio"}
    if "look" in update and isinstance(update["look"], dict):
        data["look"] = _merge_section(data.get("look") or _DEFAULT_LOOK, update["look"])
        update = {k: v for k, v in update.items() if k != "look"}
    if "locale" in update and isinstance(update["locale"], dict):
        data["locale"] = _merge_section(data.get("locale") or _DEFAULT_LOCALE, update["locale"])
        update = {k: v for k, v in update.items() if k != "locale"}
    if "brains" in update and isinstance(update["brains"], dict):
        data["brains"] = _merge_section(data.get("brains") or _DEFAULT_BRAINS, update["brains"])
        update = {k: v for k, v in update.items() if k != "brains"}
    if "agent" in update and isinstance(update["agent"], dict):
        data["agent"] = _merge_section(data.get("agent") or _DEFAULT_AGENT, update["agent"])
        update = {k: v for k, v in update.items() if k != "agent"}
    if "life" in update and isinstance(update["life"], dict):
        data["life"] = _merge_section(data.get("life") or _DEFAULT_LIFE, update["life"])
        update = {k: v for k, v in update.items() if k != "life"}
    if "api_access" in update and isinstance(update["api_access"], dict):
        data["api_access"] = _merge_section(
            data.get("api_access") or _DEFAULT_API_ACCESS, update["api_access"]
        )
        update = {k: v for k, v in update.items() if k != "api_access"}
    data.update(update)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data


def character_window(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("character_window") or {}
    cfg = dict(_DEFAULT_CHARACTER_WINDOW)
    if isinstance(raw, dict):
        cfg.update(raw)
    preset = str(cfg.get("size_preset") or "default")
    if preset not in CHARACTER_SIZE_PRESETS:
        preset = "default"
    width, height = CHARACTER_SIZE_PRESETS[preset]
    cfg["size_preset"] = preset
    cfg["width"] = width
    cfg["height"] = height
    cfg["visible"] = bool(cfg.get("visible", True))
    return cfg


def save_character_window(update: dict[str, Any]) -> dict[str, Any]:
    return character_window(save_settings({"character_window": update}))


def audio_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("audio") or {}
    cfg = dict(_DEFAULT_AUDIO)
    if isinstance(raw, dict):
        cfg.update(raw)
    try:
        cfg["output_volume"] = max(0, min(100, int(cfg.get("output_volume", 80))))
    except (TypeError, ValueError):
        cfg["output_volume"] = 80
    cfg["input_device"] = str(cfg.get("input_device") or "")
    cfg["output_device"] = str(cfg.get("output_device") or "")
    cfg["mic_muted"] = bool(cfg.get("mic_muted", False))
    return cfg


def save_audio_settings(update: dict[str, Any]) -> dict[str, Any]:
    return audio_settings(save_settings({"audio": update}))


def look_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("look") or {}
    cfg = dict(_DEFAULT_LOOK)
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["transparent_bg"] = bool(cfg.get("transparent_bg", False))
    cfg["hide_border"] = bool(cfg.get("hide_border", False))
    cfg["hologram"] = bool(cfg.get("hologram", False))
    cfg["body_debug"] = bool(cfg.get("body_debug", False))
    style = str(cfg.get("orb_style") or "orb").strip().lower()
    cfg["orb_style"] = style if style in _ORB_STYLES else "orb"
    return cfg


def save_look_settings(update: dict[str, Any]) -> dict[str, Any]:
    return look_settings(save_settings({"look": update}))


def locale_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("locale") or {}
    cfg = dict(_DEFAULT_LOCALE)
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["home_city"] = str(cfg.get("home_city") or "").strip()
    return cfg


def save_locale_settings(update: dict[str, Any]) -> dict[str, Any]:
    return locale_settings(save_settings({"locale": update}))


def _migrate_chat_host(host: str) -> str:
    text = str(host or "").strip().lower()
    if text in {"ollama", "lmstudio", "lms", "lm studio", "llama", "llamacpp"}:
        return "local"
    if text in _CHAT_HOSTS:
        return text
    return "local"


def brains_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("brains") or {}
    cfg = dict(_DEFAULT_BRAINS)
    if isinstance(raw, dict):
        cfg.update(raw)
    if "local_enabled" not in (raw or {}) and (
        raw.get("ollama_enabled") is False and raw.get("lmstudio_enabled") is False
    ):
        cfg["local_enabled"] = False
    else:
        cfg["local_enabled"] = bool(cfg.get("local_enabled", True))
    cfg["chat_host"] = _migrate_chat_host(str(cfg.get("chat_host") or "local"))
    cfg["chat_model"] = str(cfg.get("chat_model") or "").strip()
    for role in _INHERIT_ROLES:
        role_host = _migrate_chat_host(str(cfg.get(f"{role}_host") or "")) if cfg.get(f"{role}_host") else ""
        role_model = str(cfg.get(f"{role}_model") or "").strip()
        if role_host and role_host not in _CHAT_HOSTS:
            role_host = ""
        if role_host not in _CHAT_HOSTS or not role_model:
            role_host, role_model = "", ""
        cfg[f"{role}_host"] = role_host
        cfg[f"{role}_model"] = role_model
    image_host = str(cfg.get("image_host") or "").strip().lower()
    cfg["image_host"] = "comfyui" if image_host in {"comfyui", "comfy", "ollama", "lmstudio"} else "comfyui"
    cfg["image_model"] = str(cfg.get("image_model") or "").strip()
    cfg["models_dir"] = str(cfg.get("models_dir") or "").strip()
    cfg["searxng_url"] = str(cfg.get("searxng_url") or "").strip()
    cfg["comfyui_url"] = str(cfg.get("comfyui_url") or "").strip()
    cfg["comfyui_root"] = str(cfg.get("comfyui_root") or "").strip()
    cfg["comfyui_enabled"] = bool(cfg.get("comfyui_enabled", True))
    cfg["comfyui_autostop"] = bool(cfg.get("comfyui_autostop", True))
    cfg["comfyui_width"] = _clamp_int(cfg.get("comfyui_width"), 0, 2048, 0)
    cfg["comfyui_height"] = _clamp_int(cfg.get("comfyui_height"), 0, 2048, 0)
    cfg["comfyui_steps"] = _clamp_int(cfg.get("comfyui_steps"), 0, 80, 0)
    return cfg


def save_brains_settings(update: dict[str, Any]) -> dict[str, Any]:
    return brains_settings(save_settings({"brains": update}))


def _clamp_int(value: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, number))


def agent_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("agent") or {}
    cfg = dict(_DEFAULT_AGENT)
    if isinstance(raw, dict):
        cfg.update(raw)
    lo_t, hi_t = _COMMAND_TIMEOUT_RANGE
    lo_r, hi_r = _MAX_ROUNDS_RANGE
    cfg["command_timeout"] = _clamp_int(
        cfg.get("command_timeout"), lo_t, hi_t, COMMAND_TIMEOUT
    )
    cfg["max_rounds"] = _clamp_int(cfg.get("max_rounds"), lo_r, hi_r, AGENT_MAX_ROUNDS)
    cfg["tool_approval"] = bool(cfg.get("tool_approval", False))
    return cfg


def save_agent_settings(update: dict[str, Any]) -> dict[str, Any]:
    cfg = agent_settings()
    cfg.update(update)
    cfg = agent_settings({"agent": cfg})
    save_settings({"agent": cfg})
    return cfg


def life_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("life") or {}
    cfg = dict(_DEFAULT_LIFE)
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["enabled"] = bool(cfg.get("enabled", True))
    lo_i, hi_i = _LIFE_INTERVAL
    lo_idle, hi_idle = _IDLE_SLEEP_RANGE
    cfg["interval_sec"] = _clamp_int(cfg.get("interval_sec"), lo_i, hi_i, 90)
    cfg["idle_sleep_min"] = _clamp_int(cfg.get("idle_sleep_min"), lo_idle, hi_idle, 15)
    cfg["sleep_hour"] = _clamp_int(cfg.get("sleep_hour"), 0, 23, 22)
    cfg["wake_hour"] = _clamp_int(cfg.get("wake_hour"), 0, 23, 7)
    cfg["mind_every"] = _clamp_int(cfg.get("mind_every"), 0, 40, 5)
    return cfg


def save_life_settings(update: dict[str, Any]) -> dict[str, Any]:
    cfg = life_settings()
    cfg.update(update)
    cfg = life_settings({"life": cfg})
    save_settings({"life": cfg})
    return cfg


def enabled_hosts(data: dict[str, Any] | None = None) -> list[str]:
    cfg = brains_settings(data)
    hosts: list[str] = []
    if cfg.get("local_enabled", True):
        hosts.append("local")
    from .api_access import ready_cloud_hosts

    for kind in ready_cloud_hosts(data):
        if kind not in hosts:
            hosts.append(kind)
    return hosts or ["local"]


def resolve_chat_brain(
    session: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Host + model Jinx should actually talk to."""
    from .api_access import CLOUD_HOSTS, default_cloud_model

    cfg = brains_settings(data)
    session = session or {}
    hosts = enabled_hosts(data)
    host = _migrate_chat_host(str(session.get("provider") or cfg.get("chat_host") or "local"))
    if host not in hosts:
        host = hosts[0]
    model = str(session.get("model") or cfg.get("chat_model") or "").strip()
    if not model and host in CLOUD_HOSTS:
        model = default_cloud_model(host)
    return host, model


def normalize_model_role(role: str | None) -> str:
    text = str(role or "companion").strip().lower()
    if text in {"chat", "assistant", "companion"}:
        return "companion"
    if text in {"code", "coder", "developer"}:
        return "coding"
    if text in {"mem", "mem0"}:
        return "memory"
    if text in {"img", "comfy", "comfyui"}:
        return "image"
    return text if text in _MODEL_ROLES else "companion"


def saved_role_brain(
    role: str,
    data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Raw host + model for a role. Empty host/model means inherit Chat."""
    cfg = brains_settings(data)
    role = normalize_model_role(role)
    if role == "companion":
        return str(cfg.get("chat_host") or "local"), str(cfg.get("chat_model") or "")
    if role == "image":
        return str(cfg.get("image_host") or "comfyui"), str(cfg.get("image_model") or "")
    return str(cfg.get(f"{role}_host") or ""), str(cfg.get(f"{role}_model") or "")


def save_role_brain(role: str, host: str, model: str) -> dict[str, Any]:
    role = normalize_model_role(role)
    host = _migrate_chat_host(str(host or "").strip().lower()) if host else ""
    model = str(model or "").strip()
    if role == "companion":
        if host not in _CHAT_HOSTS:
            host = "local"
        return save_brains_settings({"chat_host": host, "chat_model": model})
    if role == "image":
        return save_brains_settings({"image_host": host or "comfyui", "image_model": model})
    if role in _INHERIT_ROLES:
        if host not in _CHAT_HOSTS or not model:
            host, model = "", ""
        return save_brains_settings({f"{role}_host": host, f"{role}_model": model})
    return brains_settings()


def resolve_role_brain(
    role: str,
    session: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Host + model for a job. Coding and Memory fall back to Chat when unset."""
    role = normalize_model_role(role)
    if role == "companion":
        return resolve_chat_brain(session, data)
    if role == "image":
        cfg = brains_settings(data)
        return (
            str(cfg.get("image_host") or "comfyui"),
            str(cfg.get("image_model") or "").strip(),
        )
    host, model = saved_role_brain(role, data)
    if not host or not model:
        return resolve_chat_brain(session, data)
    hosts = enabled_hosts(data)
    if host not in hosts:
        return resolve_chat_brain(session, data)
    return host, model


def catalog_for_roles(
    models_by_host: dict[str, list[str]] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Installed local names plus cloud fallbacks when a key is ready."""
    from .api_access import CLOUD_HOSTS, spec_for

    models_by_host = dict(models_by_host or {})
    catalog: dict[str, list[str]] = {}
    for host in list(enabled_hosts(data)) + ["comfyui"]:
        names = [str(n).strip() for n in (models_by_host.get(host) or []) if str(n).strip()]
        if not names and host in CLOUD_HOSTS:
            spec = spec_for(host)
            names = [str(n) for n in (spec.fallback_models if spec else ()) if n]
        seen: set[str] = set()
        uniq: list[str] = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            uniq.append(name)
        catalog[host] = uniq
    return catalog


def list_role_models(
    models_by_host: dict[str, list[str]] | None = None,
    role: str = "companion",
    data: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    """(host, model) rows for a role selector, grouped in host order."""
    role = normalize_model_role(role)
    catalog = catalog_for_roles(models_by_host, data)
    if role == "image":
        return [("comfyui", name) for name in catalog.get("comfyui") or []]
    if role == "memory":
        return [("local", name) for name in catalog.get("local") or []]
    out: list[tuple[str, str]] = []
    for host in enabled_hosts(data):
        for name in catalog.get(host) or []:
            out.append((host, name))
    return out


def app_mode(data: dict[str, Any] | None = None) -> str:
    raw = str((data or load_settings()).get("app_mode") or "developer").strip().lower()
    return "assistant" if raw == "assistant" else "developer"


def chat_pane_for(mode: str, activity: str) -> tuple[str, bool, bool]:
    """Where the chat pane belongs for this mode and activity tab.

    Returns (activity, chat_full, show_chat). Home and Members are full boards.
    Project explorer (teams) and Files dock chat on the right.
    """
    mode = app_mode({"app_mode": mode})
    activity = str(activity or "home").strip().lower()
    if activity in {"look", "appearance"}:
        activity = "settings"
    if activity == "chat":
        activity = "home"
    if mode == "assistant" and activity in {"files", "git", "db"}:
        activity = "home"
    chat_full = False
    show_chat = activity in {"teams", "files"}
    return activity, chat_full, show_chat
