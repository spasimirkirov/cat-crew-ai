"""``/ws/events``: the live event stream (see ``docs/API.md`` §3)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api.events import Client, EventHub, envelope_for, hello_envelope

router = APIRouter()


@router.websocket("/ws/events")
async def events_socket(ws: WebSocket, project_id: str | None = None) -> None:
    from backend.api import access

    hub: EventHub = ws.app.state.hub
    await ws.accept()
    owner = access.current_user().id if access.remote_mode() else None  # signed in by AuthMiddleware
    client = Client(asyncio.get_running_loop(), project_id, owner)
    hub.register(client)
    try:
        await ws.send_json(hello_envelope(0))

        async def sender() -> None:
            while True:
                event = await client.queue.get()
                if event is None:
                    return
                frame = await envelope_for(event)
                if frame is not None:
                    await ws.send_json(frame)

        async def receiver() -> None:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "ping":
                    await ws.send_json({"type": "pong"})
                elif kind == "subscribe":
                    pid = msg.get("project_id")
                    client.project_id = str(pid) if pid else None

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        done, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                raise exc
        if client.overflowed:
            await ws.close(code=1013)  # "try again later": too slow, reconnect + refetch
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(client)
