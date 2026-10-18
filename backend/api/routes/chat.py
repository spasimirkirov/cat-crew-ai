from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from backend.api import deps
from backend.core.chat_service import ChatError

router = APIRouter(prefix="/api", tags=["chat"])


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)


class SteerIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ApprovalIn(BaseModel):
    decision: Literal["allow", "allow_always", "deny"]


class AskIn(BaseModel):
    answer: str = ""


@router.get("/projects/{project_id}/threads")
def list_threads(project_id: str, request: Request) -> list[dict]:
    try:
        return deps.chat_service(request).list_threads(project_id)
    except ChatError as exc:
        deps.raise_for_chat_error(exc)


@router.get("/projects/{project_id}/threads/{bot_id}/messages")
def list_messages(
    project_id: str, bot_id: str, request: Request, limit: int = Query(default=200, ge=1, le=2000)
) -> list[dict]:
    try:
        return deps.chat_service(request).list_messages(project_id, bot_id, limit)
    except ChatError as exc:
        deps.raise_for_chat_error(exc)


@router.post("/projects/{project_id}/threads/{bot_id}/messages", status_code=202)
def post_message(project_id: str, bot_id: str, body: MessageIn, request: Request) -> dict:
    try:
        return deps.chat_service(request).submit(project_id, bot_id, body.text)
    except ChatError as exc:
        deps.raise_for_chat_error(exc)


@router.post("/projects/{project_id}/threads/{bot_id}/report", status_code=202)
def report_problem(project_id: str, bot_id: str, body: MessageIn, request: Request) -> dict:
    """Report a problem in a cat's chat (or in Cat Crew): the lead writes a fix plan."""
    try:
        return deps.chat_service(request).report_problem(project_id, bot_id, body.text)
    except ChatError as exc:
        deps.raise_for_chat_error(exc)


@router.post("/projects/{project_id}/retry", status_code=202)
def retry_turn(project_id: str, request: Request) -> dict:
    """Run again the last message whose model was turned away (rate limit, no credit), typically
    right after ``POST /api/models/replace`` switched that model for another."""
    try:
        return deps.chat_service(request).retry(project_id)
    except ChatError as exc:
        deps.raise_for_chat_error(exc)


@router.get("/turns/current")
def current_turn(request: Request) -> dict | None:
    return deps.chat_service(request).current_turn()


@router.get("/turns")
def running_turns(request: Request) -> list[dict]:
    """Every running turn, one per project lane (oldest first)."""
    return deps.chat_service(request).current_turns()


@router.post("/turns/{turn_id}/abort", status_code=202)
def abort_turn(turn_id: str, request: Request) -> dict:
    if not deps.chat_service(request).abort(turn_id):
        raise HTTPException(404, "No such running turn.")
    return {"status": "aborting"}


@router.post("/turns/{turn_id}/steer", status_code=202)
def steer_turn(turn_id: str, body: SteerIn, request: Request) -> dict:
    try:
        ok = deps.chat_service(request).steer(turn_id, body.text)
    except ChatError as exc:
        deps.raise_for_chat_error(exc)
    if not ok:
        raise HTTPException(404, "No such running turn.")
    return {"status": "queued"}


@router.post("/approvals/{approval_id}", status_code=204)
def resolve_approval(approval_id: str, body: ApprovalIn, request: Request) -> Response:
    if not deps.chat_service(request).resolve_approval(approval_id, body.decision):
        raise HTTPException(404, "No such pending approval.")
    return Response(status_code=204)


@router.post("/asks/{ask_id}", status_code=204)
def resolve_ask(ask_id: str, body: AskIn, request: Request) -> Response:
    if not deps.chat_service(request).resolve_ask(ask_id, body.answer):
        raise HTTPException(404, "No such pending question.")
    return Response(status_code=204)
