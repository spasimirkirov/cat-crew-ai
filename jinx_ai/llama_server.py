"""Own llama-server process: download the official zip, launch, stop."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import requests

from .config import (
    BASE_DIR,
    DEFAULT_BIN_DIR,
    LLAMA_CONTEXT,
    LLAMA_SERVER_PORT,
    LLAMA_SERVER_URL,
)
from .hardware import probe_hardware
from .local_models import models_dir

STATE_PATH = BASE_DIR / "data" / "llama-server.json"
_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=25"
_TIMEOUT = 2.2
_START_TIMEOUT = 90.0
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_UA = {"User-Agent": "JinxAI/1.0 (llama-server)"}
_ready_lock = threading.RLock()


def server_origin(url: str = "") -> str:
    text = (url or LLAMA_SERVER_URL).rstrip("/")
    if text.endswith("/v1"):
        text = text[: -len("/v1")]
    return text.rstrip("/")


def server_v1(url: str = "") -> str:
    return server_origin(url) + "/v1"


def _win_flags() -> int:
    if sys.platform != "win32":
        return 0
    return _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP


def _load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.is_file():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_state(data: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _clear_state() -> None:
    try:
        STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
        except Exception:  # noqa: BLE001
            return False
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def owned_by_jinx() -> bool:
    pid = int(_load_state().get("pid") or 0)
    return _pid_alive(pid)


def ping_server(url: str = "") -> bool:
    try:
        response = requests.get(f"{server_v1(url)}/models", timeout=_TIMEOUT)
        return response.status_code < 500
    except requests.RequestException:
        return False


def bin_dir() -> Path:
    path = Path(DEFAULT_BIN_DIR).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def llama_server_exe() -> Path | None:
    root = bin_dir()
    direct = root / "llama-server.exe"
    if direct.is_file():
        return direct
    try:
        for path in root.rglob("llama-server.exe"):
            if path.is_file():
                return path
    except OSError:
        pass
    found = shutil.which("llama-server")
    return Path(found) if found else None


def _prefer_cuda() -> bool:
    try:
        info = probe_hardware()
        return bool(info.gpu) and "nvidia" in (info.gpu or "").lower()
    except Exception:  # noqa: BLE001
        return False


def pick_release_asset(assets: list[dict[str, Any]], *, cuda: bool) -> dict[str, Any] | None:
    names: list[tuple[int, dict[str, Any]]] = []
    for asset in assets:
        name = str(asset.get("name") or "").lower()
        if not _is_windows_server_zip(name):
            continue
        score = 0
        if "cuda" in name:
            score = 30 if cuda else 12
            if "12.4" in name or "12-4" in name:
                score += 4
            if "13" in name:
                score += 3
        elif "vulkan" in name:
            score = 25 if not cuda else 18
        elif "cpu" in name:
            score = 10
        else:
            score = 8
        names.append((score, asset))
    names.sort(key=lambda item: item[0], reverse=True)
    return names[0][1] if names else None


def pick_cudart_asset(assets: list[dict[str, Any]], server_name: str) -> dict[str, Any] | None:
    needle = str(server_name or "").lower()
    if "cuda" not in needle:
        return None
    match = re.search(r"cuda-(\d+(?:\.\d+)?)", needle)
    ver = match.group(1) if match else ""
    fallback: dict[str, Any] | None = None
    for asset in assets:
        name = str(asset.get("name") or "").lower()
        if "cudart" not in name or not name.endswith(".zip"):
            continue
        if "win" not in name or "arm64" in name:
            continue
        if "x64" not in name and "win64" not in name:
            continue
        if ver and ver in name:
            return asset
        fallback = fallback or asset
    return fallback


def _is_windows_server_zip(name: str) -> bool:
    if not name.endswith(".zip"):
        return False
    if "cudart" in name or "-ui" in name:
        return False
    if "win" not in name:
        return False
    if "arm64" in name:
        return False
    return "x64" in name or "win64" in name


def choose_release_asset(
    releases: list[dict[str, Any]],
    *,
    cuda: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """First recent llama.cpp release that actually ships a Windows zip.

    `/releases/latest` is the wrong endpoint: ggml tags the stable docs
    release (v0.4.0) as Latest, while Windows binaries live on prereleases.
    """
    for row in releases:
        assets = [item for item in (row.get("assets") or []) if isinstance(item, dict)]
        asset = pick_release_asset(assets, cuda=cuda)
        if asset is not None:
            return asset, assets
    return None


def _list_releases() -> list[dict[str, Any]]:
    response = requests.get(_RELEASES, timeout=20, headers=_UA)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        message = str(payload.get("message") or "").strip()
        if message:
            raise RuntimeError(f"GitHub llama.cpp releases: {message}")
    return []


def _extract_zip(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


def ensure_binary(
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    abort: Callable[[], bool] | None = None,
) -> Path:
    existing = llama_server_exe()
    if existing is not None:
        return existing
    abort = abort or (lambda: False)
    picked = choose_release_asset(_list_releases(), cuda=_prefer_cuda())
    if picked is None:
        raise RuntimeError(
            "Could not find a Windows llama-server zip in recent llama.cpp releases. "
            "Put llama-server.exe in C:\\Jinx-AI\\bin, then send a message again."
        )
    asset, assets = picked
    url = str(asset.get("browser_download_url") or "")
    name = str(asset.get("name") or "llama-server.zip")
    dest = bin_dir()
    archive = dest / name
    _download_file(url, archive, on_progress, abort)
    _extract_zip(archive, dest)
    try:
        archive.unlink()
    except OSError:
        pass
    cudart = pick_cudart_asset(assets, name)
    if cudart is not None:
        c_url = str(cudart.get("browser_download_url") or "")
        c_name = str(cudart.get("name") or "cudart.zip")
        c_archive = dest / c_name
        if c_url:
            _download_file(c_url, c_archive, on_progress, abort)
            _extract_zip(c_archive, dest)
            try:
                c_archive.unlink()
            except OSError:
                pass
    exe = llama_server_exe()
    if exe is None:
        raise RuntimeError("llama-server.exe was not in that zip.")
    return exe


def _download_file(
    url: str,
    dest: Path,
    on_progress: Callable[[dict[str, Any]], None] | None,
    abort: Callable[[], bool],
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=(20, 600), headers=_UA) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if abort():
                    raise RuntimeError("Cancelled llama-server download")
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if on_progress:
                    percent = int(round(100 * done / total)) if total else 0
                    on_progress(
                        {
                            "status": "Downloading llama-server",
                            "completed": done,
                            "total": total,
                            "percent": percent,
                            "indeterminate": total <= 0,
                            "text": f"llama-server {percent}%" if total else "Downloading llama-server",
                        }
                    )


def local_enabled() -> bool:
    from .settings_store import brains_settings

    return bool(brains_settings().get("local_enabled", True))


def _chat_ggufs() -> list:
    from .local_models import scan_gguf

    return scan_gguf()


def _model_is_loaded(name: str, loaded: list[str]) -> bool:
    key = Path(name).stem.lower()
    if not key:
        return False
    return any(Path(item).stem.lower() == key for item in loaded)


_LOADED_STATES = frozenset({"loaded", "loading", "ready"})


def _status_value(row: dict[str, Any]) -> str:
    status = row.get("status")
    if isinstance(status, dict):
        return str(status.get("value") or "").strip().lower()
    if isinstance(status, str):
        return status.strip().lower()
    return ""


def names_from_models_payload(payload: Any, *, loaded_only: bool = True) -> list[str]:
    """Model ids from GET /v1/models. Router lists every GGUF; only some are in VRAM."""
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    dict_rows = [row for row in rows if isinstance(row, dict)]
    has_status = any(_status_value(row) for row in dict_rows)
    names: list[str] = []
    for row in rows:
        if isinstance(row, str):
            name = row.strip()
            status = ""
        elif isinstance(row, dict):
            name = str(row.get("id") or row.get("name") or "").strip()
            status = _status_value(row)
        else:
            continue
        if not name:
            continue
        if loaded_only and has_status and status not in _LOADED_STATES:
            continue
        if name not in names:
            names.append(name)
    return names


def ctx_from_models_payload(payload: Any) -> int:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        args = []
        status = row.get("status")
        if isinstance(status, dict) and isinstance(status.get("args"), list):
            args = [str(item) for item in status["args"]]
        for index, token in enumerate(args):
            if token in {"--ctx-size", "-c"} and index + 1 < len(args):
                try:
                    return int(args[index + 1])
                except ValueError:
                    pass
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        try:
            n_ctx = int(meta.get("n_ctx") or 0)
        except (TypeError, ValueError):
            n_ctx = 0
        if n_ctx > 0:
            return n_ctx
    return 0


def running_ctx() -> int:
    try:
        response = requests.get(f"{server_v1()}/models", timeout=_TIMEOUT)
        response.raise_for_status()
        return ctx_from_models_payload(response.json())
    except Exception:  # noqa: BLE001
        return 0


def ensure_ready(model: str = "") -> str:
    """Start llama-server (and load `model`) when Local is on and a GGUF is installed."""
    with _ready_lock:
        if not local_enabled():
            raise RuntimeError("Local is off. Turn it on in AI Models → Local.")
        if not _chat_ggufs():
            raise RuntimeError(
                "No GGUF in the models folder. Pull one from AI Models → Hugging Face Hub, "
                "then send a message — Jinx will start llama-server."
            )
        if not ping_server():
            launch_server()
        elif owned_by_jinx():
            ctx = running_ctx()
            if ctx and ctx < LLAMA_CONTEXT:
                stop_server()
                launch_server()
        text = (model or "").strip()
        if text and not _model_is_loaded(text, list_loaded()):
            return load_slot(text)
        if ping_server():
            return f"llama-server is ready at {server_origin()}"
        return f"Started llama-server; waiting for {server_origin()}"


def launch_server() -> str:
    if ping_server():
        if owned_by_jinx():
            return f"llama-server is already running at {server_origin()}"
        return f"llama-server is already running at {server_origin()} (not started by Jinx)"
    exe = ensure_binary()
    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    log_path = BASE_DIR / "data" / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        str(exe),
        "--host",
        "127.0.0.1",
        "--port",
        str(LLAMA_SERVER_PORT),
        "--models-dir",
        str(folder),
        "--models-max",
        "1",
        "--jinja",
        "-ngl",
        "99",
        "-c",
        str(LLAMA_CONTEXT),
    ]
    flags = _win_flags()
    log = log_path.open("ab")
    proc = subprocess.Popen(
        args,
        cwd=str(exe.parent),
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        close_fds=True,
    )
    _save_state({"pid": proc.pid, "exe": str(exe), "port": LLAMA_SERVER_PORT})
    deadline = time.monotonic() + _START_TIMEOUT
    while time.monotonic() < deadline:
        if ping_server():
            return f"Started llama-server at {server_origin()} (pid {proc.pid})"
        if proc.poll() is not None:
            _clear_state()
            raise RuntimeError(
                f"llama-server exited early (code {proc.returncode}). See data/llama-server.log."
            )
        time.sleep(0.4)
    return f"Started llama-server (pid {proc.pid}); waiting for {server_origin()}"


def stop_server() -> str:
    state = _load_state()
    pid = int(state.get("pid") or 0)
    if not pid:
        return "No llama-server process for Jinx to stop."
    if not _pid_alive(pid):
        _clear_state()
        return "llama-server was already gone."
    _kill_pid(pid)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    _clear_state()
    if _pid_alive(pid):
        return f"Asked PID {pid} to exit but it is still running."
    return f"Stopped llama-server (pid {pid})."


def _kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            timeout=15,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def list_loaded(url: str = "") -> list[str]:
    try:
        response = requests.get(f"{server_v1(url)}/models", timeout=_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001
        return []
    return names_from_models_payload(payload, loaded_only=True)


def _wait_loaded(name: str, timeout: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _model_is_loaded(name, list_loaded()):
            return True
        time.sleep(0.4)
    return False


def load_slot(name: str) -> str:
    text = (name or "").strip()
    if not text:
        raise ValueError("No model name")
    if not ping_server():
        with _ready_lock:
            if not ping_server():
                launch_server()
    if _model_is_loaded(text, list_loaded()):
        return f"{text} is already loaded"
    origin = server_origin()
    last_err = ""
    try:
        response = requests.post(
            f"{origin}/models/load",
            json={"model": text},
            timeout=180,
        )
        if response.status_code >= 400:
            last_err = (response.text or f"HTTP {response.status_code}")[:240]
        else:
            last_err = ""
    except requests.RequestException as exc:
        last_err = str(exc)
    if _wait_loaded(text, 180.0):
        return f"Loaded {text} in llama-server"
    if last_err:
        raise RuntimeError(f"Could not load {text}: {last_err}")
    raise RuntimeError(
        f"llama-server did not finish loading {text}. Open AI Models → Local and click Load."
    )


def unload_slot(name: str) -> str:
    text = (name or "").strip()
    origin = server_origin()
    try:
        requests.post(f"{origin}/models/unload", json={"model": text}, timeout=8)
    except requests.RequestException as exc:
        raise RuntimeError("llama-server is offline.") from exc
    return f"Unloaded {text}"
