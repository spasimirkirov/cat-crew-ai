from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.api import deps, views
from backend.platform.orchestrator import is_room_id
from backend.platform.registry import load_registry

router = APIRouter(prefix="/api", tags=["agents"])

# The crew is defined by CatCrewAI (platform/catcrew.py) and re-synced on every start: the API
# only shows the cats. There is no create / update / delete (they answer 405).


@router.get("/agents")
def list_agents() -> list[dict]:
    assignments = views.Assignments.load()
    return [views.agent_view(b, assignments=assignments) for b in load_registry().bots.values()]


@router.get("/cat-profiles")
def cat_profiles() -> list[dict]:
    """Each cat as the engine sees it: prompt, the tools its turn is bound to, and an icon name."""
    from backend.platform.cat_profiles import load_cat_profiles

    return [p.to_dict(with_prompt=False) for p in load_cat_profiles(load_registry())]


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict:
    if is_room_id(agent_id):  # Cat Crew: the orchestrator, shown as a read-only card
        return views.room_agent(load_registry())
    return views.agent_view(deps.need_bot(agent_id), with_prompt=True)


@router.get("/agents/{agent_id}/activity")
def agent_activity(agent_id: str) -> dict:
    if is_room_id(agent_id):
        return views.activity_view(views.ROOM_ID)
    deps.need_bot(agent_id)
    return views.activity_view(agent_id)


@router.get("/agents/{agent_id}/avatar")
def agent_avatar(agent_id: str):
    if is_room_id(agent_id):  # Cat Crew is not an agent; it wears the bundled default.png
        path = views.room_avatar_file()
    else:
        path = views.avatar_file(deps.need_bot(agent_id))
    if path is None:
        raise HTTPException(404, "No avatar.")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})
