"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import __version__
from backend.api import terminal_ws, ws
from backend.api.events import EventHub
from backend.api.routes import agents, auth, chat, checkpoints, jev, keys, mcp, meta, models, openrouter, projects, skills, tasks, telemetry, tools
from backend.api.security import LocalOnlyMiddleware, allowed_origins
from backend.core.chat_service import ChatError, ChatService


def _startup_data() -> None:
    """Idempotent, additive start-up: schema, playbook catalog, the core roster."""
    from backend.db import ledger
    from backend.platform.catcrew import seed_catcrew
    from backend.services.settings_store import purge_retired_settings

    purge_retired_settings()  # drop settings (and any secret stored under them) that no longer exist
    ledger.init_db()
    try:  # runs a crashed or killed process left "running" would otherwise stay so forever
        from backend.db.state.repositories import RunRepo
        from backend.platform.registry import store_path

        reaped = RunRepo.reap_orphans(store_path())
        if reaped:
            print(f"[app] marked {reaped} interrupted run(s) as failed", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[app] run reaper skipped: {exc}", flush=True)
    # The core cats come from the code, on every start: a changed prompt or bio reaches the crew
    # without a reinstall (it used to seed only once, so later prompt fixes never arrived).
    seed_catcrew()  # upserts the 8 core cats (photos kept); never wipes anything else
    ledger.sync_skills_from_disk()  # also re-points stored playbook paths at this layout
    try:  # never let a tool-catalog hiccup stop the engine from starting
        from backend.platform.role_tools import ensure_essential_skills, ensure_role_tools

        ensure_role_tools()
        ensure_essential_skills()
    except Exception as exc:  # noqa: BLE001
        print(f"[app] role tools not registered: {exc}", flush=True)


def _start_mcp() -> None:
    """Connect to the MCP servers in the background (Blender, Unity...). A missing one only shows as Offline."""
    from backend.services import mcp_service

    if not mcp_service.enabled_by_environment():
        return
    try:
        mcp_service.get_manager().start()
    except Exception as exc:  # noqa: BLE001 - never let an integration stop the engine from starting
        print(f"[app] MCP not started: {type(exc).__name__}", flush=True)


def _shutdown_runtimes() -> None:
    """Stop CatCrewAI-owned GPU children so exit frees VRAM (ComfyUI). LM Studio is
    a separate app the user manages; it is never stopped from here."""
    try:
        from backend.services.comfyui import stop_comfyui

        msg = stop_comfyui()
        if msg:
            print(f"[app] {msg}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[app] ComfyUI stop failed: {exc}", flush=True)
    try:
        from backend.tool_functions.qa_tools import close_all_sessions

        close_all_sessions()  # headless Chromium started by Aegis/Pixel
    except Exception as exc:  # noqa: BLE001
        print(f"[app] browser close failed: {exc}", flush=True)
    try:
        from backend.services import mcp_service

        mcp_service.get_manager().stop()  # ends the MCP servers this app started (Blender's bridge process...)
    except Exception as exc:  # noqa: BLE001
        print(f"[app] MCP stop failed: {exc}", flush=True)
    try:
        from backend.services.terminal_service import close_all as close_terminals

        close_terminals()  # the user's integrated-terminal shells
    except Exception as exc:  # noqa: BLE001
        print(f"[app] terminal close failed: {exc}", flush=True)


def create_app(
    *, chat_service: ChatService | None = None, startup: bool = True, ui_dir: Path | None = None, remote: bool = False
) -> FastAPI:
    """``remote``: bound to a network address, so every request needs a login (``api/access.py``)."""
    from backend.api import access

    access.configure(remote=remote)
    hub = EventHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if startup:
            _startup_data()
            from backend.services import model_health

            model_health.start_background_check()  # are the saved models still there? (never blocks start-up)
            _start_mcp()
        hub.attach()
        app.state.chat = chat_service or ChatService()
        try:
            yield
        finally:
            app.state.chat.close()
            hub.detach()
            if startup:
                _shutdown_runtimes()
            from backend.services.instance_lock import release

            release()

    app = FastAPI(title="CatCrewAI", version=__version__, lifespan=lifespan)
    app.state.hub = hub
    app.state.server = None

    origins = allowed_origins()
    # Innermost: sign the request in (remote mode) and refuse what this user may not do.
    app.add_middleware(access.AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=600,
    )
    # Outermost: reject a bad Host/Origin before anything (CORS included) sees it.
    app.add_middleware(LocalOnlyMiddleware, extra_origins=origins, remote=remote)

    @app.exception_handler(ChatError)
    async def _chat_error(_request: Request, exc: ChatError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    for module in (meta, auth, projects, agents, tasks, checkpoints, chat, skills, tools, models, keys, telemetry, mcp, jev, openrouter):
        app.include_router(module.router)
    app.include_router(ws.router)
    app.include_router(terminal_ws.router)
    if ui_dir is not None:  # browser mode (api/ui.py): after every route, so the API always wins
        from backend.api.ui import mount_ui

        mount_ui(app, ui_dir)
    return app
