"""Detect Docker Engine / Compose on this PC (optional agent sandbox + toolboxes)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

_CREATE_NO_WINDOW = 0x08000000
_TTL = 120.0
_cache: DockerInfo | None = None
_cache_at = 0.0


@dataclass
class DockerInfo:
    available: bool
    engine: str = ""
    compose: str = ""
    context: str = ""
    os_name: str = ""
    error: str = ""

    def prompt_line(self) -> str:
        if not self.available:
            return "Docker: not available on PATH (or the engine is not running)."
        bits = [f"Docker Engine {self.engine}" if self.engine else "Docker Engine"]
        if self.compose:
            bits.append(f"Compose {self.compose}")
        if self.os_name:
            bits.append(self.os_name)
        return "Docker: " + ", ".join(bits) + ". Call read_skill docker when useful."


def _run(cmd: list[str], timeout: float = 4.0) -> tuple[int, str]:
    flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return int(proc.returncode), out


def docker_bin() -> str | None:
    return shutil.which("docker")


def probe_docker(ttl: float = _TTL) -> DockerInfo:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < ttl:
        return _cache
    if not docker_bin():
        info = DockerInfo(available=False, error="docker not on PATH")
        _cache, _cache_at = info, now
        return info
    code, engine = _run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        timeout=6.0,
    )
    if code != 0 or not engine or "error" in engine.lower():
        info = DockerInfo(
            available=False,
            error=engine or "docker engine not reachable (is Docker Desktop running?)",
        )
        _cache, _cache_at = info, now
        return info
    _c, compose_raw = _run(["docker", "compose", "version", "--short"], timeout=4.0)
    compose = compose_raw if _c == 0 else ""
    if not compose:
        _c2, compose_long = _run(["docker", "compose", "version"], timeout=4.0)
        if _c2 == 0 and "version" in compose_long.lower():
            # e.g. "Docker Compose version v5.5.0"
            compose = compose_long.split()[-1].lstrip("v")
    _ci, context = _run(["docker", "context", "show"], timeout=3.0)
    _oi, os_name = _run(
        ["docker", "info", "--format", "{{.OperatingSystem}}"],
        timeout=6.0,
    )
    info = DockerInfo(
        available=True,
        engine=engine.strip(),
        compose=compose.strip(),
        context=context.strip() if _ci == 0 else "",
        os_name=os_name.strip() if _oi == 0 else "",
    )
    _cache, _cache_at = info, now
    return info


def format_docker(info: DockerInfo | None = None) -> str:
    snap = info or probe_docker()
    if not snap.available:
        return (
            "Docker: not available.\n"
            f"Detail: {snap.error or 'unknown'}\n"
            "Install Docker Desktop and keep the engine running if you want "
            "container sandboxes or MCP toolbox images."
        )
    lines = [
        f"Docker Engine: {snap.engine}",
        f"Compose: {snap.compose or '(not found)'}",
    ]
    if snap.context:
        lines.append(f"Context: {snap.context}")
    if snap.os_name:
        lines.append(f"OS: {snap.os_name}")
    lines.append(
        "Use: optional command sandbox (`docker run --rm -v ...`) and "
        "MCP/toolbox images for connectors. Everyday coding stays on the host "
        "Terminal. Call read_skill docker."
    )
    return "\n".join(lines)


def docker_info_dict() -> dict[str, Any]:
    return asdict(probe_docker())
