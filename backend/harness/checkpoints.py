"""Checkpoints: every agent turn can be undone, files and all.

Each turn (``harness/loop.run_agent``: a 1:1 chat, Cat Crew's chat, each pipeline work order)
opens a checkpoint for the workspace it runs in. Before a tool changes a file
(``write_file``, ``replace_in_file``, ``move_path``...; the ``checkpoint`` before-hook), the file's
content as it was before the turn touched it is saved once. Restoring a checkpoint puts every file
back the way it was before that turn, undoing that turn and every later one in the same workspace,
newest first: files the turns created are deleted, files they changed or deleted come back.

What a checkpoint cannot undo, it lists: shell commands (``run_command`` and friends) and remote
actions (pushes, deploys) ran outside the file tools, so their effects stay. A turn that changed
nothing leaves no checkpoint behind.

Storage is ``data/checkpoints/<workspace key>/<checkpoint id>/``: a ``manifest.json`` plus one
saved copy per file. The last ``KEEP`` checkpoints per workspace are kept.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config import BASE_DIR

CHECKPOINTS_DIR = BASE_DIR / "data" / "checkpoints"
KEEP = 60
_MAX_FILE_BYTES = 5_000_000
_MAX_TREE_BYTES = 25_000_000

# Tool -> the arguments that name paths it changes.
PATH_ARGS: dict[str, tuple[str, ...]] = {
    "write_file": ("path",),
    "replace_in_file": ("path",),
    "write_file_patch": ("path",),
    "create_directory": ("path",),
    "copy_path": ("dest",),
    "move_path": ("src", "dest"),
}
# Tools whose effects a checkpoint cannot take back: named in the checkpoint so the UI can say so.
UNTRACKED = frozenset(
    {
        "run_command", "execute_terminal_cmd", "run_test_suite", "run_smoke_test", "scaffold_project",
        "ssh_command", "git_commit", "git_push", "git_pull", "github_push", "github_create_branch",
        "github_create_pull_request", "github_merge_pull_request", "github_create_release",
        "github_run_workflow",
    }
)

_lock = threading.RLock()


@dataclass
class _Active:
    id: str
    folder: Path
    workspace: Path
    label: str
    created_at: float
    project_id: str = ""
    agent_id: str = ""
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)

    def manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "workspace": str(self.workspace),
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "files": self.files,
            "commands": self.commands,
        }

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "manifest.json").write_text(json.dumps(self.manifest(), indent=1), encoding="utf-8")


_current: ContextVar[_Active | None] = ContextVar("catcrew_checkpoint", default=None)


def workspace_key(workspace: Path | str) -> str:
    resolved = str(Path(workspace).resolve()).replace("\\", "/").lower()
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def _scope_dir(workspace: Path | str) -> Path:
    return CHECKPOINTS_DIR / workspace_key(workspace)


def begin(label: str = "", workspace: Path | str | None = None) -> object | None:
    """Open a checkpoint for this turn. Returns a token for :func:`end`, or ``None`` when a
    checkpoint is already open (a subagent's changes belong to the turn that started it)."""
    if _current.get() is not None:
        return None
    try:
        if workspace is None:
            from backend.services.workspace import current_workspace

            workspace = current_workspace()
        ws = Path(workspace).resolve()
    except Exception:  # noqa: BLE001 - no workspace, no checkpoint; the turn must still run
        return None
    try:
        from backend.platform.runtime import current_bot_id, current_project_id

        project_id, agent_id = current_project_id(), current_bot_id()
    except Exception:  # noqa: BLE001
        project_id = agent_id = ""
    stamp = time.time()
    cp_id = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(stamp))}-{uuid.uuid4().hex[:6]}"
    active = _Active(
        id=cp_id,
        folder=_scope_dir(ws) / cp_id,
        workspace=ws,
        label=" ".join(str(label or "").split())[:160],
        created_at=stamp,
        project_id=project_id,
        agent_id=agent_id,
    )
    return _current.set(active)


def end(token: object | None) -> None:
    """Close the turn's checkpoint: keep it when the turn changed something, drop it otherwise."""
    if token is None:
        return
    active = _current.get()
    try:
        _current.reset(token)  # type: ignore[arg-type]
    except (ValueError, LookupError):
        _current.set(None)
    if active is None:
        return
    with _lock:
        if active.files or active.commands:
            active.save()
            _prune(active.folder.parent)
        elif active.folder.exists():
            shutil.rmtree(active.folder, ignore_errors=True)


def _tree_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
            if total > _MAX_TREE_BYTES:
                break
    return total


def capture(path: Path | str) -> None:
    """Save ``path`` as it is now, once per checkpoint, before a tool changes it."""
    active = _current.get()
    if active is None:
        return
    target = Path(path).resolve()
    try:
        rel = target.relative_to(active.workspace).as_posix()
    except ValueError:
        rel = str(target)  # outside the project (an absolute path a tool may still reach)
    with _lock:
        if rel in active.files:
            return
        entry: dict[str, Any] = {"path": str(target)}
        blob = f"{len(active.files):05d}"
        try:
            if not target.exists():
                entry["existed"] = False
            elif target.is_dir():
                entry["existed"], entry["kind"] = True, "dir"
                if _tree_size(target) <= _MAX_TREE_BYTES:
                    shutil.copytree(target, active.folder / "blobs" / blob, symlinks=True)
                    entry["blob"] = blob
                else:
                    entry["skipped"] = "folder too large to save"
            elif target.stat().st_size <= _MAX_FILE_BYTES:
                (active.folder / "blobs").mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, active.folder / "blobs" / blob)
                entry["existed"], entry["kind"], entry["blob"] = True, "file", blob
            else:
                entry["existed"], entry["kind"], entry["skipped"] = True, "file", "file too large to save"
        except OSError as exc:
            entry["skipped"] = f"could not save: {exc}"
        active.files[rel] = entry
        active.save()


def note_command(tool: str, arguments: dict[str, Any]) -> None:
    active = _current.get()
    if active is None:
        return
    detail = arguments.get("command") or arguments.get("message") or arguments.get("tag") or ""
    line = f"{tool}: {str(detail)[:200]}".rstrip(": ")
    with _lock:
        active.commands.append(line)
        active.save()


def before_tool(name: str, arguments: dict[str, Any]) -> None:
    """The before-hook body: save what ``name`` is about to change (never blocks the call)."""
    if _current.get() is None:
        return
    if name in UNTRACKED:
        note_command(name, arguments)
        return
    keys = PATH_ARGS.get(name)
    if not keys:
        return
    from backend import tools as _tools

    for key in keys:
        raw = str(arguments.get(key) or "").strip()
        if not raw:
            continue
        try:
            target = _tools.resolve_user_path(raw)
        except Exception:  # noqa: BLE001 - the tool itself reports a bad path
            continue
        if key == "dest" and target.is_dir() and name in ("copy_path", "move_path"):
            src = str(arguments.get("src") or "").strip()
            if src:
                target = target / Path(src).name  # copying into a folder creates folder/<name>
        capture(target)


def open_changes() -> tuple[Path | None, list[str]]:
    """The open checkpoint's workspace and the files this turn changed so far (for the
    end-of-turn check)."""
    active = _current.get()
    if active is None:
        return None, []
    with _lock:
        return active.workspace, list(active.files)


# -- listing and restoring ------------------------------------------------------------------


def _load(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_checkpoints(workspace: Path | str) -> list[dict[str, Any]]:
    """Checkpoints of ``workspace``, newest first: id, label, when, who, files and commands."""
    scope = _scope_dir(workspace)
    if not scope.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for folder in scope.iterdir():
        data = _load(folder) if folder.is_dir() else None
        if data is None:
            continue
        out.append(
            {
                "id": data.get("id") or folder.name,
                "label": data.get("label") or "",
                "created_at": data.get("created_at") or 0,
                "project_id": data.get("project_id") or "",
                "agent_id": data.get("agent_id") or "",
                "files": sorted((data.get("files") or {}).keys()),
                "commands": list(data.get("commands") or []),
            }
        )
    out.sort(key=lambda c: (c["created_at"], c["id"]), reverse=True)
    return out


def _restore_one(folder: Path, data: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for rel, entry in (data.get("files") or {}).items():
        target = Path(str(entry.get("path") or ""))
        if not str(target):
            continue
        if entry.get("skipped"):
            notes.append(f"not restored: {rel} ({entry['skipped']})")
            continue
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            if entry.get("existed"):
                source = folder / "blobs" / str(entry.get("blob"))
                target.parent.mkdir(parents=True, exist_ok=True)
                if entry.get("kind") == "dir":
                    shutil.copytree(source, target, symlinks=True)
                else:
                    shutil.copy2(source, target)
                notes.append(f"restored {rel}")
            else:
                notes.append(f"removed {rel}")
        except OSError as exc:
            notes.append(f"failed {rel}: {exc}")
    return notes


def restore(workspace: Path | str, checkpoint_id: str) -> dict[str, Any]:
    """Undo checkpoint ``checkpoint_id`` and every later one in ``workspace``, newest first.

    Returns ``{"undone": [ids], "files": [notes], "not_undone": [commands]}``. Raises
    ``KeyError`` for an unknown checkpoint."""
    scope = _scope_dir(workspace)
    with _lock:
        items = [c for c in list_checkpoints(workspace)]
        ids = [c["id"] for c in items]
        if checkpoint_id not in ids:
            raise KeyError(checkpoint_id)
        target = next(c for c in items if c["id"] == checkpoint_id)
        later = [c for c in items if (c["created_at"], c["id"]) >= (target["created_at"], target["id"])]
        undone: list[str] = []
        notes: list[str] = []
        commands: list[str] = []
        for item in later:  # newest first: each one puts back what it found
            folder = scope / item["id"]
            data = _load(folder) or {}
            notes += _restore_one(folder, data)
            commands += list(data.get("commands") or [])
            shutil.rmtree(folder, ignore_errors=True)
            undone.append(item["id"])
    return {"undone": undone, "files": notes, "not_undone": commands}


def _prune(scope: Path) -> None:
    folders = sorted((f for f in scope.iterdir() if f.is_dir()), key=lambda f: f.name)
    for old in folders[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
