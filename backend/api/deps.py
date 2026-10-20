"""Shared helpers for route modules."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from backend.core.chat_service import ChatError, ChatService
from backend.platform.models import Bot, Project
from backend.platform.registry import get_bot, get_project


def chat_service(request: Request) -> ChatService:
    return request.app.state.chat


def need_project(project_id: str) -> Project:
    """The project, or 404 -- also when it belongs to another user (remote mode): another user's
    project is indistinguishable from one that does not exist."""
    from backend.api import access

    project = get_project(project_id)
    if project is None or not access.owns(project):
        raise HTTPException(404, f"Unknown project {project_id!r}.")
    return project


def owns_project_id(project_id: str) -> bool:
    """Is ``project_id`` one of the current user's projects (always true in local mode)?"""
    from backend.api import access

    if not access.remote_mode():
        return True
    project = get_project(project_id) if project_id else None
    return project is not None and access.owns(project)


def need_bot(bot_id: str) -> Bot:
    bot = get_bot(bot_id)
    if bot is None:
        raise HTTPException(404, f"Unknown agent {bot_id!r}.")
    return bot


def bot_exists(bot_id: str) -> bool:
    return get_bot(bot_id) is not None


def raise_for_chat_error(exc: ChatError) -> None:
    raise HTTPException(exc.status, str(exc)) from exc


def ledger_project(project: Project) -> None:
    """Make sure the task ledger knows this project (``tasks`` reference it)."""
    from backend.db import ledger

    ledger.ensure_project(project.id, project.name, project.folders[0] if project.folders else "")


def clean(text: Any) -> str:
    return str(text or "").strip()
