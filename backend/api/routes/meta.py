from __future__ import annotations

from fastapi import APIRouter, Request

from backend import __version__
from backend.api import access, deps, views
from backend.platform.registry import load_registry

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health(request: Request) -> dict:
    return {"status": "ok", "version": __version__, "busy": deps.chat_service(request).is_busy()}


@router.get("/bootstrap")
def bootstrap(request: Request) -> dict:
    """Everything the UI needs at start-up in one round trip."""
    registry = load_registry()
    assignments = views.Assignments.load()
    agents = [views.agent_view(b, assignments=assignments) for b in registry.bots.values()]
    ids = [a["id"] for a in agents] + [views.ROOM_ID]
    turns = [t for t in deps.chat_service(request).current_turns() if deps.owns_project_id(str(t.get("project_id") or ""))]
    return {
        "agents": agents,
        "projects": [views.project_view(p) for p in registry.projects.values() if access.owns(p)],
        "activity": [views.activity_view(i) for i in ids],
        "current_turn": next(reversed(turns), None),
        "turns": turns,
    }


@router.post("/shutdown", status_code=202)
def shutdown(request: Request) -> dict:
    """Graceful exit (stops the ComfyUI child, releases the instance lock). The
    desktop shell calls this when its window closes; a no-op outside uvicorn."""
    server = getattr(request.app.state, "server", None)
    if server is not None:
        server.should_exit = True
    return {"status": "stopping" if server is not None else "not-running-under-uvicorn"}
