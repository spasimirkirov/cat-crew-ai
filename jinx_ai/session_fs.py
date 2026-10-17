"""Per-chat scratch under the app data dir: ``data/scratch/<session-id>/``.

Todo and activity logs live with Jinx, not inside the user's project.
Optional project playbooks may already exist at ``<folder>/.jinx-ai/skills`` —
we never create that tree. If the project has a ``.gitignore``, we append
``.jinx-ai/`` so user-added overlay files stay out of git.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import BASE_DIR

JINX_AI_DIR = ".jinx-ai"
SCRATCH_ROOT = BASE_DIR / "data" / "scratch"
_GITIGNORE_LINE = ".jinx-ai/"


def safe_session_id(session_id: str) -> str:
    raw = str(session_id or "").strip() or "unknown"
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)[:80]


def resolve_session_id(session_id: str = "") -> str:
    text = str(session_id or "").strip()
    if not text:
        from .trace import current_session_id

        text = current_session_id()
    if not text:
        from .sessions import active_session_id

        text = str(active_session_id() or "").strip()
    return safe_session_id(text) if text else "unknown"


def scratch_root() -> Path:
    return SCRATCH_ROOT


def gitignore_covers_jinx_ai(text: str) -> bool:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        token = line.rstrip("/")
        if token in {JINX_AI_DIR, "**/.jinx-ai"} or token.endswith("/.jinx-ai"):
            return True
    return False


def ignore_jinx_ai_in_gitignore(folder: Path) -> None:
    """Append ``.jinx-ai/`` to an existing .gitignore. Never create the file."""
    path = Path(folder) / ".gitignore"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if gitignore_covers_jinx_ai(text):
        return
    if text and not text.endswith("\n"):
        text += "\n"
    try:
        path.write_text(text + _GITIGNORE_LINE + "\n", encoding="utf-8")
    except OSError:
        pass


def ensure_workspace_gitignore(folders: list[str | Path] | None = None) -> None:
    """When folders are attached, ignore ``.jinx-ai/`` if a .gitignore already exists."""
    from .workspace import current_folders

    roots = [Path(item) for item in folders] if folders is not None else list(current_folders())
    for folder in roots:
        ignore_jinx_ai_in_gitignore(folder)


def session_work_dir(session_id: str = "", workspace: Path | None = None) -> Path:
    del workspace  # Scratch is always under app data, never the project.
    return scratch_root() / resolve_session_id(session_id)


def ensure_session_work_dir(session_id: str = "", workspace: Path | None = None) -> Path:
    path = session_work_dir(session_id, workspace)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _rmtree_safe(path: Path, *, expected_parent_name: str, sid: str) -> None:
    try:
        resolved = path.resolve()
    except OSError:
        return
    if resolved.name != sid or resolved.parent.name != expected_parent_name:
        return
    if not resolved.is_dir():
        return
    shutil.rmtree(resolved, ignore_errors=True)


def remove_session_work_dir(
    session_id: str,
    *,
    workspace: Path | None = None,
    folders: list[str | Path] | None = None,
) -> None:
    """Remove ``data/scratch/<id>/`` and any legacy ``<ws>/.jinx-ai/<id>/``."""
    sid = safe_session_id(session_id)
    if not sid or sid == "unknown":
        return
    _rmtree_safe(scratch_root() / sid, expected_parent_name="scratch", sid=sid)

    roots: list[Path] = []
    if workspace is not None:
        roots.append(Path(workspace))
    if folders:
        roots.extend(Path(item) for item in folders)
    if not roots:
        from .workspace import current_folders

        roots = list(current_folders() or [])
    seen: set[Path] = set()
    for root in roots:
        path = Path(root) / JINX_AI_DIR / sid
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        _rmtree_safe(path, expected_parent_name=JINX_AI_DIR, sid=sid)


# Back-compat alias used by older call sites / docs.
def jinx_ai_root(workspace: Path | None = None) -> Path:
    del workspace
    return scratch_root()
