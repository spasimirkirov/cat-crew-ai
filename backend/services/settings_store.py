"""Simple settings in data/settings.json (does not touch .env)."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from backend.config import AGENT_MAX_ROUNDS, AVATAR_WINDOW_SIZE, BASE_DIR, COMMAND_TIMEOUT
from backend.services.api_access import api_access_defaults

SETTINGS_PATH = BASE_DIR / "data" / "settings.json"

# save_settings is read-merge-write; the API server calls it from request threads.
_save_lock = threading.RLock()

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
    "chat_host": "",  # a cloud host (services/api_access.CLOUD_HOSTS); "" until one is picked
    "chat_model": "",
    "coding_host": "",
    "coding_model": "",
    "memory_host": "",
    "memory_model": "",
    "vision_host": "",
    "vision_model": "",
    "planning_host": "",
    "planning_model": "",
    # Review: the model QA (Aegis) checks the build with. Unset, it follows Coding.
    "review_host": "",
    "review_model": "",
    "image_host": "comfyui",
    "image_model": "",
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

def _chat_hosts() -> tuple[str, ...]:
    """Hosts a chat-type role can run on: every cloud host of the catalog (no local host)."""
    from backend.services.api_access import CLOUD_HOSTS

    return CLOUD_HOSTS


#: Images: a local ComfyUI, an OpenRouter image model, or switched off.
_IMAGE_HOSTS = ("comfyui", "openrouter", "off")
#: Roles the user can switch off outright ("off" host, no model): Images, and Vision for a chat
#: model that cannot see -- browser_look then says so instead of sending a screenshot to it.
_OFF_ROLES = {"image", "vision"}
_MODEL_ROLES = ("companion", "coding", "memory", "vision", "planning", "review", "image")

_DEFAULT_OPENROUTER: dict[str, Any] = {
    # "" = OpenRouter's own default (price-weighted load balancing across providers, with
    # automatic fallback). "price" | "throughput" | "latency" pins one sort and turns load
    # balancing off, per OpenRouter's provider-routing docs.
    "provider_sort": "",
    "allow_fallbacks": True,
    # The "context-compression" plugin (what removes/truncates messages from the middle of a long
    # conversation to fit a small context window) is opt-in: it can drop content, so it is never
    # silently turned on for a long system prompt.
    "compression_enabled": False,
    # Auxiliary/sub-agent routing: lightweight, low-stakes calls (see services/aux_routing.py) may
    # go to a free OpenRouter model instead of the configured Coding brain.
    "free_tier_enabled": False,
    "free_tier_model": "meta-llama/llama-3.3-70b-instruct:free",
}

_PROVIDER_SORTS = ("", "price", "throughput", "latency")


def openrouter_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Savings/performance knobs for the OpenRouter cloud host: provider routing, the optional
    compression plugin, and auxiliary free-tier routing. See ``services/api_access.make_cloud_provider``
    and ``services/aux_routing.py``."""
    raw = (data or load_settings()).get("openrouter") or {}
    cfg = dict(_DEFAULT_OPENROUTER)
    if isinstance(raw, dict):
        cfg.update(raw)
    sort = str(cfg.get("provider_sort") or "").strip().lower()
    cfg["provider_sort"] = sort if sort in _PROVIDER_SORTS else ""
    cfg["allow_fallbacks"] = bool(cfg.get("allow_fallbacks", True))
    cfg["compression_enabled"] = bool(cfg.get("compression_enabled", False))
    cfg["free_tier_enabled"] = bool(cfg.get("free_tier_enabled", False))
    cfg["free_tier_model"] = str(cfg.get("free_tier_model") or "").strip() or _DEFAULT_OPENROUTER["free_tier_model"]
    return cfg


def save_openrouter_settings(update: dict[str, Any]) -> dict[str, Any]:
    cfg = openrouter_settings()
    cfg.update(update)
    cfg = openrouter_settings({"openrouter": cfg})
    save_settings({"openrouter": cfg})
    return cfg


_DEFAULT_JEV: dict[str, Any] = {
    "enabled": False,
    #: Where Jev runs: "openrouter" (OpenRouter's Decisions API, the OpenRouter key) or "typesafe"
    #: (TypeSafe's official API, the TypeSafe key). Keys live in the one key catalog
    #: (services/api_access.py), never here.
    "backend": "openrouter",
    #: How sure Jev must be before it changes anything: skipping a clarify rewrite or a skill
    #: proposal, or (opt-in) blocking a tool call it judges unsafe. Below it, Jev has no say.
    "confidence_threshold": 0.92,
    #: Opt-in, off by default: both measured worse than no gate in the harness evals (a guard that
    #: blocks on doubt blocked every correct edit; dropping tool schemas breaks prompt caching).
    "guard_tool_calls": False,
    "skip_tools_precheck": False,
    #: Optional override key. Leave empty to use the already-configured OpenRouter key (the normal
    #: case) -- write-only past this module; routes never return it (see api/routes/jev.py).
    "api_key": "",
}
# Roles that run on the Chat model until the user picks one of their own.
_INHERIT_ROLES = {"coding", "memory", "vision", "planning", "review"}

_DEFAULT_API_ACCESS: dict[str, Any] = api_access_defaults()

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


def _merge_api_access(base: dict[str, Any], raw: Any) -> dict[str, Any]:
    """Like ``_merge_section`` but only for keys this version knows, so a key that was
    retired (see ``RETIRED_API_KEYS``) is dropped from the file the next time it is written."""
    out = dict(_DEFAULT_API_ACCESS)
    for source in (base, raw):
        if isinstance(source, dict):
            out.update({key: value for key, value in source.items() if key in out})
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
                data["api_access"] = _merge_api_access(_DEFAULT_API_ACCESS, api_access)
    except (OSError, json.JSONDecodeError):
        pass
    return data


# Settings that older versions wrote and nothing reads any more. ``purge_retired_settings``
# removes them from an existing settings file, including any secret stored under them.
RETIRED_API_KEYS = ("huggingface_key", "huggingface_enabled")


def purge_retired_settings() -> bool:
    """Rewrite ``data/settings.json`` without retired keys. Returns True when it changed."""
    with _save_lock:
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        section = raw.get("api_access") if isinstance(raw, dict) else None
        if not isinstance(section, dict) or not any(key in section for key in RETIRED_API_KEYS):
            return False
        _save_settings_locked({})  # loading drops unknown api_access keys, so this rewrite is the purge
        return True


def save_settings(update: dict[str, Any]) -> dict[str, Any]:
    with _save_lock:
        return _save_settings_locked(update)


def _save_settings_locked(update: dict[str, Any]) -> dict[str, Any]:
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
        data["api_access"] = _merge_api_access(data.get("api_access"), update["api_access"])
        update = {k: v for k, v in update.items() if k != "api_access"}
    data.update(update)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace: a reader (or a crash) never sees a half-written file, and
    # a failed parse in load_settings can never make us overwrite real settings
    # with defaults.
    tmp = SETTINGS_PATH.with_name(f"{SETTINGS_PATH.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # holds API keys: owner-only on POSIX (a no-op for the ACLs on Windows)
    except OSError:
        pass
    os.replace(tmp, SETTINGS_PATH)
    return data


def locale_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("locale") or {}
    cfg = dict(_DEFAULT_LOCALE)
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["home_city"] = str(cfg.get("home_city") or "").strip()
    return cfg


def _migrate_chat_host(host: str) -> str:
    """A saved host as it stands now: a cloud host id, or "" -- an old LM Studio / Ollama pick
    included (there is no local model host any more; the start-up check asks for a new pick)."""
    text = str(host or "").strip().lower()
    return text if text in _chat_hosts() else ""


def brains_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (data or load_settings()).get("brains") or {}
    cfg = dict(_DEFAULT_BRAINS)
    if isinstance(raw, dict):
        cfg.update(raw)
    for retired in ("lmstudio_enabled", "lmstudio_url", "lmstudio_api_key", "local_enabled", "ollama_enabled"):
        cfg.pop(retired, None)
    cfg["chat_host"] = _migrate_chat_host(str(cfg.get("chat_host") or ""))
    cfg["chat_model"] = str(cfg.get("chat_model") or "").strip()
    for role in _INHERIT_ROLES:
        if role in _OFF_ROLES and str(cfg.get(f"{role}_host") or "").strip().lower() == "off":
            cfg[f"{role}_host"], cfg[f"{role}_model"] = "off", ""
            continue
        role_host = _migrate_chat_host(str(cfg.get(f"{role}_host") or "")) if cfg.get(f"{role}_host") else ""
        role_model = str(cfg.get(f"{role}_model") or "").strip()
        if role_host not in _chat_hosts() or not role_model:
            role_host, role_model = "", ""
        cfg[f"{role}_host"] = role_host
        cfg[f"{role}_model"] = role_model
    image_host = str(cfg.get("image_host") or "").strip().lower()
    if image_host in {"comfy", "ollama", "lmstudio", "local"}:
        image_host = "comfyui"
    cfg["image_host"] = image_host if image_host in _IMAGE_HOSTS else "comfyui"
    cfg["image_model"] = str(cfg.get("image_model") or "").strip()
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


def _move_legacy_jev_key(raw: dict[str, Any]) -> None:
    """Jev used to keep a key of its own in its settings. Keys now live in one place, the key
    catalog: an OpenRouter key (``sk-or-...``) is already the OpenRouter host's key, so Jev simply
    runs on OpenRouter; any other key is a TypeSafe key and moves to the TypeSafe entry. Either way
    the copy in the Jev settings is erased."""
    from backend.services import api_access

    key = str(raw.get("api_key") or "").strip()
    update: dict[str, Any] = {"api_key": ""}
    if key.startswith("sk-or-"):
        if not api_access.has_api_key("openrouter"):
            api_access.set_api_key("openrouter", key)
        update["backend"] = "openrouter"
    else:
        if not api_access.has_api_key("typesafe"):
            api_access.set_api_key("typesafe", key)
        update["backend"] = "typesafe"
    save_settings({"jev": {**raw, **update}})


def jev_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Jev ("System One"), TypeSafe AI's decision model: off by default; ``backend`` picks where it
    runs (OpenRouter or TypeSafe's official API). It holds no key: the key comes from the key catalog
    (``services/api_access.py``) for the chosen backend. See ``backend/middleware/jev_gatekeeper.py``."""
    raw = (data or load_settings()).get("jev") or {}
    cfg = dict(_DEFAULT_JEV)
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["enabled"] = bool(cfg.get("enabled", False))
    if data is None and isinstance(raw, dict) and str(raw.get("api_key") or "").strip():
        _move_legacy_jev_key(raw)  # once: the old Jev-only key moves into the key catalog
        raw = (load_settings().get("jev") or {})
        cfg.update(raw)
    backend = str(cfg.get("backend") or "").strip().lower()
    cfg["backend"] = "typesafe" if backend == "typesafe" else "openrouter"  # old "local"/"model" -> OpenRouter
    for retired in ("api_key", "local_model", "decider_host", "decider_model"):
        cfg.pop(retired, None)
    # Read-only: the name Jev answers to on the chosen backend (not a setting).
    cfg["model"] = "jev-latest" if cfg["backend"] == "typesafe" else "typesafe/jev-latest"
    cfg["guard_tool_calls"] = bool(cfg.get("guard_tool_calls", False))
    cfg["skip_tools_precheck"] = bool(cfg.get("skip_tools_precheck", False))
    try:
        threshold = float(cfg.get("confidence_threshold"))
    except (TypeError, ValueError):
        threshold = _DEFAULT_JEV["confidence_threshold"]
    cfg["confidence_threshold"] = max(0.0, min(1.0, threshold))
    return cfg


def save_jev_settings(update: dict[str, Any]) -> dict[str, Any]:
    cfg = jev_settings()
    cfg.update(update)
    cfg = jev_settings({"jev": cfg})
    save_settings({"jev": cfg})
    return cfg


def enabled_hosts(data: dict[str, Any] | None = None) -> list[str]:
    """Cloud hosts with a key that are switched on. May be empty: then nothing can run until a key
    is added in Models -> Hosts."""
    from backend.services.api_access import ready_cloud_hosts

    return list(ready_cloud_hosts(data))


def resolve_chat_brain(
    session: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Host + model CatCrewAI should actually talk to.

    The Models page decides. Every session records the model it started on, and that record used
    to win over the Models page for good: a project's chats stayed on a model picked weeks earlier
    (openai/gpt-oss-20b) whatever was chosen since. A session's own host / model counts only when it
    was pinned on purpose (``model_pinned``: the CLI's --host / --model)."""
    from backend.services.api_access import CLOUD_HOSTS, default_cloud_model

    cfg = brains_settings(data)
    session = session if (session or {}).get("model_pinned") else {}
    hosts = enabled_hosts(data)
    host = _migrate_chat_host(str(session.get("provider") or cfg.get("chat_host") or ""))
    if host not in hosts:
        host = hosts[0] if hosts else ""
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
    if text in {"mem", "mem0", "summary", "summarizer"}:
        return "memory"
    if text in {"vlm", "screenshot", "multimodal"}:
        return "vision"
    if text in {"plan", "planner"}:
        return "planning"
    if text in {"reviewer", "verify", "verifier"}:
        return "review"
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
        return str(cfg.get("chat_host") or ""), str(cfg.get("chat_model") or "")
    if role == "image":
        return str(cfg.get("image_host") or "comfyui"), str(cfg.get("image_model") or "")
    return str(cfg.get(f"{role}_host") or ""), str(cfg.get(f"{role}_model") or "")


def save_role_brain(role: str, host: str, model: str) -> dict[str, Any]:
    role = normalize_model_role(role)
    raw_host = str(host or "").strip().lower()
    host = _migrate_chat_host(raw_host) if raw_host else ""
    model = str(model or "").strip()
    if role == "companion":
        if host not in _chat_hosts():
            host = ""
        return save_brains_settings({"chat_host": host, "chat_model": model})
    if role == "image":
        image_host = raw_host if raw_host in _IMAGE_HOSTS else "comfyui"
        return save_brains_settings({"image_host": image_host, "image_model": "" if image_host == "off" else model})
    if role == "vision" and raw_host == "off":
        return save_brains_settings({"vision_host": "off", "vision_model": ""})
    if role in _INHERIT_ROLES:
        if host not in _chat_hosts() or not model:
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


def replace_model_everywhere(old_host: str, old_model: str, new_host: str, new_model: str) -> list[str]:
    """Put ``new_host``/``new_model`` wherever ``old_host``/``old_model`` is picked: the global roles,
    every project's own picks, and OpenRouter's free-tier model for background calls. Returns a
    short line per change. Roles that follow Chat follow it to the new model on their own."""
    old_host, old_model = str(old_host or "").strip().lower(), str(old_model or "").strip()
    new_host, new_model = str(new_host or "").strip().lower(), str(new_model or "").strip()
    changed: list[str] = []
    for role in ("companion", "coding", "memory", "vision", "planning", "review"):
        host, model = saved_role_brain(role)
        if host == old_host and model == old_model:
            save_role_brain(role, new_host, new_model)
            changed.append(f"{'chat' if role == 'companion' else role} (global)")
    with _save_lock:
        data = load_settings()
        table = data.get("project_model_roles")
        table = dict(table) if isinstance(table, dict) else {}
        touched = False
        for project_id, entry in list(table.items()):
            if not isinstance(entry, dict):
                continue
            entry = dict(entry)
            for role, brain in list(entry.items()):
                if isinstance(brain, dict) and str(brain.get("host") or "").lower() == old_host and str(brain.get("model") or "") == old_model:
                    entry[role] = {"host": new_host, "model": new_model}
                    changed.append(f"{'chat' if role == 'companion' else role} (project {project_id})")
                    touched = True
            table[project_id] = entry
        if touched:
            _save_settings_locked({"project_model_roles": table})
    router = openrouter_settings()
    if old_host == "openrouter" and router.get("free_tier_enabled") and router.get("free_tier_model") == old_model:
        if new_host == "openrouter":
            save_openrouter_settings({"free_tier_model": new_model})
            changed.append("OpenRouter free-tier model")
        else:
            save_openrouter_settings({"free_tier_enabled": False})
            changed.append("OpenRouter free-tier routing (turned off)")
    return changed


def project_role_overrides(project_id: str, data: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """The project's own model picks, by settings role (``companion``, ``coding``, ...).

    Only roles the user overrode are present; a missing role means "use the global setting".
    Stored in ``settings.json`` under ``project_model_roles`` -> ``{project_id: {role: {host, model}}}``.
    """
    if not project_id:
        return {}
    table = (data or load_settings()).get("project_model_roles")
    entry = table.get(project_id) if isinstance(table, dict) else None
    if not isinstance(entry, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for role, brain in entry.items():
        if role not in _MODEL_ROLES or not isinstance(brain, dict):
            continue
        host = str(brain.get("host") or "").strip().lower()
        model = str(brain.get("model") or "").strip()
        if (host and host != "inherit" and model) or (role in _OFF_ROLES and host == "off"):
            out[role] = {"host": host, "model": model}
    return out


def save_project_role_overrides(project_id: str, overrides: dict[str, dict[str, str] | None]) -> dict[str, dict[str, str]]:
    """Set or clear per-role overrides for one project and return what is stored.

    ``overrides`` maps a role to ``{host, model}``; ``None``, an empty host or the host
    ``"inherit"`` clears that role (it follows the global setting again). Roles not named
    are left as they are.
    """
    if not project_id:
        return {}
    with _save_lock:
        data = load_settings()
        table = data.get("project_model_roles")
        table = dict(table) if isinstance(table, dict) else {}
        current = dict(project_role_overrides(project_id, data))
        for role, brain in overrides.items():
            role = normalize_model_role(role)
            host = str((brain or {}).get("host") or "").strip().lower()
            model = str((brain or {}).get("model") or "").strip()
            if role in _OFF_ROLES and brain and host == "off":
                current[role] = {"host": "off", "model": ""}
            elif brain and host and host != "inherit" and model:
                if role == "image":
                    host = host if host in {"comfyui", "openrouter"} else "comfyui"
                else:
                    host = _migrate_chat_host(host)
                if host:
                    current[role] = {"host": host, "model": model}
                else:
                    current.pop(role, None)
            else:
                current.pop(role, None)
        if current:
            table[project_id] = current
        else:
            table.pop(project_id, None)
        _save_settings_locked({"project_model_roles": table})
    return current


def clear_project_role_overrides(project_id: str) -> None:
    """Forget every override of a project (called when the project is deleted)."""
    save_project_role_overrides(project_id, {role: None for role in _MODEL_ROLES})


def project_lm_model(project_id: str, data: dict[str, Any] | None = None) -> tuple[str, str]:
    """Per-project chat model override for the Cat Crew header dropdown.

    Empty return means "no override" — every specialist bot in that room
    follows the normal Chat role resolution. Set by ``save_project_lm_model``
    when the user picks a model from the Cat Crew header; takes effect
    on the next turn for every active agent, no restart required.
    """
    if not project_id:
        return "", ""
    raw = (data or load_settings()).get("project_models") or {}
    entry = raw.get(project_id) if isinstance(raw, dict) else None
    if not isinstance(entry, dict):
        return "", ""
    return str(entry.get("host") or "").strip(), str(entry.get("model") or "").strip()


def save_project_lm_model(project_id: str, host: str, model: str) -> dict[str, str]:
    """Save (or clear, when host/model are empty) the Cat Crew model override."""
    if not project_id:
        return {"host": "", "model": ""}
    data = load_settings()
    raw = data.get("project_models")
    table: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    host = str(host or "").strip()
    model = str(model or "").strip()
    if host and model:
        table[project_id] = {"host": host, "model": model}
    else:
        table.pop(project_id, None)
    save_settings({"project_models": table})
    return {"host": host, "model": model}


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
