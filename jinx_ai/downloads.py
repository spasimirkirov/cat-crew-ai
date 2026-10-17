"""Local GGUF download history for the title-bar flyout and Downloads page."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BASE_DIR

HISTORY_PATH = BASE_DIR / "data" / "downloads.json"
JOB_PATH = BASE_DIR / "data" / "download_job.json"
HISTORY_CAP = 200
RECENT_COUNT = 5
RETRY_STATUSES = frozenset({"interrupted", "cancelled", "failed"})
OPEN_STATUSES = frozenset({"downloading", "queued"})


def load_history() -> list[dict[str, Any]]:
    try:
        if HISTORY_PATH.is_file():
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            rows = data.get("items") if isinstance(data, dict) else data
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    return []


def save_history(items: list[dict[str, Any]]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps({"items": items[:HISTORY_CAP]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def record_download(
    name: str,
    *,
    status: str,
    folder: str = "",
    error: str = "",
) -> dict[str, Any]:
    from .lm_runtime import model_key

    now = datetime.now(timezone.utc).isoformat()
    row = {
        "name": (name or "").strip(),
        "status": (status or "done").strip() or "done",
        "folder": str(folder or ""),
        "error": (error or "").strip()[:400],
        "finished_at": now,
    }
    if row["status"] in OPEN_STATUSES:
        row["started_at"] = now
    key = model_key(row["name"])
    previous = ""
    items = []
    for item in load_history():
        if model_key(str(item.get("name") or "")) == key:
            previous = str(item.get("name") or "")
            continue
        items.append(item)
    if looks_like_hf_spec(previous) and not looks_like_hf_spec(row["name"]):
        row["name"] = previous
    items.insert(0, row)
    save_history(items)
    return row


def save_job(current: str, queued: list[str] | None = None) -> None:
    current = (current or "").strip()
    waiting = [str(item).strip() for item in (queued or []) if str(item).strip()]
    if not current and not waiting:
        try:
            JOB_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return
    JOB_PATH.parent.mkdir(parents=True, exist_ok=True)
    JOB_PATH.write_text(
        json.dumps({"current": current, "queued": waiting}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_job() -> dict[str, Any]:
    try:
        if JOB_PATH.is_file():
            data = json.loads(JOB_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return {}


def display_name(name: str) -> str:
    text = (name or "").strip()
    if "#" in text:
        return text.split("#", 1)[-1]
    return text


def _gguf_path(name: str, root: Path) -> Path:
    file_name = Path(display_name(name)).name
    if not file_name.lower().endswith(".gguf"):
        file_name = f"{Path(file_name).stem}.gguf"
    return root / file_name


def looks_like_hf_spec(name: str) -> bool:
    text = (name or "").strip()
    repo, sep, _rest = text.partition("#")
    return bool(sep) and "/" in repo


def _repo_from_sidecar(file_name: str, root: Path) -> str:
    from .local_models import read_sidecar

    dest = _gguf_path(file_name, root)
    return str(read_sidecar(dest).get("hf_repo") or "").strip()


def _repo_from_sibling_history(file_name: str) -> str:
    import re
    from .local_models import family_from_name, is_mmproj_name

    if is_mmproj_name(file_name):
        return ""
    size = re.search(r"(\d+)\s*b", Path(file_name).stem.lower())
    if not size:
        return ""
    want = size.group(0).replace(" ", "")
    family = family_from_name(file_name).lower()
    token = family.split("-")[0].split("_")[0]
    for item in load_history():
        spec = str(item.get("name") or "").strip()
        if not looks_like_hf_spec(spec):
            continue
        repo = spec.split("#", 1)[0]
        if token and token not in repo.lower():
            continue
        guessed = re.sub(r"\d+\s*b", want, repo, count=1, flags=re.I)
        if guessed != repo:
            return guessed
    return ""


def _parent_chat_spec() -> str:
    from .local_models import is_mmproj_name

    for item in load_history():
        spec = str(item.get("name") or "").strip()
        if not spec or is_mmproj_name(spec):
            continue
        status = str(item.get("status") or "")
        if status not in RETRY_STATUSES | OPEN_STATUSES:
            continue
        resolved = resolve_download_spec(spec, guess=True)
        if looks_like_hf_spec(resolved):
            return resolved
    return ""


def with_live_status(
    recent: list[dict[str, Any]],
    current: str = "",
    queued: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Overlay downloading/queued onto history rows so Retry is not shown."""
    from .lm_runtime import model_key

    live: dict[str, tuple[str, str]] = {}
    if (current or "").strip():
        live[model_key(current)] = ("downloading", current.strip())
    for item in queued or []:
        text = str(item).strip()
        if text:
            live[model_key(text)] = ("queued", text)
    out: list[dict[str, Any]] = []
    for row in recent:
        key = model_key(str(row.get("name") or ""))
        if key in live:
            status, spec = live[key]
            row = {**row, "status": status, "name": spec, "error": ""}
        out.append(row)
    return out


def resolve_download_spec(name: str, folder: Path | None = None, *, guess: bool = True) -> str:
    """Turn a leftover filename into repo#file when we still know the Hub repo."""
    from .lm_runtime import model_key
    from .local_models import is_mmproj_name, models_dir

    text = (name or "").strip()
    if not text:
        return ""
    if looks_like_hf_spec(text):
        return text
    key = model_key(text)
    file_name = _gguf_path(text, Path(".")).name
    for item in load_history():
        spec = str(item.get("name") or "").strip()
        if looks_like_hf_spec(spec) and model_key(spec) == key:
            if is_mmproj_name(file_name):
                continue
            return spec
    root = folder or models_dir()
    repo = _repo_from_sidecar(file_name, root)
    if repo:
        return f"{repo}#{file_name}"
    if is_mmproj_name(file_name):
        parent = _parent_chat_spec()
        if parent:
            return parent
        return text
    sibling = _repo_from_sibling_history(file_name)
    if sibling:
        return f"{sibling}#{file_name}"
    if guess:
        from .gguf_browser import guess_hub_spec

        found = guess_hub_spec(file_name)
        if found:
            return found
    return text


def discard_incomplete(name: str, folder: Path | None = None) -> list[str]:
    """Delete leftover .part / truncated GGUF so a retry starts clean."""
    from .local_models import is_complete_gguf, models_dir, part_path_for

    root = folder or models_dir()
    dest = _gguf_path(name, root)
    removed: list[str] = []
    for path in (part_path_for(dest), dest):
        if not path.is_file():
            continue
        if path == dest and is_complete_gguf(dest):
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed


def recover_interrupted(folder: Path | None = None) -> list[str]:
    """Leftover pulls after a crash or Exit: history + .part files become interrupted."""
    from .local_models import incomplete_gguf_names, is_complete_gguf, models_dir
    from .lm_runtime import model_key

    root = folder or models_dir()
    names: list[str] = []
    job = load_job()
    current = str(job.get("current") or "").strip()
    if current:
        names.append(current)
    for item in job.get("queued") or []:
        text = str(item).strip()
        if text:
            names.append(text)
    names.extend(incomplete_gguf_names(root))
    seen: set[str] = set()
    marked: list[str] = []
    for name in names:
        spec = resolve_download_spec(name, root)
        key = model_key(spec or name)
        if not key or key in seen:
            continue
        dest = _gguf_path(spec or name, root)
        if is_complete_gguf(dest):
            continue
        seen.add(key)
        record_download(
            spec,
            status="interrupted",
            folder=str(root),
            error="Download was interrupted.",
        )
        marked.append(spec)
    save_job("", [])
    return marked


def recent_downloads(limit: int = RECENT_COUNT) -> list[dict[str, Any]]:
    return load_history()[: max(0, int(limit))]


def format_when(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if when.tzinfo is not None:
            when = when.astimezone()
        return when.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16]


def display_name(name: str) -> str:
    text = (name or "").strip()
    if "#" in text:
        return text.split("#", 1)[-1]
    return text
