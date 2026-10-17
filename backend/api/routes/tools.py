from __future__ import annotations

import importlib
import inspect
import json
import pkgutil

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from backend.api import deps, views
from backend.db import ledger
from backend.platform import tool_registry
from backend.platform.essentials import essential_agents_for_slug
from backend.platform.orchestrator import without_room

router = APIRouter(prefix="/api", tags=["tools"])


class ToolCreate(BaseModel):
    handler: str = Field(min_length=3, description="module:function")


class ImportBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class Assignments(BaseModel):
    agent_ids: list[str] = Field(default_factory=list)


class GlobalBody(BaseModel):
    on: bool


def _row(tool_id: str) -> dict:
    row = next((t for t in ledger.list_tools() if t["id"] == tool_id), None)
    if row is None:
        raise HTTPException(404, f"Unknown tool {tool_id!r}.")
    return row


def _schema(row: dict):
    try:
        return json.loads(row.get("schema_json") or "{}")
    except ValueError:
        return {}


def _view(row: dict, *, with_schema: bool = False) -> dict:
    return views.tool_view(
        row,
        agent_ids=sorted(ledger.agent_ids_for_tool(row["id"])),
        schema=_schema(row) if with_schema else None,
        locked_agent_ids=sorted(essential_agents_for_slug(str(row.get("slug") or ""))),
    )


@router.get("/tools")
def list_tools() -> list[dict]:
    rows = ledger.list_tools()
    conn = ledger._connect()
    try:
        by_tool: dict[str, list[str]] = {}
        for r in conn.execute("SELECT tool_id, agent_id FROM agent_tools"):
            by_tool.setdefault(r["tool_id"], []).append(r["agent_id"])
    finally:
        conn.close()
    return [
        views.tool_view(
            r,
            agent_ids=by_tool.get(r["id"], []),
            locked_agent_ids=sorted(essential_agents_for_slug(str(r.get("slug") or ""))),
        )
        for r in rows
    ]


@router.get("/tools/available")
def available_tools() -> list[dict]:
    """Public functions in ``backend/tool_functions`` (register one with POST /api/tools)."""
    registered = {t["handler_path"] for t in ledger.list_tools()}
    out: list[dict] = []
    pkg = importlib.import_module("backend.tool_functions")
    for mod in pkgutil.iter_modules(pkg.__path__):
        try:
            module = importlib.import_module(f"backend.tool_functions.{mod.name}")
        except Exception:  # noqa: BLE001 - one broken module must not hide the rest
            continue
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != module.__name__:
                continue
            handler = f"{module.__name__}:{name}"
            doc = (inspect.getdoc(fn) or "").split("\n\n")[0].replace("\n", " ")
            out.append({"handler": handler, "name": name, "description": doc, "registered": handler in registered})
    return out


@router.post("/tools/import", status_code=201)
def import_tool(body: ImportBody) -> dict:
    message = tool_registry.import_tool_from_url(body.url.strip())
    return {"message": message, "tools": list_tools()}


@router.get("/tools/{tool_id}")
def get_tool(tool_id: str) -> dict:
    return _view(_row(tool_id), with_schema=True)


@router.post("/tools", status_code=201)
def create_tool(body: ToolCreate) -> dict:
    handler = body.handler.strip()
    try:
        fn = tool_registry.resolve_handler(handler)
        slug = getattr(fn, "__name__", "") or handler.split(":")[-1]
        tool_id = tool_registry.register_tool_function(slug, fn, handler_path=handler, type="system")
    except Exception as exc:  # noqa: BLE001 - surface import/schema problems as 422
        raise HTTPException(422, f"Could not register {handler!r}: {exc}") from exc
    return _view(_row(tool_id), with_schema=True)


@router.put("/tools/{tool_id}/global")
def set_global(tool_id: str, body: GlobalBody) -> dict:
    _row(tool_id)
    ledger.set_tool_global(tool_id, body.on)
    return _view(_row(tool_id))


@router.put("/tools/{tool_id}/assignments")
def set_assignments(tool_id: str, body: Assignments) -> dict:
    row = _row(tool_id)
    # Assignments are for specialist cats only. Cat Crew (the orchestrator, legacy
    # id "chief") uses built-in routing, so it is never looked up or updated here.
    wanted = set()
    for aid in without_room(body.agent_ids):
        deps.need_bot(aid)
        wanted.add(aid)
    # Essential tools cannot be switched off: whatever the request says, those cats keep them.
    locked = essential_agents_for_slug(str(row.get("slug") or ""))
    wanted |= {aid for aid in locked if deps.bot_exists(aid)}
    current = set(without_room(ledger.agent_ids_for_tool(tool_id)))
    for aid in wanted - current:
        ledger.assign_tool(aid, tool_id)
    for aid in current - wanted:
        ledger.unassign_tool(aid, tool_id)
    return _view(_row(tool_id))


@router.delete("/tools/{tool_id}", status_code=204)
def delete_tool(tool_id: str) -> Response:
    row = _row(tool_id)
    if essential_agents_for_slug(str(row.get("slug") or "")):
        raise HTTPException(409, f"{row.get('name') or 'This tool'} is essential for at least one cat and cannot be deleted.")
    ledger.delete_tool(tool_id)
    return Response(status_code=204)
