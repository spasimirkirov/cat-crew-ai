"""GGUF folder: scan, sidecar metadata, move when the models path changes."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import DEFAULT_BIN_DIR, DEFAULT_MODELS_DIR

_SIDECAR_KEYS = ("hf_repo", "quant", "mmproj", "family", "capabilities")
_SKIP_SUFFIXES = {".part", ".download", ".tmp"}
_GGUF_MAGIC = b"GGUF"


@dataclass
class GgufFile:
    name: str
    path: Path
    size_bytes: int = 0
    quant: str = ""
    family: str = ""
    hf_repo: str = ""
    mmproj: str = ""
    capabilities: list[str] | None = None
    is_mmproj: bool = False


def default_models_dir() -> Path:
    return Path(DEFAULT_MODELS_DIR)


def default_bin_dir() -> Path:
    return Path(DEFAULT_BIN_DIR)


def models_dir(data: dict[str, Any] | None = None) -> Path:
    from .settings_store import brains_settings

    raw = str((brains_settings(data).get("models_dir") or "")).strip()
    path = Path(raw) if raw else default_models_dir()
    return path.expanduser()


def ensure_models_dir(folder: Path | None = None) -> Path:
    path = (folder or models_dir()).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_mmproj_name(name: str) -> bool:
    low = (name or "").lower()
    return "mmproj" in low


def is_gguf_name(name: str) -> bool:
    return (name or "").lower().endswith(".gguf")


def part_path_for(gguf: Path) -> Path:
    return gguf.with_name(gguf.name + ".part")


def is_complete_gguf(path: Path) -> bool:
    """Finished GGUF: real file, GGUF magic, no leftover .part sibling."""
    if not path.is_file():
        return False
    if part_path_for(path).is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 4:
        return False
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic == _GGUF_MAGIC


def incomplete_gguf_names(folder: Path | None = None) -> list[str]:
    """`.part` leftovers and truncated `.gguf` files that must not look Installed."""
    root = ensure_models_dir(folder)
    names: list[str] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for path in entries:
        if not path.is_file():
            continue
        low = path.name.lower()
        if low.endswith(".gguf.part"):
            names.append(path.name[: -len(".part")])
            continue
        if is_gguf_name(path.name) and not is_complete_gguf(path):
            names.append(path.name)
    return names


def parse_quant(name: str) -> str:
    text = Path(name or "").stem
    tokens = text.replace("-", "_").split("_")
    for i, token in enumerate(tokens):
        upper = token.upper()
        if upper.startswith("Q") or upper.startswith("IQ"):
            rest = tokens[i : i + 3]
            bits = [rest[0].upper()]
            for extra in rest[1:]:
                if extra.upper() in {"K", "M", "S", "L", "XL", "XS", "NL", "XXS"}:
                    bits.append(extra.upper())
                else:
                    break
            return "_".join(bits)
    return ""


def family_from_name(name: str) -> str:
    stem = Path(name or "").stem
    if is_mmproj_name(stem):
        stem = stem.replace(".mmproj", "").replace("-mmproj", "").replace("_mmproj", "")
        for suffix in ("-f16", "-f32", "-fp16", "-fp32", "-q8_0", "-F16", "-F32"):
            if stem.lower().endswith(suffix.lower()):
                stem = stem[: -len(suffix)]
                break
    quant = parse_quant(stem)
    if quant:
        cut = stem.lower().rfind(quant.lower().replace("_", "-"))
        if cut < 0:
            cut = stem.lower().rfind(quant.lower())
        if cut > 1:
            stem = stem[:cut].rstrip("-_")
    return stem.strip("-_")


def sidecar_path(gguf: Path) -> Path:
    return gguf.with_suffix(".json")


def read_sidecar(gguf: Path) -> dict[str, Any]:
    path = sidecar_path(gguf)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_sidecar(gguf: Path, data: dict[str, Any]) -> Path:
    payload = {key: data.get(key) for key in _SIDECAR_KEYS if data.get(key) not in (None, "", [])}
    path = sidecar_path(gguf)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def scan_gguf(folder: Path | None = None) -> list[GgufFile]:
    """Installed chat GGUFs. mmproj files are attached, not listed as models."""
    root = ensure_models_dir(folder)
    found: list[GgufFile] = []
    mmproj_by_stem: dict[str, Path] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for path in entries:
        if not path.is_file() or path.suffix.lower() in _SKIP_SUFFIXES:
            continue
        if not is_gguf_name(path.name):
            continue
        if is_mmproj_name(path.name):
            if is_complete_gguf(path):
                mmproj_by_stem[family_from_name(path.name).lower()] = path
            continue
        if not is_complete_gguf(path):
            continue
        meta = read_sidecar(path)
        caps = meta.get("capabilities")
        if not isinstance(caps, list):
            caps = None
        found.append(
            GgufFile(
                name=path.stem,
                path=path,
                size_bytes=int(path.stat().st_size),
                quant=str(meta.get("quant") or parse_quant(path.name)),
                family=str(meta.get("family") or family_from_name(path.name)),
                hf_repo=str(meta.get("hf_repo") or ""),
                mmproj=str(meta.get("mmproj") or ""),
                capabilities=caps,
            )
        )
    for item in found:
        if item.mmproj:
            continue
        mate = mmproj_by_stem.get(item.family.lower()) or mmproj_by_stem.get(item.name.lower())
        if mate is not None:
            item.mmproj = mate.name
    found.sort(key=lambda item: item.name.lower())
    return found


def same_volume(src: Path, dest: Path) -> bool:
    left = src.expanduser().resolve()
    right = dest.expanduser().resolve()
    if sys.platform == "win32":
        return (left.drive or "c:").lower() == (right.drive or "c:").lower()
    try:
        if not left.exists():
            left = left.parent
        if not right.exists():
            right = right.parent
        return os.stat(left).st_dev == os.stat(right).st_dev
    except OSError:
        return False


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    if dest.stat().st_size != src.stat().st_size:
        dest.unlink(missing_ok=True)
        raise OSError(f"Copy size mismatch for {src.name}")


def move_models_dir(
    src: Path,
    dest: Path,
    *,
    stop: Callable[[], None] | None = None,
) -> Path:
    """Move GGUFs to a new folder. Same volume = rename; else copy+verify+delete."""
    old = Path(src).expanduser()
    new = Path(dest).expanduser()
    if old.resolve() == new.resolve():
        ensure_models_dir(new)
        return new
    if stop is not None:
        stop()
    ensure_models_dir(new)
    if not old.exists():
        return new
    same = same_volume(old, new)
    try:
        names = list(old.iterdir())
    except OSError:
        names = []
    for path in names:
        target = new / path.name
        if path.suffix.lower() in _SKIP_SUFFIXES:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        if not path.is_file():
            continue
        if same:
            if target.exists():
                target.unlink()
            path.replace(target)
            continue
        _copy_file(path, target)
        path.unlink()
    try:
        if old.exists() and not any(old.iterdir()):
            old.rmdir()
    except OSError:
        pass
    return new
