"""Chat sessions: each has a title, workspace folder, and message history."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import BASE_DIR
from backend.services.workspace import set_folders

SESSIONS_DIR = BASE_DIR / "data" / "sessions"
INDEX_PATH = SESSIONS_DIR / "index.json"

# The API server has request threads and turn threads writing sessions at the
# same time. The index is a read-modify-write file, so serialise it; and write
# every file atomically so a concurrent reader never sees a half-written JSON.
_index_lock = threading.RLock()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_dir() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def format_session_title(
    created_at: str | None = None,
    *,
    mode: str = "developer",
) -> str:
    """Mode-agnostic name: CatCrewAI - Session - YYYY-MM-DD HH:MM."""
    del mode
    dt = None
    raw = (created_at or "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None:
        dt = datetime.now(timezone.utc)
    local = dt.astimezone()
    return f"CatCrewAI - Session - {local.strftime('%Y-%m-%d %H:%M')}"


def _load_index() -> dict[str, Any]:
    _ensure_dir()
    if not INDEX_PATH.is_file():
        return {"active_id": None, "sessions": []}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active_id": None, "sessions": []}
    if not isinstance(data, dict):
        return {"active_id": None, "sessions": []}
    data.setdefault("active_id", None)
    data.setdefault("sessions", [])
    if not isinstance(data["sessions"], list):
        data["sessions"] = []
    return data


def _save_index(data: dict[str, Any]) -> None:
    _ensure_dir()
    _atomic_write(INDEX_PATH, json.dumps(data, ensure_ascii=False, indent=2))


def _session_workspace_value(session: dict[str, Any]) -> str:
    folders = session_folders(session)
    if folders:
        return folders[0]
    return str(session.get("workspace") or "").strip()


def _summarize(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session.get("id"),
        "title": session.get("title") or "Untitled",
        "workspace": _session_workspace_value(session),
        "folders": session_folders(session),
        "provider": session.get("provider") or "",
        "model": session.get("model") or "",
        "updated_at": session.get("updated_at") or _now(),
        "created_at": session.get("created_at") or _now(),
        "message_count": len(session.get("messages") or []),
        "project_id": str(session.get("project_id") or ""),
        "bot_id": str(session.get("bot_id") or ""),
    }


def _upsert_index(summary: dict[str, Any], active: bool = False) -> None:
    with _index_lock:
        index = _load_index()
        sessions = [s for s in index["sessions"] if s.get("id") != summary["id"]]
        sessions.insert(0, summary)
        index["sessions"] = sessions
        if active:
            index["active_id"] = summary["id"]
        _save_index(index)


def session_folders(session: dict[str, Any] | None) -> list[str]:
    session = session or {}
    raw = session.get("folders")
    folders: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            text = str(item or "").strip()
            if text:
                folders.append(str(Path(text).expanduser()))
    elif str(session.get("workspace") or "").strip():
        # Legacy single-workspace field. Old builds defaulted this to the app
        # install/source tree — treat that alone as "no project attached".
        ws = str(session.get("workspace")).strip()
        try:
            if Path(ws).expanduser().resolve() == BASE_DIR.resolve():
                return []
        except OSError:
            pass
        folders = [ws]
    unique: list[str] = []
    for folder in folders:
        path = (
            str(Path(folder).expanduser().resolve())
            if Path(folder).expanduser().exists()
            else folder
        )
        if path not in unique:
            unique.append(path)
    return unique


def apply_session_workspace(session: dict[str, Any]) -> None:
    folders = session_folders(session)
    set_folders(folders)
    try:
        from backend.services.session_fs import ensure_session_work_dir, ensure_workspace_gitignore

        ensure_session_work_dir(str(session.get("id") or ""))
        if folders:
            ensure_workspace_gitignore(folders)
    except OSError:
        pass


def new_session(
    workspace: str | Path | None = None,
    title: str | None = None,
    *,
    activate: bool = True,
    mode: str = "developer",
) -> dict[str, Any]:
    from backend.services.settings_store import resolve_chat_brain

    session_id = uuid.uuid4().hex[:12]
    now = _now()
    host, model = resolve_chat_brain()
    if workspace is None:
        folders: list[str] = []
        workspace_value = ""
    else:
        folder = Path(workspace).expanduser().resolve()
        folders = [str(folder)]
        workspace_value = str(folder)
    session = {
        "id": session_id,
        "title": title or format_session_title(now, mode=mode),
        "workspace": workspace_value,
        "folders": folders,
        "provider": host,
        "model": model,
        "mode": mode,
        "messages": [],
        "head": None,
        "created_at": now,
        "updated_at": now,
    }
    save_session(session, activate=activate)
    if activate:
        apply_session_workspace(session)
    return session


def load_session(session_id: str) -> dict[str, Any] | None:
    path = _session_path(session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    data.setdefault("messages", [])
    data.setdefault("title", "Untitled")
    data.setdefault("folders", session_folders(data))
    folders = session_folders(data)
    if folders:
        data["workspace"] = folders[0]
    else:
        # Empty Files: do not keep a stale CatCrewAI-source workspace path.
        data["workspace"] = ""
    data.setdefault("provider", "")
    data.setdefault("model", "")
    from backend.services.api_access import CHAT_HOSTS
    from backend.services.lm_runtime import normalize_host

    data["provider"] = normalize_host(data.get("provider"))
    if data.get("provider") not in CHAT_HOSTS:
        data["provider"] = ""  # follow the Chat role (an old LM Studio pick included)
    from backend.services.session_tree import ensure_tree

    return ensure_tree(data)


def save_session(session: dict[str, Any], *, activate: bool = False) -> dict[str, Any]:
    session = dict(session)
    session["updated_at"] = _now()
    session.setdefault("id", uuid.uuid4().hex[:12])
    session.setdefault("messages", [])
    _ensure_dir()
    _atomic_write(
        _session_path(session["id"]), json.dumps(session, ensure_ascii=False, indent=2)
    )
    _upsert_index(_summarize(session), active=activate)
    return session


def delete_session(session_id: str) -> None:
    session = load_session(session_id)
    folders = session_folders(session) if session else []
    workspace = str((session or {}).get("workspace") or "").strip()
    if workspace and workspace not in folders:
        folders.append(workspace)
    path = _session_path(session_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    with _index_lock:
        index = _load_index()
        index["sessions"] = [s for s in index["sessions"] if s.get("id") != session_id]
        if index.get("active_id") == session_id:
            index["active_id"] = index["sessions"][0]["id"] if index["sessions"] else None
        _save_index(index)
    try:
        from backend.services.session_fs import remove_session_work_dir

        remove_session_work_dir(session_id, folders=folders)
    except OSError:
        pass


def list_sessions() -> list[dict[str, Any]]:
    index = _load_index()
    return list(index.get("sessions") or [])


def active_session_id() -> str | None:
    return _load_index().get("active_id")


def set_active_session(session_id: str) -> dict[str, Any] | None:
    session = load_session(session_id)
    if session is None:
        return None
    with _index_lock:
        index = _load_index()
        index["active_id"] = session_id
        _save_index(index)
    apply_session_workspace(session)
    return session


def bind_project_computer(session: dict[str, Any], project_id: str = "") -> None:
    """Give every project thread the project's shared computer (source of truth)."""
    pid = str(project_id or session.get("project_id") or "").strip()
    if not pid:
        apply_session_workspace(session)
        return
    try:
        from backend.platform.registry import get_project

        project = get_project(pid)
        folders = [str(p) for p in (project.folders if project else []) if p]
    except Exception:  # noqa: BLE001
        folders = []
    session["folders"] = folders
    session["workspace"] = folders[0] if folders else ""
    apply_session_workspace(session)


def _thread_owner(raw: Any) -> str:
    """The bot id a project thread belongs to. Cat Crew's own thread is stored
    as ``crew`` now, ``chief`` (or empty) in sessions written before the rename."""
    from backend.platform.orchestrator import canonical_room_id

    return canonical_room_id(str(raw or ""))


def find_project_thread(project_id: str, bot_id: str = "crew") -> dict[str, Any] | None:
    pid = str(project_id or "").strip()
    bid = _thread_owner(bot_id)
    if not pid:
        return None
    for row in list_sessions():
        session = load_session(str(row.get("id") or ""))
        if session is None:
            continue
        if str(session.get("project_id") or "") != pid:
            continue
        if _thread_owner(session.get("bot_id")) == bid:
            return session
    return None


def project_thread_ids(project_id: str) -> dict[str, str]:
    """``{bot_id: session_id}`` for a project's threads, newest first wins.

    Read-only: unlike ``open_project_thread`` it never touches the active-session
    pointer or the process-wide workspace, so the API can call it while a turn
    is running. Index rows written before ``project_id``/``bot_id`` were part of
    the summary are resolved by loading the session file once."""
    pid = str(project_id or "").strip()
    out: dict[str, str] = {}
    if not pid:
        return out
    for row in list_sessions():
        row_pid = row.get("project_id")
        if row_pid is None:
            loaded = load_session(str(row.get("id") or ""))
            if loaded is None:
                continue
            row_pid = loaded.get("project_id") or ""
            row_bot = loaded.get("bot_id") or ""
        else:
            row_bot = row.get("bot_id") or ""
        if str(row_pid) != pid:
            continue
        out.setdefault(_thread_owner(row_bot), str(row.get("id") or ""))
    return out


def open_project_thread(
    project_id: str,
    bot_id: str = "crew",
    *,
    title: str | None = None,
) -> dict[str, Any]:
    existing = find_project_thread(project_id, bot_id)
    if existing is not None:
        loaded = set_active_session(str(existing.get("id") or ""))
        session = loaded or existing
        bind_project_computer(session, project_id)
        save_session(session, activate=True)
        return session
    session = new_session(title=title, activate=True)
    session["project_id"] = str(project_id or "").strip()
    session["bot_id"] = _thread_owner(bot_id)
    bind_project_computer(session, project_id)
    save_session(session, activate=True)
    return session


def use_active_session(session: dict[str, Any]) -> dict[str, Any]:
    """Make an already-loaded, in-memory session dict the active one
    without the disk round-trip ``set_active_session``/``load_session``
    would do.

    ``open_project_thread`` re-reads the session file from disk every
    time it is called, which returns a brand-new dict object even when
    the on-disk id is identical to one already held in memory. Callers
    that track "does this chat own the turn that's currently running" by
    object identity (``chat_window.ChatWindow._turn_session is
    self.session``) need that identity preserved across navigation, or a
    round trip back to the same project/bot the turn is running in would
    look like a different chat and silently drop its live activity. This
    just flips the active-session pointer and applies the given dict's
    own workspace, keeping the caller's object as the one now considered
    "current" instead of swapping in a freshly deserialized copy.
    """
    sid = str(session.get("id") or "").strip()
    if not sid:
        return session
    with _index_lock:
        index = _load_index()
        index["active_id"] = sid
        _save_index(index)
    apply_session_workspace(session)
    return session


def fork_session(source: dict[str, Any], title: str | None = None) -> dict[str, Any]:
    base = dict(source)
    session_id = uuid.uuid4().hex[:12]
    now = _now()
    parent_title = str(base.get("title") or "Untitled")
    folders = session_folders(base)
    session = {
        "id": session_id,
        "title": (title or f"{parent_title} (fork)")[:80],
        "workspace": folders[0] if folders else "",
        "folders": folders,
        "provider": base.get("provider") or "",
        "model": base.get("model") or "",
        "mode": base.get("mode") or "developer",
        "messages": list(base.get("messages") or []),
        "head": base.get("head"),
        "parent_id": base.get("id"),
        "created_at": now,
        "updated_at": now,
    }
    for key in ("project_id", "bot_id"):
        value = str(base.get(key) or "").strip()
        if value:
            session[key] = value
    save_session(session, activate=True)
    apply_session_workspace(session)
    return session


