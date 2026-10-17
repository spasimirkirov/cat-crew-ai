"""MCP integrations (Blender, Unity, ...): live status and a way to look again without restarting the engine.

Servers are configured in ``backend/config/mcp_servers.json`` (see ``services/mcp_service.py``). Nothing here
returns a secret: no environment variable, header or credential ever appears in a response.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.services import mcp_service

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _cat_names(ids: list[str]) -> list[dict[str, str]]:
    """``[{id, name}]`` for the cats a server is bound to (the id when the registry does not know it)."""
    names: dict[str, str] = {}
    try:
        from backend.platform.registry import load_registry

        for bot in load_registry().bots.values():
            names[str(bot.id).lower()] = str(bot.name)
    except Exception:  # noqa: BLE001 - names are a nicety
        pass
    return [{"id": i, "name": names.get(i, i.title())} for i in ids]


def _payload(manager: mcp_service.MCPManager) -> dict[str, Any]:
    servers = manager.status()
    for view in servers:
        view["bound_cats"] = _cat_names(view["bound_to"])
    return {
        "servers": servers,
        "problems": manager.problems(),
        "sdk_installed": manager.mcp_installed(),
        "running": manager.running(),
        "enabled": mcp_service.enabled_by_environment(),
    }


@router.get("/servers")
def servers() -> dict[str, Any]:
    """Every configured server with its live status (``connected``, ``connecting``, ``reconnecting``,
    ``disconnected``, ``disabled``), whether the program behind it answers (``ready``), the cats it is bound
    to and how many tools it offers."""
    return _payload(mcp_service.get_manager())


async def _hard(request: Request) -> bool:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an empty or absent body is normal
        return False
    return bool(body.get("hard")) if isinstance(body, dict) else False


@router.post("/refresh")
async def refresh(request: Request) -> dict[str, Any]:
    """*Refresh / Reconnect MCP*: re-reads ``mcp_servers.json``, re-discovers the tools of every connected
    server (``tools/list``) and reconnects the ones that are down now instead of waiting for their back-off.
    ``{"hard": true}`` reconnects every server. Waits up to 15 s for the outcome."""
    import asyncio

    hard = await _hard(request)
    manager = mcp_service.get_manager()
    if not mcp_service.enabled_by_environment():
        raise HTTPException(409, "MCP integrations are switched off (CATCREW_MCP=0).")
    await asyncio.to_thread(manager.refresh, None, hard=hard)
    return _payload(manager)


@router.post("/servers/{server_id}/reconnect")
async def reconnect(server_id: str) -> dict[str, Any]:
    """Close one server's connection and open a new one."""
    import asyncio

    manager = mcp_service.get_manager()
    if not mcp_service.enabled_by_environment():
        raise HTTPException(409, "MCP integrations are switched off (CATCREW_MCP=0).")
    try:
        await asyncio.to_thread(manager.reconnect, server_id)
    except KeyError:
        raise HTTPException(404, f"There is no MCP server called '{server_id}'.") from None
    payload = _payload(manager)
    return {**payload, "server": next((s for s in payload["servers"] if s["id"] == server_id), None)}


@router.get("/servers/{server_id}/tools")
def tools(server_id: str) -> list[dict[str, Any]]:
    """The tools a connected server offers, and which of them are in the bound cats' tool list."""
    rows = mcp_service.get_manager().tool_rows(server_id)
    if rows is None:
        raise HTTPException(404, f"There is no MCP server called '{server_id}'.")
    return rows
