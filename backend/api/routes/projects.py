from __future__ import annotations

import os
import re
import string
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from backend.api import deps, views
from backend.core.chat_service import ROOM, ChatError
from backend.platform.orchestrator import is_room_id
from backend.platform import bus
from backend.platform.models import Project, new_id
from backend.platform.registry import (
    delete_project,
    get_bot,
    load_registry,
    save_project,
)
from backend.services.workspace import prepare_computer_folder

router = APIRouter(prefix="/api", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    folders: list[str] = Field(default_factory=list)
    bot_ids: list[str] = Field(default_factory=list)
    kickoff: str = ""


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


class WorkspaceBody(BaseModel):
    path: str = Field(min_length=1)


class NewFolder(BaseModel):
    parent: str = Field(max_length=4096)
    name: str = Field(max_length=300)


class RosterAdd(BaseModel):
    bot_id: str = Field(min_length=1)


def _prepare_folder(raw: str) -> str:
    try:
        return str(prepare_computer_folder(raw))
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects")
def list_projects() -> list[dict]:
    return [views.project_view(p) for p in load_registry().projects.values()]


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, request: Request) -> dict:
    name = deps.clean(body.name)
    if not name:
        raise HTTPException(422, "Name is required.")
    folders: list[str] = []
    for raw in body.folders:
        if deps.clean(raw):
            path = _prepare_folder(raw)
            if path not in folders:
                folders.append(path)
    bot_ids: list[str] = []
    for bid in body.bot_ids:
        if get_bot(bid) is None:
            raise HTTPException(404, f"Unknown agent {bid!r}.")
        if bid not in bot_ids:
            bot_ids.append(bid)
    project = Project(
        id=new_id("proj"),
        name=name,
        description=deps.clean(body.description),
        bot_ids=bot_ids,
        folders=folders,
        state={"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    )
    save_project(project)
    deps.ledger_project(project)
    for bid in bot_ids:
        from backend.db import ledger

        ledger.add_project_member(project.id, bid)
    bus.emit_project_changed(project.id)
    kickoff = deps.clean(body.kickoff)
    if kickoff:
        try:
            deps.chat_service(request).submit(project.id, ROOM, kickoff)
        except ChatError:
            pass  # the project exists either way; the user can send the brief again
    return views.project_view(project)


@router.get("/projects/{project_id}")
def get_project_route(project_id: str) -> dict:
    return views.project_view(deps.need_project(project_id))


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch) -> dict:
    project = deps.need_project(project_id)
    if body.name is not None:
        project.name = deps.clean(body.name)
    if body.description is not None:
        project.description = deps.clean(body.description)
    save_project(project)
    deps.ledger_project(project)
    bus.emit_project_changed(project.id)
    return views.project_view(project)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project_route(project_id: str, request: Request) -> Response:
    deps.need_project(project_id)
    if deps.chat_service(request).is_busy(project_id):
        raise HTTPException(409, "Stop the running turn before deleting this project.")
    delete_project(project_id)
    from backend.services import settings_store

    settings_store.clear_project_role_overrides(project_id)  # a deleted project leaves no model picks behind
    from backend.services import terminal_service

    terminal_service.get_service().close_project(project_id)
    from backend.services import usage_meter

    usage_meter.get_meter().forget_project(project_id)  # its context gauge goes with it
    return Response(status_code=204)


@router.put("/projects/{project_id}/workspace")
def set_workspace(project_id: str, body: WorkspaceBody) -> dict:
    project = deps.need_project(project_id)
    path = _prepare_folder(body.path)
    project.folders = [path, *[f for f in project.folders if f != path]]
    save_project(project)
    deps.ledger_project(project)
    bus.emit_project_changed(project.id)
    return views.project_view(project)


@router.post("/projects/{project_id}/reset")
def reset_project(project_id: str, request: Request) -> dict:
    deps.need_project(project_id)
    try:
        deps.chat_service(request).reset_project(project_id)
    except ChatError as exc:
        deps.raise_for_chat_error(exc)
    return views.project_view(deps.need_project(project_id))


# -- fleet roster -------------------------------------------------------------------


@router.get("/projects/{project_id}/roster")
def get_roster(project_id: str) -> list[dict]:
    return views.roster_for(deps.need_project(project_id))


@router.post("/projects/{project_id}/roster", status_code=201)
def add_to_roster(project_id: str, body: RosterAdd) -> dict:
    from backend.db import ledger

    project = deps.need_project(project_id)
    bot = deps.need_bot(body.bot_id)
    added_to_registry = bot.id not in project.bot_ids
    if added_to_registry:
        project.bot_ids.append(bot.id)
        save_project(project)
    deps.ledger_project(project)
    was_member = bot.id in ledger.list_project_members(project.id)
    ledger.add_project_member(project.id, bot.id)  # announces bot.staffed when the row is new
    if was_member and added_to_registry:
        bus.emit_bot_staffed(project.id, bot.id)
    joined = views._member_joined_at(project.id).get(bot.id)
    return views.roster_entry(bot, joined)


@router.delete("/projects/{project_id}/roster/{bot_id}", status_code=204)
def remove_from_roster(project_id: str, bot_id: str) -> Response:
    from backend.db import ledger

    project = deps.need_project(project_id)
    if is_room_id(bot_id):
        raise HTTPException(422, "Cat Crew cannot be removed.")
    if bot_id in project.bot_ids:
        project.bot_ids = [b for b in project.bot_ids if b != bot_id]
        save_project(project)
    ledger.remove_project_member(project.id, bot_id)  # emits bot.released
    return Response(status_code=204)


# -- workspace folder picker --------------------------------------------------------


@router.get("/workspaces/browse")
def browse(path: str = Query(default="")) -> dict:
    """List sub-folders for the launcher's folder picker (directories only)."""
    if not path:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
            return {
                "path": "",
                "parent": None,
                "entries": [{"name": d, "path": d, "is_dir": True} for d in drives],
            }
        target = Path.home()
    else:
        target = Path(path).expanduser()
    try:
        target = target.resolve()
        if not target.is_dir():
            raise HTTPException(422, f"Not a folder: {target}")
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child), "is_dir": True})
            except OSError:
                continue
    except PermissionError as exc:
        raise HTTPException(403, "Permission denied.") from exc
    except OSError as exc:
        raise HTTPException(422, str(exc)) from exc
    parent = target.parent
    return {
        "path": str(target),
        "parent": str(parent) if parent != target else ("" if os.name == "nt" else None),
        "entries": entries,
    }


_BAD_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def clean_folder_name(raw: str) -> str:
    """A safe single folder name, or an HTTPException(422) saying what is wrong with it."""
    name = (raw or "").strip()
    if not name:
        raise HTTPException(422, "Type a name for the new folder.")
    if len(name) > 120:
        raise HTTPException(422, "That folder name is too long (120 characters at most).")
    if name in (".", "..") or _BAD_FOLDER_CHARS.search(name):
        raise HTTPException(422, 'A folder name cannot contain \\ / : * ? " < > | or be "." or "..".')
    if name.endswith((".", " ")):
        raise HTTPException(422, "A folder name cannot end with a dot or a space.")
    if name.split(".")[0].lower() in _WINDOWS_RESERVED:
        raise HTTPException(422, f"{name!r} is a reserved name on Windows. Pick another.")
    return name


@router.post("/workspaces/folders", status_code=201)
def create_folder(body: NewFolder) -> dict:
    """Make a new folder inside ``parent`` (the launcher's folder picker) and return its listing,
    so the picker can step into it. Never creates more than one level: ``name`` is a single name."""
    name = clean_folder_name(body.name)
    if not body.parent.strip():
        raise HTTPException(422, "Open a drive or a folder first, then create the new folder inside it.")
    parent = Path(body.parent).expanduser()
    try:
        parent = parent.resolve()
        if not parent.is_dir():
            raise HTTPException(422, f"Not a folder: {parent}")
        target = parent / name
        if target.exists():
            raise HTTPException(409, f"{name!r} already exists in this folder.")
        target.mkdir()
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(403, "Permission denied: you cannot create folders here.") from exc
    except OSError as exc:
        raise HTTPException(422, f"Could not create the folder: {exc.strerror or exc}") from exc
    return browse(str(target))
