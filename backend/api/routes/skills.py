from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from backend import agent_context
from backend.api import deps, views
from backend.db import ledger
from backend.platform import auto_skill
from backend.platform.essentials import essential_agents_for_skill
from backend.platform.orchestrator import without_room

router = APIRouter(prefix="/api", tags=["skills"])


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    body: str = ""


class Assignments(BaseModel):
    agent_ids: list[str] = Field(default_factory=list)


class GlobalBody(BaseModel):
    on: bool


class ApproveBody(BaseModel):
    agent_id: str = Field(min_length=1)


class ImportBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


def _row(skill_id: str) -> dict:
    row = next((s for s in ledger.list_skills() if s["id"] == skill_id), None)
    if row is None:
        raise HTTPException(404, f"Unknown skill {skill_id!r}.")
    return row


def _view(row: dict, *, body: bool = False) -> dict:
    text = None
    if body:
        skill = agent_context.find_skill(str(row.get("slug") or row.get("name") or ""))
        if skill is not None:
            text = skill.body
        else:
            try:
                text = Path(str(row.get("playbook_path") or "")).read_text(encoding="utf-8")
            except OSError:
                text = ""
    return views.skill_view(
        row,
        agent_ids=sorted(ledger.agent_ids_for_skill(row["id"])),
        body=text,
        locked_agent_ids=sorted(_locked(row)),
    )


def _locked(row: dict) -> frozenset[str]:
    """Cats for which this playbook is essential (``platform/essentials.ESSENTIAL_SKILLS``)."""
    return essential_agents_for_skill(str(row.get("slug") or row.get("name") or ""))


def _all_views() -> list[dict]:
    rows = ledger.list_skills()
    conn = ledger._connect()
    try:
        by_skill: dict[str, list[str]] = {}
        for r in conn.execute("SELECT skill_id, agent_id FROM agent_skills"):
            by_skill.setdefault(r["skill_id"], []).append(r["agent_id"])
    finally:
        conn.close()
    return [
        views.skill_view(r, agent_ids=by_skill.get(r["id"], []), locked_agent_ids=sorted(_locked(r)))
        for r in rows
    ]


@router.get("/skills")
def list_skills(refresh: bool = False) -> list[dict]:
    if refresh:
        ledger.sync_skills_from_disk()
    return _all_views()


# declared before /skills/{skill_id} so "proposals" is not read as an id
@router.get("/skills/proposals")
def list_proposals() -> list[dict]:
    """Skills the Hermes auto-builder proposed that nobody has approved yet."""
    return [s for s in _all_views() if s["type"] == "auto_generated" and not s["agent_ids"]]


@router.post("/skills/import", status_code=201)
def import_skill(body: ImportBody) -> dict:
    message = agent_context.import_skill_from_url(body.url.strip())
    ledger.sync_skills_from_disk()
    return {"message": message, "skills": _all_views()}


@router.get("/skills/{skill_id}")
def get_skill(skill_id: str) -> dict:
    return _view(_row(skill_id), body=True)


@router.post("/skills", status_code=201)
def create_skill(body: SkillCreate) -> dict:
    try:
        skill = agent_context.create_custom_skill(body.name, body.description, body.body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    skill_id = ledger.upsert_skill(
        slug=skill.name,
        name=skill.name,
        description=skill.description,
        playbook_path=str(skill.path),
        type="custom",
        version=skill.version,
    )
    return _view(_row(skill_id))


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str) -> Response:
    row = _row(skill_id)
    path = Path(str(row.get("playbook_path") or ""))
    if path.name and agent_context.is_bundled_skill(path):
        raise HTTPException(403, "Bundled playbooks cannot be deleted.")
    if _locked(row):
        raise HTTPException(409, f"{row.get('name') or 'This skill'} is essential for at least one cat and cannot be deleted.")
    if path.name:
        agent_context.delete_custom_skill(path)
    ledger.delete_skill(skill_id)
    return Response(status_code=204)


@router.put("/skills/{skill_id}/assignments")
def set_assignments(skill_id: str, body: Assignments) -> dict:
    row = _row(skill_id)
    # Assignments are for specialist cats only. Cat Crew (the orchestrator, legacy
    # id "chief") uses built-in routing, so it is never looked up or updated here.
    wanted = set()
    for aid in without_room(body.agent_ids):
        deps.need_bot(aid)
        wanted.add(aid)
    # Essential playbooks cannot be switched off: whatever the request says, those cats keep them.
    wanted |= {aid for aid in _locked(row) if deps.bot_exists(aid)}
    current = set(without_room(ledger.agent_ids_for_skill(skill_id)))
    for aid in wanted - current:
        ledger.assign_skill(aid, skill_id)
    for aid in current - wanted:
        ledger.unassign_skill(aid, skill_id)
    return _view(_row(skill_id))


@router.put("/skills/{skill_id}/global")
def set_global(skill_id: str, body: GlobalBody) -> dict:
    _row(skill_id)
    ledger.set_skill_global(skill_id, body.on)
    return _view(_row(skill_id))


@router.post("/skills/{skill_id}/approve")
def approve(skill_id: str, body: ApproveBody) -> dict:
    row = _row(skill_id)
    deps.need_bot(body.agent_id)
    auto_skill.approve_proposal(skill_id, body.agent_id)
    return _view(row)


@router.post("/skills/{skill_id}/dismiss", status_code=204)
def dismiss(skill_id: str) -> Response:
    _row(skill_id)
    auto_skill.dismiss_proposal(skill_id)
    return Response(status_code=204)
