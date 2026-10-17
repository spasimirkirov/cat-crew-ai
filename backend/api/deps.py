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
    project = get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Unknown project {project_id!r}.")
    return project


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
