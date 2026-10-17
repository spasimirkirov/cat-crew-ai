"""Probe local llama-server + GGUF folder, and cloud OpenAI-compatible hosts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from .config import LLAMA_SERVER_URL
from .local_models import models_dir, scan_gguf

LOCAL_HOSTS = ("local",)
_TIMEOUT = 1.8

_CAP_ALIASES = {
    "completion": "chat",
    "chat": "chat",
    "tools": "tools",
    "tool": "tools",
    "trained_for_tool_use": "tools",
    "vision": "vision",
    "embedding": "embed",
    "embed": "embed",
    "thinking": "think",
    "image": "image",
}

_VISION_HINTS = (
    "llava",
    "vision",
    "minicpm-v",
    "moondream",
    "qwen2.5-vl",
    "qwen2-vl",
    "qwen-vl",
    "pixtral",
    "gemma3",
    "gemma4",
)
_IMAGE_HINTS = ("flux", "sdxl", "stable-diffusion", "sd3", "imagegen", "janus")


@dataclass
class LmModel:
    name: str
    size_bytes: int = 0
    loaded: bool = False
    capabilities: list[str] = field(default_factory=list)
    family: str = ""
    extra: str = ""


@dataclass
class HostSnapshot:
    kind: str
    online: bool = False
    error: str = ""
    models: list[LmModel] = field(default_factory=list)
    loaded_names: list[str] = field(default_factory=list)


def normalize_host(kind: str | None) -> str:
    text = str(kind or "").strip().lower()
    if text in {"ollama", "lmstudio", "lm studio", "lms", "llama", "llamacpp", "local"}:
        return "local"
    if text in {"deepseek", "gemini", "groq"}:
        return text
    if text in {"comfyui", "comfy"}:
        return "comfyui"
    return "local"


def host_label(kind: str) -> str:
    text = str(kind or "").strip().lower()
    labels = {
        "local": "Local",
        "llama": "Local",
        "ollama": "Local",
        "lmstudio": "Local",
        "comfyui": "ComfyUI",
        "searxng": "SearXNG",
        "deepseek": "DeepSeek",
        "gemini": "Google Gemini",
        "groq": "Groq",
    }
    return labels.get(text, "Local")


def format_size(n: int) -> str:
    try:
        size = int(n)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    for unit, div in (("GB", 1_000_000_000), ("MB", 1_000_000), ("KB", 1_000)):
        if size >= div:
            value = size / div
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{size} B"


def normalize_capabilities(raw: Any) -> list[str]:
    found: list[str] = []
    if isinstance(raw, dict):
        for key, val in raw.items():
            if val:
                found.append(str(key))
    elif isinstance(raw, (list, tuple, set)):
        found = [str(item) for item in raw if item]
    elif isinstance(raw, str) and raw.strip():
        found = [raw]
    out: list[str] = []
    for item in found:
        mapped = _CAP_ALIASES.get(item.lower().replace(" ", "_"), "")
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def infer_capabilities(name: str, explicit: Any = None) -> list[str]:
    caps = normalize_capabilities(explicit)
    low = (name or "").lower()
    if "vision" not in caps and any(hint in low for hint in _VISION_HINTS):
        caps.append("vision")
    if "vl" in low.split(":")[0].replace("_", "-").split("-") and "vision" not in caps:
        caps.append("vision")
    if "image" not in caps and any(hint in low for hint in _IMAGE_HINTS):
        caps.append("image")
    if "image" in caps:
        return caps
    if "tools" not in caps and "chat" not in caps:
        caps.append("chat")
        caps.append("tools")
    return caps


def format_capabilities(caps: list[str] | None) -> str:
    order = ("tools", "vision", "think", "embed", "image", "chat")
    seen = {str(item) for item in (caps or [])}
    return " · ".join(item for item in order if item in seen)


def loaded_model_note(host: str, model: str, caps: list[str] | None) -> str:
    name = (model or "").strip() or "unknown"
    label = host_label(host)
    bits = format_capabilities(caps)
    parts = [f"Loaded chat model: {name} via {label}."]
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
            "The weights support vision; Jinx does not attach images to chat yet."
        )
    else:
        parts.append("This model does not report vision.")
    parts.append(
        "Image generation is a separate GPU slot — do not unload this chat model to draw."
    )
    return " ".join(parts)


_SKIP_CLOUD_MODELS = (
    "embed",
    "embedding",
    "tts",
    "whisper",
    "dall-e",
    "moderation",
)


def parse_openai_models(
    payload: Any,
    *,
    fallback: tuple[str, ...] = (),
    allow: str = "",
) -> list[LmModel]:
    rows: list[Any] = []
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
    elif isinstance(payload, list):
        rows = payload
    names: list[str] = []
    for row in rows:
        if isinstance(row, str):
            name = row.strip()
        elif isinstance(row, dict):
            name = str(row.get("id") or row.get("name") or "").strip()
        else:
            continue
        if not name:
            continue
        if name.startswith("models/"):
            name = name[len("models/") :]
        low = name.lower()
        if any(token in low for token in _SKIP_CLOUD_MODELS):
            continue
        if allow and allow not in low:
            continue
        if name not in names:
            names.append(name)
    if not names:
        names = [item for item in fallback if item]
    return [
        LmModel(name=name, loaded=True, capabilities=infer_capabilities(name, ["tools", "chat"]))
        for name in names[:24]
    ]


def probe_cloud(kind: str) -> HostSnapshot:
    from .api_access import compat_root, default_cloud_model, resolved_key, spec_for

    spec = spec_for(kind)
    snap = HostSnapshot(kind=kind)
    if spec is None:
        snap.error = "unknown host"
        return snap
    key = resolved_key(kind)
    if not key:
        snap.error = "no API key"
        return snap
    fallback = spec.fallback_models or ((spec.default_model,) if spec.default_model else ())
    allow = "gemini" if kind == "gemini" else ""
    try:
        response = requests.get(
            f"{compat_root(spec.base_url)}/models",
            timeout=4.5,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        snap.online = True
        snap.models = parse_openai_models(response.json(), fallback=fallback, allow=allow)
    except Exception as exc:  # noqa: BLE001
        snap.error = str(exc).splitlines()[0][:72]
        snap.models = parse_openai_models({}, fallback=fallback, allow=allow)
    if not snap.models and default_cloud_model(kind):
        name = default_cloud_model(kind)
        snap.models = [
            LmModel(name=name, loaded=True, capabilities=["tools", "chat"])
        ]
        snap.online = True
    snap.loaded_names = [item.name for item in snap.models[:3]]
    return snap


def _name_matches_loaded(name: str, loaded: list[str]) -> bool:
    key = Path(name).stem.lower()
    if not key:
        return False
    return any(Path(item).stem.lower() == key for item in loaded)


def probe_local() -> HostSnapshot:
    from .llama_server import list_loaded, ping_server

    snap = HostSnapshot(kind="local")
    installed = scan_gguf()
    loaded: list[str] = []
    if ping_server():
        snap.online = True
        loaded = list_loaded()
        snap.loaded_names = loaded
    else:
        snap.error = "offline"
    for item in installed:
        caps = infer_capabilities(item.name, item.capabilities)
        if item.mmproj and "vision" not in caps:
            caps.append("vision")
        extra = item.quant
        if item.mmproj:
            extra = (extra + " · mmproj").strip(" ·")
        snap.models.append(
            LmModel(
                name=item.name,
                size_bytes=item.size_bytes,
                loaded=_name_matches_loaded(item.name, loaded),
                capabilities=caps,
                family=item.family,
                extra=extra,
            )
        )
    return snap


def probe_host(kind: str) -> HostSnapshot:
    if normalize_host(kind) == "local":
        return probe_local()
    from .api_access import is_cloud_host

    if is_cloud_host(kind):
        return probe_cloud(kind)
    return probe_local()


def probe_all() -> dict[str, HostSnapshot]:
    from .api_access import CLOUD_HOSTS, has_api_key

    snaps = {"local": probe_local()}
    for kind in CLOUD_HOSTS:
        if has_api_key(kind):
            snaps[kind] = probe_cloud(kind)
    return snaps


def launch_host(kind: str = "local") -> str:
    del kind
    from .llama_server import launch_server

    return launch_server()


def stop_local() -> str:
    from .llama_server import stop_server

    return stop_server()


def load_model(kind: str, name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("No model name")
    from .api_access import is_cloud_host

    if is_cloud_host(kind):
        return f"{name} is a cloud model — nothing to load locally"
    from .llama_server import load_slot

    return load_slot(name)


def unload_model(kind: str, name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("No model name")
    from .llama_server import unload_slot

    return unload_slot(name)


def delete_model(kind: str, name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("No model name")
    from .api_access import is_cloud_host

    if is_cloud_host(kind) or normalize_host(kind) == "comfyui":
        raise RuntimeError(f"{host_label(kind)} models are not deleted from this PC this way.")
    from .llama_server import ping_server, stop_server
    from .local_models import sidecar_path

    folder = models_dir()
    gguf = folder / f"{name}.gguf"
    if not gguf.is_file():
        matches = list(folder.glob(f"{name}*.gguf"))
        gguf = matches[0] if matches else gguf
    if not gguf.is_file():
        raise FileNotFoundError(f"No GGUF named {name} in {folder}")
    if ping_server():
        try:
            stop_server()
        except Exception:  # noqa: BLE001
            pass
    sidecar = sidecar_path(gguf)
    try:
        gguf.unlink()
    except OSError as exc:
        raise RuntimeError(f"Could not delete {gguf.name}") from exc
    sidecar.unlink(missing_ok=True)
    return f"Deleted {gguf.name}"


def parse_pull_progress(data: dict[str, Any] | None) -> dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    status = str(payload.get("status") or "").strip()
    error = str(payload.get("error") or "").strip()
    try:
        total = int(payload.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    try:
        completed = int(payload.get("completed") or 0)
    except (TypeError, ValueError):
        completed = 0
    if total > 0:
        percent = max(0, min(100, int(round(100 * completed / total))))
    elif str(payload.get("percent") or ""):
        try:
            percent = int(payload.get("percent") or 0)
        except (TypeError, ValueError):
            percent = 0
    else:
        percent = 0
    bits = [status or "working"]
    if total > 0:
        bits.append(f"{format_size(completed)} / {format_size(total)}")
        bits.append(f"{percent}%")
    return {
        "status": status,
        "error": error,
        "completed": completed,
        "total": total,
        "percent": percent,
        "indeterminate": bool(payload.get("indeterminate"))
        if "indeterminate" in payload
        else (total <= 0),
        "text": str(payload.get("text") or " · ".join(bit for bit in bits if bit)),
    }


def gguf_stem(name: str) -> str:
    text = (name or "").strip()
    if "#" in text:
        text = text.split("#", 1)[1]
    stem = Path(text).name
    if stem.lower().endswith(".part"):
        stem = stem[:-5]
    if stem.lower().endswith(".gguf"):
        stem = stem[:-5]
    return stem


def model_key(name: str) -> str:
    return gguf_stem(name).lower()


def model_names_match(left: str, right: str) -> bool:
    a = model_key(left)
    b = model_key(right)
    return bool(a and b and a == b)


def snapshot_has_model(snap: HostSnapshot, name: str) -> bool:
    return any(model_names_match(item.name, name) for item in snap.models)


def inject_pulled_model(snap: HostSnapshot, name: str) -> bool:
    text = gguf_stem(name)
    if not text or snapshot_has_model(snap, text):
        return False
    snap.models.insert(0, LmModel(name=text, extra="just pulled"))
    return True


def enqueue_download(current: str, queued: list[str], name: str) -> tuple[str, list[str]]:
    text = (name or "").strip()
    if not text:
        return "empty", list(queued)
    if model_names_match(current, text):
        return "duplicate", list(queued)
    if any(model_names_match(item, text) for item in queued):
        return "duplicate", list(queued)
    if (current or "").strip():
        return "queued", [*queued, text]
    return "start", list(queued)


def dequeue_download(queued: list[str], name: str) -> list[str]:
    return [item for item in queued if not model_names_match(item, name)]


def pull_model_stream(
    name: str,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    abort: Callable[[], bool] | None = None,
) -> str:
    """Download `repo` or `repo#filename` from Hugging Face into the models folder."""
    from .downloads import discard_incomplete, looks_like_hf_spec, resolve_download_spec
    from .gguf_browser import HfFile, download_gguf, list_repo_files, matching_mmproj, pick_quant

    text = resolve_download_spec(name)
    if not text:
        raise ValueError("No model name")
    if not looks_like_hf_spec(text) and "#" not in text:
        raise RuntimeError(
            f"Don't know which Hugging Face repo {text} came from. "
            "Open AI Models → Hugging Face Hub and Pull it again."
        )
    discard_incomplete(text)
    repo, _, rest = text.partition("#")
    repo = repo.strip()
    filename = rest.strip()
    files = list_repo_files(repo)
    if filename:
        want = Path(filename).name.lower()
        chosen = next(
            (item for item in files if Path(item.filename).name.lower() == want),
            None,
        )
        if chosen is None:
            chosen = HfFile(repo=repo, filename=filename)
    else:
        chosen = pick_quant(files)
    if chosen is None:
        raise RuntimeError(f"No GGUF files in {repo}")
    filename = chosen.filename
    mate = matching_mmproj(files, chosen)
    mmproj = mate.filename if mate else ""
    download_gguf(repo, filename, on_progress=on_progress, abort=abort, mmproj=mmproj)
    return f"Downloaded {Path(filename).name} from {repo}"
