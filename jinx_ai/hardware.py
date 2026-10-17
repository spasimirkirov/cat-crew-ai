"""Local CPU, RAM, and GPU probe so Jinx can size model recommendations."""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_CREATE_NO_WINDOW = 0x08000000
_TTL = 120.0
_GENERIC_CPU = re.compile(r"Family\s+\d+\s+Model", re.I)
_cache: HardwareInfo | None = None
_cache_at = 0.0


@dataclass
class HardwareInfo:
    cpu: str
    cores: int
    ram_gb: float
    gpu: str
    vram_gb: float

    def prompt_line(self) -> str:
        gpu = self.gpu or "unknown GPU"
        if self.vram_gb > 0:
            gpu = f"{self.gpu} ({self.vram_gb:.0f} GB VRAM)"
        cpu = self.cpu or "unknown CPU"
        return (
            f"This PC: {gpu}, {self.ram_gb:.0f} GB RAM, "
            f"{cpu} ({self.cores} cores)."
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_hardware(*, ttl: float = _TTL) -> HardwareInfo:
    """Return a cached snapshot. ttl=0 always re-probes."""
    global _cache, _cache_at
    now = time.monotonic()
    if ttl > 0 and _cache is not None and now - _cache_at < ttl:
        return _cache
    info = HardwareInfo(
        cpu=_cpu_name(),
        cores=os.cpu_count() or 1,
        ram_gb=_ram_gb(),
        gpu="",
        vram_gb=0.0,
    )
    gpus = _nvidia_gpus()
    if not gpus:
        gpus = _fallback_gpus()
    if gpus:
        name, vram = max(gpus, key=lambda item: item[1])
        info.gpu = name
        info.vram_gb = vram
    else:
        info.gpu = "No NVIDIA GPU detected"
    _cache = info
    _cache_at = now
    return info


def format_hardware(info: HardwareInfo | None = None) -> str:
    snap = info or probe_hardware()
    lines = [
        f"CPU: {snap.cpu}",
        f"Cores: {snap.cores}",
        f"RAM: {snap.ram_gb:.1f} GB",
        f"GPU: {snap.gpu}",
        f"VRAM: {snap.vram_gb:.1f} GB" if snap.vram_gb else "VRAM: unknown",
    ]
    return "\n".join(lines)


def _run(cmd: list[str], timeout: float = 4.0) -> str:
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "text": True,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.check_output(cmd, **kwargs).strip()


def _cpu_name() -> str:
    name = (platform.processor() or "").strip()
    if name and "unknown" not in name.lower() and not _GENERIC_CPU.search(name):
        return name
    if sys.platform == "win32":
        try:
            out = _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_Processor).Name",
                ]
            )
            line = out.splitlines()[0].strip() if out else ""
            if line:
                return line
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.machine() or "CPU"


def _ram_gb() -> float:
    if sys.platform == "win32":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / (1024**3), 1)
        except (OSError, AttributeError, ValueError):
            pass
    return 0.0


def _nvidia_smi() -> str:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    bundled = Path(r"C:\Windows\System32\nvidia-smi.exe")
    if bundled.is_file():
        return str(bundled)
    return ""


def _nvidia_gpus() -> list[tuple[str, float]]:
    exe = _nvidia_smi()
    if not exe:
        return []
    try:
        out = _run(
            [
                exe,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ]
        )
    except (OSError, subprocess.SubprocessError):
        return []
    gpus: list[tuple[str, float]] = []
    for line in out.splitlines():
        if "," not in line:
            continue
        name, mem = line.rsplit(",", 1)
        try:
            mi = float(mem.strip())
        except ValueError:
            continue
        # nvidia-smi memory.total is MiB.
        gb = round(mi / 1024.0, 1) if mi > 128 else round(mi, 1)
        gpus.append((name.strip(), gb))
    return gpus


def _fallback_gpus() -> list[tuple[str, float]]:
    if sys.platform != "win32":
        return []
    try:
        out = _run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | "
                "ForEach-Object { $_.Name + ',' + $_.AdapterRAM }",
            ]
        )
    except (OSError, subprocess.SubprocessError):
        return []
    gpus: list[tuple[str, float]] = []
    for line in out.splitlines():
        if "," not in line:
            continue
        name, ram = line.rsplit(",", 1)
        label = name.strip()
        if not label or "microsoft" in label.lower():
            continue
        try:
            raw = float(ram.strip())
        except ValueError:
            gpus.append((label, 0.0))
            continue
        gb = round(raw / (1024**3), 1) if raw > 1024 else 0.0
        gpus.append((label, gb))
    return gpus
