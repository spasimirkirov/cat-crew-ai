"""Live model telemetry for the project inspector (Telemetry tab). Never returns an API key."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.api import deps
from backend.services import telemetry

router = APIRouter(prefix="/api", tags=["telemetry"])


@router.get("/telemetry/live")
def live(project_id: str = Query("", description="Project whose context gauge to read; empty = the last run of any project")) -> dict:
    """One snapshot: context tokens of the current session, the active host/model and its status, the
    OpenRouter balance and prompt-cache savings, the Jev gatekeeper's counters, and the local GPU. The
    context gauge also arrives live as ``context.usage`` events on ``/ws/events``."""
    if project_id:
        deps.need_project(project_id)
    else:
        from backend.api import access

        if access.remote_mode():  # "the last run of any project" could be another user's
            raise HTTPException(422, "Pass project_id.")
    return telemetry.live(project_id)
