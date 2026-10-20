"""Current agent workspace: one or more folders.

A **project computer** lives on ``Project.folders`` (shared by Everyone + 1:1).
A new chat with no project starts with no folders. Relative tool paths and the
terminal then fall back to the session's ``data/scratch/<id>/`` — never CatCrewAI
source. The folder picker starts at ``C:\\`` when Files is empty.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

# The process-wide folders (what the API and the CLI see outside a turn) ...
_folders: list[Path] = []
# ... and a turn's own folders. Turns of different projects run at the same time, each on its own
# thread with its project's computer bound here (``bind_folders``); ``set_folders`` inside a bound
# turn changes only that turn's view.
_turn_folders: ContextVar[list[Path] | None] = ContextVar("catcrew_turn_folders", default=None)
_PICKER_EMPTY = Path(os.environ.get("SystemDrive", "C:") + os.sep)


# A turn of a regular user in remote mode: its file tools reach only the project's folders, never
# the CatCrewAI install tree (api/access.py, tools._allowed_roots).
_confined: ContextVar[bool] = ContextVar("catcrew_turn_confined", default=False)


def bind_confined(on: bool):
    return _confined.set(bool(on))


def reset_confined(token) -> None:
    _confined.reset(token)


def is_confined() -> bool:
    return _confined.get()


def current_folders() -> list[Path]:
    bound = _turn_folders.get()
    return list(bound if bound is not None else _folders)


def bind_folders(paths: list[str | Path]):
    """Give this thread (a turn) its own folders; returns the token for ``reset_folders``."""
    return _turn_folders.set(_resolve_all(paths))


def reset_folders(token) -> None:
    _turn_folders.reset(token)


def current_workspace() -> Path:
    """Primary workspace root, or the active session scratch when Files is empty."""
    folders = current_folders()
    if folders:
        return folders[0]
    from backend.services.session_fs import ensure_session_work_dir

    return ensure_session_work_dir()


def folder_picker_start() -> Path:
    """Where the Add/Choose workspace dialog opens."""
    folders = current_folders()
    if folders:
        return folders[0]
    return _PICKER_EMPTY if _PICKER_EMPTY.is_dir() else Path.home()


def terminal_folders() -> list[Path]:
    """Roots a new terminal may open in. One folder, or every workspace root."""
    folders = current_folders()
    return folders if folders else [current_workspace()]


def set_workspace(path: str | Path) -> Path:
    resolved = _require_dir(path)
    others = [folder for folder in current_folders() if folder != resolved]
    set_folders([resolved, *others])
    return resolved


def add_folder(path: str | Path) -> Path:
    resolved = _require_dir(path)
    folders = current_folders()
    if resolved not in folders:
        folders.append(resolved)
        set_folders(folders)
    return resolved


def remove_folder(path: str | Path) -> None:
    resolved = Path(path).expanduser().resolve()
    folders = [folder for folder in current_folders() if folder != resolved]
    set_folders(folders)


def _resolve_all(paths: list[str | Path]) -> list[Path]:
    resolved: list[Path] = []
    for raw in paths:
        try:
            folder = _require_dir(raw)
        except OSError:
            continue
        if folder not in resolved:
            resolved.append(folder)
    return resolved


def set_folders(paths: list[str | Path]) -> list[Path]:
    global _folders
    resolved = _resolve_all(paths)
    if _turn_folders.get() is not None:
        _turn_folders.set(resolved)
    else:
        _folders = resolved
    return list(resolved)


def is_app_install(path: str | Path) -> bool:
    """True if this is the CatCrewAI source/install tree — never a project computer."""
    from backend.config import BASE_DIR

    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return False
    return resolved == BASE_DIR.resolve()


def prepare_computer_folder(path: str | Path, *, create: bool = True) -> Path:
    """Resolve a project computer folder; create it if needed. Rejects the CatCrewAI install."""
    resolved = Path(path).expanduser().resolve()
    if is_app_install(resolved):
        raise ValueError("Do not use the CatCrewAI install folder as the project computer.")
    if not resolved.exists() and create:
        resolved.mkdir(parents=True, exist_ok=True)
    return _require_dir(resolved)


def _require_dir(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Folder does not exist: {resolved}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"Not a folder: {resolved}")
    return resolved
