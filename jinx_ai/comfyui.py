"""Local ComfyUI / Comfy Desktop — separate process, never unloads the chat LLM."""

from __future__ import annotations

import ctypes
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .config import BASE_DIR, COMFYUI_ROOT, COMFYUI_URL
from .lm_runtime import HostSnapshot, LmModel
from .settings_store import brains_settings

GENERATED_DIR = BASE_DIR / "data" / "generated"
_TIMEOUT = 2.0
_GEN_TIMEOUT = 180
_WORKFLOW_TIMEOUT = 420
_SEED_WIDGETS = {"randomize", "fixed", "increment", "decrement"}
_SPECIAL = ("flux", "sd3", "hunyuan", "wan", "ltx", "hidream", "qwen-image")
_REMBG_NODES = (
    "ImageRemoveBackgroundRMBG",
    "easy imageRemBg",
    "RembgSession+",
    "BRIA RMBG",
    "Image Rembg",
)
_MODEL_LOADERS = (
    ("checkpoints", "CheckpointLoaderSimple", "ckpt_name"),
    ("loras", "LoraLoader", "lora_name"),
    ("vae", "VAELoader", "vae_name"),
    ("upscale", "UpscaleModelLoader", "model_name"),
    ("controlnet", "ControlNetLoader", "control_net_name"),
    ("unet", "UNETLoader", "unet_name"),
    ("clip", "CLIPLoader", "clip_name"),
)
_DISK_FOLDERS = {
    "checkpoints": "checkpoints",
    "loras": "loras",
    "vae": "vae",
    "upscale": "upscale_models",
    "controlnet": "controlnet",
    "unet": "unet",
    "diffusion_models": "diffusion_models",
    "clip": "clip",
    "text_encoders": "text_encoders",
}
_EXTRA_FOLDERS = (
    "checkpoints",
    "loras",
    "vae",
    "text_encoders",
    "clip",
    "unet",
    "diffusion_models",
    "controlnet",
    "upscale_models",
    "embeddings",
    "clip_vision",
    "hypernetworks",
    "photomaker",
    "style_models",
    "vae_approx",
    "gligen",
    "animatediff_models",
    "ipadapter",
)
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_START_TIMEOUT = 120.0

_origin_cache = ""
_origin_at = 0.0
_origin_online = False
_NO_PROXY = {"http": None, "https": None}
_http = requests.Session()
_http.trust_env = False
_http.proxies = {}


def _http_get(url: str, **kwargs):
    kwargs.setdefault("proxies", {})
    return _http.get(url, **kwargs)


def _http_post(url: str, **kwargs):
    kwargs.setdefault("proxies", {})
    return _http.post(url, **kwargs)


def desktop_exe() -> Path | None:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    for path in (
        Path(r"C:\Program Files\Comfy Desktop\Comfy Desktop.exe"),
        Path(r"C:\Program Files\ComfyUI\ComfyUI.exe"),
        local / "Programs" / "Comfy Desktop" / "Comfy Desktop.exe",
        local / "Programs" / "ComfyUI" / "ComfyUI.exe",
    ):
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def desktop_settings() -> dict[str, Any]:
    path = Path(os.environ.get("APPDATA", "")) / "Comfy Desktop" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def shared_root() -> Path | None:
    cfg = desktop_settings()
    models = cfg.get("modelsDirs")
    if isinstance(models, list) and models:
        try:
            path = Path(str(models[0])).expanduser()
            if path.is_dir():
                return path
        except (OSError, TypeError):
            pass
    fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" / "ComfyUI-Shared" / "models"
    return fallback if fallback.is_dir() else None


def install_root() -> Path | None:
    brains = brains_settings()
    configured = Path(str(brains.get("comfyui_root") or COMFYUI_ROOT or "")).expanduser()
    if configured.is_dir() and (configured / "main.py").is_file():
        return configured
    cfg = desktop_settings()
    install_dir = Path(str(cfg.get("installDir") or "")).expanduser()
    nested = install_dir / "ComfyUI" / "ComfyUI"
    if (nested / "main.py").is_file():
        return nested
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" / "ComfyUI-Installs" / "ComfyUI" / "ComfyUI"
    if (local / "main.py").is_file():
        return local
    return None


def template_dir() -> Path | None:
    root = install_root()
    if root is None:
        return None
    packaged = (
        root
        / ".venv"
        / "Lib"
        / "site-packages"
        / "comfyui_workflow_templates_json"
        / "templates"
    )
    return packaged if packaged.is_dir() else None


def user_workflows_dir() -> Path | None:
    root = install_root()
    if root is None:
        return None
    folder = root / "user" / "default" / "workflows"
    return folder if folder.is_dir() else None


def comfy_display_origin() -> str:
    """Last known URL. Never touches the network (safe on the UI thread)."""
    if _origin_cache:
        return _origin_cache
    brains = brains_settings()
    configured = str(brains.get("comfyui_url") or "").strip().rstrip("/")
    return configured or (COMFYUI_URL or "http://127.0.0.1:8188").rstrip("/")


def comfy_origin(url: str = "", *, refresh: bool = False) -> str:
    global _origin_cache, _origin_at, _origin_online
    brains = brains_settings()
    configured = (url or str(brains.get("comfyui_url") or "") or "").rstrip("/")
    now = time.monotonic()
    if not refresh and _origin_cache and now - _origin_at < 8:
        return _origin_cache
    default = (COMFYUI_URL or "http://127.0.0.1:8188").rstrip("/")
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    if default not in candidates:
        candidates.append(default)
    fallback = configured or default
    for origin in candidates:
        if _tcp_state(origin) != "open":
            continue
        if _ping(origin):
            _origin_cache = origin
            _origin_at = now
            _origin_online = True
            return origin
    extra = "http://127.0.0.1:8000"
    if extra not in candidates and _tcp_state(extra) == "open" and _ping(extra):
        _origin_cache = extra
        _origin_at = now
        _origin_online = True
        return extra
    _origin_cache = fallback
    _origin_at = now
    _origin_online = False
    return fallback


def _tcp_state(origin: str, timeout: float = 0.12) -> str:
    parsed = urlparse(origin)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return "open"
    except TimeoutError:
        return "timeout"
    except OSError:
        return "refused"


def _ping(origin: str) -> bool:
    if not origin:
        return False
    for path in ("/system_stats", "/queue"):
        try:
            response = _http_get(f"{origin}{path}", timeout=(0.2, 0.5))
            if response.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def ping_comfyui(url: str = "") -> bool:
    global _origin_online
    if url:
        ok = _ping(url.rstrip("/"))
        if ok:
            _origin_online = True
        return ok
    comfy_origin(refresh=True)
    return _origin_online


def _forget_origin() -> None:
    global _origin_cache, _origin_at, _origin_online
    _origin_cache = ""
    _origin_at = 0.0
    _origin_online = False


def _state_path() -> Path:
    return BASE_DIR / "data" / "comfy.pid"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _clear_state() -> None:
    path = _state_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_image(pid: int) -> str:
    if pid <= 0 or sys.platform != "win32":
        return ""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint(32768)
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint),
        ]
        query.restype = ctypes.c_int
        if query(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _pid_is_ours(pid: int, python_exe: str = "") -> bool:
    if not _pid_alive(pid):
        return False
    expected = str(python_exe or "").strip()
    if not expected:
        return False
    image = _process_image(pid)
    if not image:
        return True
    try:
        return Path(image).resolve() == Path(expected).resolve()
    except OSError:
        return Path(image).name.lower() == Path(expected).name.lower()


def owned_by_jinx() -> bool:
    state = _load_state()
    pid = int(state.get("pid") or 0)
    if not pid:
        return False
    return _pid_is_ours(pid, str(state.get("python") or ""))


def mark_comfy_sticky() -> None:
    state = _load_state()
    if not state.get("pid"):
        return
    state["sticky"] = True
    _save_state(state)


def _require_online(*, start: bool = True) -> str:
    if start:
        return ensure_comfyui()
    origin = comfy_origin(refresh=True)
    if not _ping(origin):
        raise RuntimeError(
            "ComfyUI is not running. Jinx starts a hidden ComfyUI when she generates; "
            f"or click Launch on AI Models → ComfyUI ({origin}). Chat model stays loaded."
        )
    return origin


def _get_json(origin: str, path: str, timeout: float = 8) -> Any:
    last: Exception | None = None
    for prefix in ("", "/api"):
        try:
            response = _http_get(f"{origin}{prefix}{path}", timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
    if last is not None:
        raise last
    return {}


def object_info(origin: str = "", node: str = "") -> dict[str, Any]:
    root = origin or comfy_origin()
    path = f"/object_info/{node}" if node else "/object_info"
    data = _get_json(root, path, timeout=20 if not node else 8)
    return data if isinstance(data, dict) else {}


def _combo(node: dict[str, Any], key: str) -> list[str]:
    required = ((node.get("input") or {}).get("required") or {})
    optional = ((node.get("input") or {}).get("optional") or {})
    spec = required.get(key) or optional.get(key)
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return [str(item) for item in spec[0] if item]
    if isinstance(spec, list):
        return [str(item) for item in spec if isinstance(item, str)]
    return []


def list_checkpoints(url: str = "") -> list[str]:
    origin = url or comfy_origin()
    try:
        data = object_info(origin, "CheckpointLoaderSimple")
    except Exception:  # noqa: BLE001
        return list_models_on_disk("checkpoints")
    node = data.get("CheckpointLoaderSimple") if isinstance(data, dict) else None
    if not isinstance(node, dict):
        node = data if isinstance(data, dict) else {}
    names = _combo(node, "ckpt_name")
    return names or list_models_on_disk("checkpoints")


def list_models_on_disk(kind: str) -> list[str]:
    folder_name = _DISK_FOLDERS.get(kind, kind)
    root = shared_root()
    if root is None:
        return []
    folder = root / folder_name
    if not folder.is_dir():
        return []
    names: list[str] = []
    try:
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() in {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".sft"}:
                names.append(str(path.relative_to(folder)).replace("\\", "/"))
    except OSError:
        return []
    return names


def list_assets(kind: str, query: str = "", origin: str = "") -> list[str]:
    needle = (query or "").strip().lower()
    kind = (kind or "checkpoints").strip().lower()
    names: list[str] = []
    if kind in {"nodes", "node"}:
        try:
            info = object_info(origin or comfy_origin())
            names = sorted(str(key) for key in info)
        except Exception:  # noqa: BLE001
            names = []
    elif kind in {"templates", "template"}:
        folder = template_dir()
        if folder is not None:
            names = sorted(path.stem for path in folder.glob("*.json"))
    elif kind in {"workflows", "workflow"}:
        folder = user_workflows_dir()
        if folder is not None:
            names = sorted(
                str(path.relative_to(folder)).replace("\\", "/")
                for path in folder.rglob("*.json")
            )
    else:
        mapped = {row[0]: (row[1], row[2]) for row in _MODEL_LOADERS}
        if kind in mapped and ping_comfyui(origin):
            node_name, key = mapped[kind]
            try:
                data = object_info(origin or comfy_origin(), node_name)
                node = data.get(node_name) if isinstance(data, dict) else data
                names = _combo(node if isinstance(node, dict) else {}, key)
            except Exception:  # noqa: BLE001
                names = []
        if not names:
            names = list_models_on_disk(kind if kind != "upscale" else "upscale")
    if needle:
        names = [item for item in names if needle in item.lower()]
    return names[:80]


def node_classes(origin: str = "") -> set[str]:
    try:
        info = object_info(origin or comfy_origin())
    except Exception:  # noqa: BLE001
        return set()
    return {str(key) for key in info}


def rembg_node(origin: str = "") -> str:
    available = node_classes(origin)
    for name in _REMBG_NODES:
        if name in available:
            return name
    for name in sorted(available):
        low = name.lower()
        if "rembg" in low or "rmbg" in low or "removebackground" in low.replace(" ", ""):
            return name
    return ""


def probe_comfyui() -> HostSnapshot:
    snap = HostSnapshot(kind="comfyui")
    origin = comfy_origin(refresh=True)
    online = _ping(origin)
    snap.online = online
    names = []
    if online:
        try:
            names = list_checkpoints(origin)
        except Exception as exc:  # noqa: BLE001
            snap.error = str(exc)
            names = list_models_on_disk("checkpoints")
    else:
        names = list_models_on_disk("checkpoints")
        snap.error = f"offline ({origin})"
    snap.models = [
        LmModel(name=name, capabilities=["image"], extra="checkpoint") for name in names
    ]
    return snap


def sampler_for_checkpoint(name: str) -> dict[str, Any]:
    low = (name or "").lower()
    if "turbo" in low or "lcm" in low:
        return {
            "width": 768,
            "height": 768,
            "steps": 6,
            "cfg": 1.4,
            "sampler_name": "euler",
            "scheduler": "simple",
        }
    if "xl" in low or "sdxl" in low:
        return {
            "width": 1024,
            "height": 1024,
            "steps": 20,
            "cfg": 5.5,
            "sampler_name": "euler",
            "scheduler": "normal",
        }
    return {
        "width": 768,
        "height": 768,
        "steps": 22,
        "cfg": 6.5,
        "sampler_name": "euler",
        "scheduler": "normal",
    }


def txt2img_workflow(
    ckpt: str,
    prompt: str,
    negative: str = "",
    seed: int = -1,
    *,
    width: int = 0,
    height: int = 0,
    steps: int = 0,
) -> dict[str, Any]:
    opts = sampler_for_checkpoint(ckpt)
    if seed < 0:
        seed = random.randint(0, 2**32 - 1)
    neg = negative or "blurry, low quality, watermark, text"
    w = int(width or opts["width"])
    h = int(height or opts["height"])
    n_steps = int(steps or opts["steps"])
    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": w, "height": h, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": neg, "clip": ["4", 1]},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(seed),
                "steps": n_steps,
                "cfg": float(opts["cfg"]),
                "sampler_name": opts["sampler_name"],
                "scheduler": opts["scheduler"],
                "denoise": 1,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "jinx", "images": ["8", 0]},
        },
    }


def img2img_workflow(
    ckpt: str,
    prompt: str,
    image_name: str,
    negative: str = "",
    seed: int = -1,
    denoise: float = 0.55,
    *,
    steps: int = 0,
) -> dict[str, Any]:
    opts = sampler_for_checkpoint(ckpt)
    if seed < 0:
        seed = random.randint(0, 2**32 - 1)
    graph = txt2img_workflow(ckpt, prompt, negative, seed, steps=steps)
    graph["10"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
    }
    graph["11"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["10", 0], "vae": ["4", 2]},
    }
    graph["3"]["inputs"]["latent_image"] = ["11", 0]
    graph["3"]["inputs"]["denoise"] = max(0.05, min(1.0, float(denoise)))
    return graph


def upscale_workflow(image_name: str, scale: float = 2.0, model: str = "") -> dict[str, Any]:
    graph: dict[str, Any] = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
    }
    if model:
        graph["2"] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": model},
        }
        graph["3"] = {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]},
        }
        last = "3"
    else:
        factor = max(1.0, min(4.0, float(scale) or 2.0))
        graph["2"] = {
            "class_type": "ImageScaleBy",
            "inputs": {
                "image": ["1", 0],
                "upscale_method": "lanczos",
                "scale_by": factor,
            },
        }
        last = "2"
    graph["9"] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "jinx-upscale", "images": [last, 0]},
    }
    return graph


def rembg_workflow(image_name: str, node: str) -> dict[str, Any]:
    graph: dict[str, Any] = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": node, "inputs": {"image": ["1", 0]}},
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "jinx-rembg", "images": ["2", 0]},
        },
    }
    return graph


def ui_to_api(workflow: dict[str, Any], info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert a ComfyUI canvas export to API prompt format when possible."""
    if not isinstance(workflow, dict):
        raise ValueError("Workflow must be a JSON object")
    if "prompt" in workflow and isinstance(workflow["prompt"], dict) and "nodes" not in workflow:
        return workflow["prompt"]
    if "nodes" not in workflow:
        return {str(key): value for key, value in workflow.items() if isinstance(value, dict) and "class_type" in value}

    links: dict[int, list[Any]] = {}
    for link in workflow.get("links") or []:
        if isinstance(link, list) and len(link) >= 5:
            links[int(link[0])] = link

    prompt: dict[str, Any] = {}
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if int(node.get("mode") or 0) == 4:
            continue
        class_type = str(node.get("type") or "")
        if not class_type or class_type in {"Note", "MarkdownNote"}:
            continue
        nid = str(node.get("id"))
        inputs: dict[str, Any] = {}
        for inp in node.get("inputs") or []:
            if not isinstance(inp, dict):
                continue
            name = str(inp.get("name") or "")
            link = inp.get("link")
            if name and link is not None and int(link) in links:
                src = links[int(link)]
                inputs[name] = [str(src[1]), int(src[2])]
        widgets = list(node.get("widgets_values") or [])
        widget_i = 0
        spec = ((info or {}).get(class_type) or {}).get("input") or {}
        ordered: list[str] = []
        for group in ("required", "optional"):
            block = spec.get(group) or {}
            if isinstance(block, dict):
                ordered.extend(str(key) for key in block)
        if not ordered:
            ordered = [str(inp.get("name")) for inp in (node.get("inputs") or []) if isinstance(inp, dict)]
        for name in ordered:
            if name in inputs:
                continue
            if widget_i >= len(widgets):
                break
            value = widgets[widget_i]
            widget_i += 1
            if isinstance(value, str) and value in _SEED_WIDGETS:
                if widget_i < len(widgets):
                    value = widgets[widget_i]
                    widget_i += 1
                continue
            inputs[name] = value
        prompt[nid] = {"class_type": class_type, "inputs": inputs}
    if not prompt:
        raise ValueError("No runnable nodes in that workflow")
    return prompt


def apply_overrides(graph: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return graph
    out = json.loads(json.dumps(graph))
    for key, value in params.items():
        if "." in str(key):
            nid, field = str(key).split(".", 1)
            node = out.get(nid)
            if isinstance(node, dict):
                inputs = node.setdefault("inputs", {})
                if isinstance(inputs, dict):
                    inputs[field] = value
            continue
        node = out.get(str(key))
        if isinstance(node, dict) and isinstance(value, dict):
            inputs = node.setdefault("inputs", {})
            if isinstance(inputs, dict):
                inputs.update(value)
    return out


def _download_output(origin: str, image: dict[str, Any], dest: Path) -> Path:
    params = {
        "filename": image.get("filename"),
        "subfolder": image.get("subfolder") or "",
        "type": image.get("type") or "output",
    }
    response = _http_get(f"{origin}/view", params=params, timeout=60)
    response.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(response.content)
    return dest


def _collect_media(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for node in outputs.values():
        if not isinstance(node, dict):
            continue
        for items in node.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("filename"):
                    found.append(item)
    return found


def submit_prompt(graph: dict[str, Any], origin: str = "") -> str:
    root = origin or _require_online()
    payload = {"prompt": graph, "client_id": str(uuid.uuid4())}
    submitted = _http_post(f"{root}/prompt", json=payload, timeout=30)
    submitted.raise_for_status()
    body = submitted.json()
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"ComfyUI rejected the prompt: {body.get('error')}")
    prompt_id = str((body or {}).get("prompt_id") or "")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not accept the prompt: {body}")
    return prompt_id


def wait_for_job(prompt_id: str, origin: str = "", timeout: float = _WORKFLOW_TIMEOUT) -> list[Path]:
    root = origin or comfy_origin()
    deadline = time.time() + max(30, float(timeout))
    while time.time() < deadline:
        hist = _http_get(f"{root}/history/{prompt_id}", timeout=10)
        hist.raise_for_status()
        data = hist.json() or {}
        entry = data.get(prompt_id) if isinstance(data, dict) else None
        if isinstance(entry, dict):
            status = entry.get("status")
            if isinstance(status, dict) and status.get("status_str") == "error":
                messages = status.get("messages") or []
                raise RuntimeError(f"ComfyUI job failed: {messages or entry}")
            if entry.get("outputs"):
                media = _collect_media(entry.get("outputs") or {})
                if media:
                    saved: list[Path] = []
                    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
                    for item in media:
                        name = str(item.get("filename") or f"jinx-{prompt_id[:8]}")
                        dest = GENERATED_DIR / name
                        saved.append(_download_output(root, item, dest))
                    return saved
        time.sleep(0.7)
    raise TimeoutError(f"ComfyUI timed out waiting for job {prompt_id}")


def format_saved(paths: list[Path], caption: str = "") -> str:
    if not paths:
        return "ComfyUI finished but returned no files."
    label = (caption or "Generated").replace("\n", " ")[:80]
    lines = []
    for path in paths:
        posix = path.as_posix()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            lines.append(f"Saved image: {path}\n\n![{label}]({posix})")
        else:
            lines.append(f"Saved file: {path}")
    return "\n\n".join(lines)


def queue_status(origin: str = "") -> dict[str, Any]:
    root = origin or comfy_origin()
    data = _get_json(root, "/queue", timeout=4)
    return data if isinstance(data, dict) else {}


def interrupt_comfy(origin: str = "") -> str:
    root = origin.rstrip("/") if origin else _require_online(start=False)
    _http_post(f"{root}/interrupt", timeout=8).raise_for_status()
    return "Asked ComfyUI to interrupt the current job."


def _resolve_media(raw: str | Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Missing image path")
    try:
        from .tools import resolve_user_path

        return resolve_user_path(text)
    except (ValueError, ImportError):
        pass
    path = Path(text).expanduser()
    if path.is_file():
        extra = [GENERATED_DIR, BASE_DIR / "data" / "screenshots"]
        cfg = desktop_settings()
        for key in ("inputDir", "outputDir"):
            if cfg.get(key):
                extra.append(Path(str(cfg[key])))
        resolved = path.resolve()
        for root in extra:
            try:
                resolved.relative_to(Path(root).expanduser().resolve())
                return resolved
            except (ValueError, OSError):
                continue
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(f"No image at {raw}")


def upload_image(path: str | Path, origin: str = "") -> str:
    file_path = _resolve_media(path)
    root = origin or _require_online()
    with file_path.open("rb") as handle:
        response = _http_post(
            f"{root}/upload/image",
            files={"image": (file_path.name, handle, "application/octet-stream")},
            data={"overwrite": "true"},
            timeout=60,
        )
    response.raise_for_status()
    body = response.json() if response.content else {}
    name = str((body or {}).get("name") or file_path.name)
    return name


def system_report() -> str:
    origin = comfy_origin(refresh=True)
    online = _ping(origin)
    lines = [f"ComfyUI: {'online' if online else 'offline'} at {origin}"]
    exe = desktop_exe()
    if exe:
        lines.append(f"Comfy Desktop app (not used for generation): {exe}")
    root = install_root()
    if root:
        lines.append(f"Install: {root}")
    py = install_python(root) if root else None
    if py:
        lines.append(f"Headless python: {py}")
    shared = shared_root()
    if shared:
        lines.append(f"Models: {shared}")
    if owned_by_jinx():
        lines.append("Started by Jinx (hidden window, no Electron).")
    if not online:
        ckpts = list_models_on_disk("checkpoints")
        lines.append(f"Checkpoints on disk: {len(ckpts)}")
        if ckpts:
            lines.append("  " + ", ".join(ckpts[:8]))
        lines.append(
            "Jinx will start a hidden ComfyUI on the next generate, or click Launch "
            "on AI Models → ComfyUI (chat LLM stays loaded)."
        )
        return "\n".join(lines)
    try:
        stats = _get_json(origin, "/system_stats")
    except Exception as exc:  # noqa: BLE001
        stats = {"error": str(exc)}
    devices = stats.get("devices") if isinstance(stats, dict) else None
    system = stats.get("system") if isinstance(stats, dict) else None
    if isinstance(system, dict):
        lines.append(f"ComfyUI python: {system.get('comfyui_version') or system.get('python_version') or ''}".strip())
    if isinstance(devices, list):
        for device in devices:
            if not isinstance(device, dict):
                continue
            name = device.get("name") or device.get("type")
            vram = device.get("vram_total") or device.get("vram_free")
            lines.append(f"GPU: {name}" + (f" ({int(vram) // (1024 * 1024)} MB VRAM)" if vram else ""))
    try:
        queue = queue_status(origin)
        running = queue.get("queue_running") or []
        pending = queue.get("queue_pending") or []
        lines.append(f"Queue: {len(running)} running, {len(pending)} pending")
    except Exception:  # noqa: BLE001
        pass
    ckpts = list_checkpoints(origin)
    lines.append(f"Checkpoints: {len(ckpts)}")
    nodes = node_classes(origin)
    lines.append(f"Nodes: {len(nodes)}")
    rembg = rembg_node(origin)
    lines.append("Background removal: " + (rembg or "no rembg node installed"))
    return "\n".join(lines)


def _pick_checkpoint(checkpoint: str = "") -> str:
    brains = brains_settings()
    ckpt = (checkpoint or str(brains.get("image_model") or "")).strip()
    host = str(brains.get("image_host") or "")
    if host and host not in {"comfyui", "comfy"}:
        raise RuntimeError(
            "Image slot is not a ComfyUI checkpoint. Open AI Models → ComfyUI, pick a ComfyUI "
            "checkpoint, then Image slot. The chat LLM stays loaded."
        )
    origin = _require_online()
    if not ckpt:
        names = list_checkpoints(origin)
        if not names:
            raise RuntimeError(
                "No ComfyUI checkpoints found. Put a .safetensors file in the Desktop shared "
                "models/checkpoints folder."
            )
        ckpt = names[0]
    low = ckpt.lower()
    if any(token in low for token in _SPECIAL):
        raise RuntimeError(
            f"{ckpt} needs a dedicated workflow (not the built-in SD/SDXL graph). "
            "Use comfy_list templates then comfy_run_workflow, or export API JSON from Comfy Desktop."
        )
    return ckpt


def generate_image(
    prompt: str,
    *,
    checkpoint: str = "",
    negative: str = "",
    seed: int = -1,
    width: int = 0,
    height: int = 0,
    steps: int = 0,
    image: str = "",
    denoise: float = 0.55,
    keep_alive: bool = False,
) -> Path:
    text = (prompt or "").strip()
    if not text:
        raise ValueError("Empty image prompt")
    origin = _require_online()
    brains = brains_settings()
    if keep_alive:
        mark_comfy_sticky()
    ckpt = _pick_checkpoint(checkpoint)
    width = int(width or brains.get("comfyui_width") or 0)
    height = int(height or brains.get("comfyui_height") or 0)
    steps = int(steps or brains.get("comfyui_steps") or 0)
    if image:
        uploaded = upload_image(_resolve_media(image), origin)
        graph = img2img_workflow(ckpt, text, uploaded, negative, seed, denoise, steps=steps)
    else:
        graph = txt2img_workflow(ckpt, text, negative, seed, width=width, height=height, steps=steps)
    prompt_id = submit_prompt(graph, origin)
    saved = wait_for_job(prompt_id, origin, timeout=_GEN_TIMEOUT)
    return saved[0]


def upscale_image(path: str, scale: float = 2.0, model: str = "", keep_alive: bool = False) -> list[Path]:
    origin = _require_online()
    if keep_alive:
        mark_comfy_sticky()
    uploaded = upload_image(_resolve_media(path), origin)
    model_name = (model or "").strip()
    if not model_name:
        models = list_assets("upscale", origin=origin)
        if models:
            model_name = models[0]
    graph = upscale_workflow(uploaded, scale, model_name)
    prompt_id = submit_prompt(graph, origin)
    return wait_for_job(prompt_id, origin, timeout=_GEN_TIMEOUT)


def remove_background(path: str, keep_alive: bool = False) -> list[Path]:
    origin = _require_online()
    if keep_alive:
        mark_comfy_sticky()
    node = rembg_node(origin)
    if not node:
        raise RuntimeError(
            "This Comfy install has no background-removal node. Install a rembg custom node "
            "in Comfy Desktop, or run a template with comfy_run_workflow."
        )
    uploaded = upload_image(_resolve_media(path), origin)
    graph = rembg_workflow(uploaded, node)
    prompt_id = submit_prompt(graph, origin)
    return wait_for_job(prompt_id, origin, timeout=_GEN_TIMEOUT)


def load_workflow_payload(workflow: str) -> dict[str, Any]:
    raw = (workflow or "").strip()
    if not raw:
        raise ValueError("Missing workflow")
    if raw.startswith("{") or raw.startswith("["):
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Workflow JSON must be an object")
        return data
    folder = template_dir()
    if folder is not None:
        match = folder / f"{raw}.json"
        if not match.is_file():
            hits = list(folder.glob(f"*{raw}*.json"))
            match = hits[0] if len(hits) == 1 else match
        if match.is_file():
            return json.loads(match.read_text(encoding="utf-8"))
    from .tools import resolve_user_path

    path = Path(raw)
    if not path.is_file():
        try:
            path = resolve_user_path(raw)
        except (ValueError, FileNotFoundError):
            path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"No workflow JSON at {raw}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Workflow file is not a JSON object")
    return data


def run_workflow(
    workflow: str,
    *,
    wait: bool = True,
    timeout: float = _WORKFLOW_TIMEOUT,
    params: dict[str, Any] | None = None,
    keep_alive: bool = False,
) -> str:
    origin = _require_online()
    if keep_alive or not wait:
        mark_comfy_sticky()
    payload = load_workflow_payload(workflow)
    info = None
    if "nodes" in payload:
        try:
            info = object_info(origin)
        except Exception:  # noqa: BLE001
            info = {}
    graph = ui_to_api(payload, info)
    graph = apply_overrides(graph, params)
    prompt_id = submit_prompt(graph, origin)
    if not wait:
        return f"Submitted ComfyUI job {prompt_id}. Call comfy_info later or wait with the same prompt_id."
    saved = wait_for_job(prompt_id, origin, timeout=timeout)
    return format_saved(saved, caption=Path(str(workflow)).stem or "workflow")


def extra_paths_config() -> Path | None:
    shared = shared_root()
    if shared is None:
        return None
    dest = BASE_DIR / "data" / "comfy_extra_model_paths.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "jinx:",
        f"    base_path: {shared.as_posix()}",
        "    is_default: true",
    ]
    for folder in _EXTRA_FOLDERS:
        if (shared / folder).is_dir():
            lines.append(f"    {folder}: {folder}/")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def install_python(root: Path | None) -> Path | None:
    if root is None:
        return None
    for rel in (
        Path(".venv") / "Scripts" / "pythonw.exe",
        Path(".venv") / "Scripts" / "python.exe",
        Path("venv") / "Scripts" / "pythonw.exe",
        Path("venv") / "Scripts" / "python.exe",
        Path(".venv") / "bin" / "python",
        Path("venv") / "bin" / "python",
    ):
        path = root / rel
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _listen_port() -> int:
    brains = brains_settings()
    raw = str(brains.get("comfyui_url") or COMFYUI_URL or "http://127.0.0.1:8188").rstrip("/")
    try:
        host = raw.rsplit(":", 1)
        if len(host) == 2 and host[1].isdigit():
            return int(host[1])
    except (TypeError, ValueError):
        pass
    return 8188


def hidden_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    return _CREATE_NO_WINDOW | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", _CREATE_NEW_PROCESS_GROUP
    )


def build_headless_command(root: Path | None = None) -> tuple[list[str], Path]:
    """Argv + cwd for a windowless ComfyUI server. Never launches Electron."""
    found = root or install_root()
    if found is None:
        configured = Path(str(brains_settings().get("comfyui_root") or COMFYUI_ROOT or "")).expanduser()
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        home = Path.home()
        found = _first_dir(
            [
                configured,
                home / "ComfyUI",
                home / "Documents" / "ComfyUI",
                Path(r"C:\ComfyUI"),
                local / "Programs" / "ComfyUI",
            ]
        )
    if found is None or not (found / "main.py").is_file():
        cli = shutil.which("comfy")
        if not cli:
            raise FileNotFoundError(
                "No ComfyUI Python install found. Install Comfy Desktop once so Jinx can use "
                "its .venv and shared models — she will not open the Desktop window. "
                "Or set JINX_COMFYUI_ROOT to a folder that contains main.py."
            )
        port = str(_listen_port())
        cmd = [
            cli,
            "launch",
            "--",
            "--listen",
            "127.0.0.1",
            "--port",
            port,
            "--disable-auto-launch",
        ]
        extra = extra_paths_config()
        if extra is not None:
            cmd.extend(["--extra-model-paths-config", str(extra)])
        return cmd, Path(cli).resolve().parent

    py = install_python(found)
    if py is None:
        py = Path(shutil.which("python") or sys.executable)
    port = str(_listen_port())
    cmd = [
        str(py),
        "main.py",
        "--listen",
        "127.0.0.1",
        "--port",
        port,
        "--disable-auto-launch",
    ]
    extra = extra_paths_config()
    if extra is not None:
        cmd.extend(["--extra-model-paths-config", str(extra)])
    user_dir = found / "user"
    cmd.extend(["--user-directory", str(user_dir)])
    cfg = desktop_settings()
    input_dir = Path(str(cfg.get("inputDir") or "")).expanduser()
    output_dir = Path(str(cfg.get("outputDir") or "")).expanduser()
    if str(cfg.get("inputDir") or "") and input_dir.is_dir():
        cmd.extend(["--input-directory", str(input_dir)])
    if str(cfg.get("outputDir") or "") and output_dir.is_dir():
        cmd.extend(["--output-directory", str(output_dir)])
    return cmd, found


def _first_dir(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.is_dir() and (path / "main.py").is_file():
                return path
        except OSError:
            continue
    return None


def _spawn_hidden(cmd: list[str], cwd: Path | None = None) -> subprocess.Popen:
    log_path = BASE_DIR / "data" / "comfy-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")
    kwargs: dict[str, Any] = {
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    flags = hidden_creationflags()
    if flags:
        kwargs["creationflags"] = flags
        info = _hidden_startupinfo()
        if info is not None:
            kwargs["startupinfo"] = info
    try:
        return subprocess.Popen(cmd, **kwargs)
    finally:
        log_handle.close()


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if sys.platform != "win32":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info


def _wait_until_up(origin: str, timeout: float) -> bool:
    deadline = time.monotonic() + max(8.0, timeout)
    while time.monotonic() < deadline:
        if _ping(origin):
            return True
        time.sleep(0.8)
    return False


def ensure_comfyui(*, wait: bool = True, sticky: bool = False, timeout: float = _START_TIMEOUT) -> str:
    origin = comfy_origin(refresh=True)
    if _ping(origin):
        if sticky and owned_by_jinx():
            mark_comfy_sticky()
        return origin
    state = _load_state()
    pid = int(state.get("pid") or 0)
    if pid and not _pid_alive(pid):
        _clear_state()

    cmd, cwd = build_headless_command()
    if any("Comfy Desktop" in part or part.lower().endswith("comfy desktop.exe") for part in cmd):
        raise RuntimeError("Refusing to launch Comfy Desktop.exe — Jinx uses the hidden Python server.")
    proc = _spawn_hidden(cmd, cwd=cwd)
    python_exe = cmd[0]
    _save_state(
        {
            "pid": proc.pid,
            "python": python_exe,
            "origin": f"http://127.0.0.1:{_listen_port()}",
            "sticky": bool(sticky),
            "argv": cmd,
        }
    )
    expected = f"http://127.0.0.1:{_listen_port()}"
    if not wait:
        _forget_origin()
        return expected
    if not _wait_until_up(expected, timeout):
        raise RuntimeError(
            f"Started hidden ComfyUI (pid {proc.pid}) but {expected} did not answer within "
            f"{int(timeout)}s. Check data/comfy-server.log."
        )
    _forget_origin()
    return comfy_origin(refresh=True)


def launch_comfyui() -> str:
    """AI Models → ComfyUI Launch: hidden server, keep running until Stop or Jinx exits."""
    origin = comfy_origin(refresh=True)
    if _ping(origin):
        if owned_by_jinx():
            mark_comfy_sticky()
            return f"Hidden ComfyUI is already running at {origin}"
        return f"ComfyUI is already running at {origin} (not started by Jinx — left alone)"
    started = ensure_comfyui(wait=False, sticky=True)
    return (
        f"Starting hidden ComfyUI at {started} (no Desktop window). "
        "First boot can take a minute while custom nodes load."
    )


def stop_comfyui(*, force: bool = False) -> str:
    """Kill only the ComfyUI process Jinx spawned. Never Comfy Desktop."""
    state = _load_state()
    pid = int(state.get("pid") or 0)
    python_exe = str(state.get("python") or "")
    origin = str(state.get("origin") or "") or comfy_display_origin()
    if not pid:
        return "No hidden ComfyUI process for Jinx to stop."
    if not _pid_is_ours(pid, python_exe) and not force:
        _clear_state()
        return (
            f"PID {pid} is not Jinx's hidden ComfyUI (refusing to kill). "
            "If Comfy Desktop is open, close it yourself."
        )
    try:
        _http_post(
            f"{origin}/free",
            json={"unload_models": True, "free_memory": True},
            timeout=4,
        )
    except Exception:  # noqa: BLE001
        pass
    _kill_pid(pid)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    _clear_state()
    _forget_origin()
    if _pid_alive(pid):
        return f"Asked PID {pid} to exit but it is still running."
    return f"Stopped hidden ComfyUI (pid {pid}). VRAM is free."


def _kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        flags = hidden_creationflags()
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=flags,
            timeout=15,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def maybe_stop_comfy() -> str:
    """After a chat turn: stop Jinx's hidden server unless sticky or autostop is off."""
    if not owned_by_jinx():
        return ""
    brains = brains_settings()
    if not bool(brains.get("comfyui_autostop", True)):
        return ""
    state = _load_state()
    if state.get("sticky"):
        return ""
    try:
        origin = str(state.get("origin") or "") or comfy_origin()
        queue = _get_json(origin, "/queue", timeout=3)
        running = queue.get("queue_running") or []
        pending = queue.get("queue_pending") or []
        if running or pending:
            return ""
    except Exception:  # noqa: BLE001
        pass
    return stop_comfyui()
