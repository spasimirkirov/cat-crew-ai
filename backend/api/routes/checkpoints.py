"""Checkpoints of a project's folder: list them, and put the files back to before a turn."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from backend.api import deps
from backend.harness import checkpoints

router = APIRouter(prefix="/api", tags=["checkpoints"])


def _folder(project_id: str) -> str:
    project = deps.need_project(project_id)
    return (project.folders or [""])[0]


@router.get("/projects/{project_id}/checkpoints")
def list_checkpoints(project_id: str) -> list[dict]:
    folder = _folder(project_id)
    return checkpoints.list_checkpoints(folder) if folder else []


@router.post("/projects/{project_id}/checkpoints/{checkpoint_id}/restore")
def restore_checkpoint(project_id: str, checkpoint_id: str, request: Request) -> dict:
    """Undo that turn and every later one in the project folder. Refused while a turn runs:
    the agent would keep writing over what was just put back."""
    folder = _folder(project_id)
    if not folder:
        raise HTTPException(409, "This project has no folder, so there is nothing to restore.")
    if deps.chat_service(request).is_busy(project_id):
        raise HTTPException(409, "The crew is still working on this project: stop it first, then restore.")
    try:
        result = checkpoints.restore(folder, checkpoint_id)
    except KeyError:
        raise HTTPException(404, f"Unknown checkpoint {checkpoint_id!r}.") from None
    from backend.platform.bus import emit_file_changed

    emit_file_changed(project_id)
    return result
