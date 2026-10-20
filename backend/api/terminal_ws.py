"""``/ws/projects/{project_id}/terminal``: the project's integrated terminal.

Client -> server (JSON text frames)::

    {"type": "run", "command": "npm test"}     run a line in the shell (cwd = the session's directory)
    {"type": "input", "data": "y\\n"}           stdin for the running command
    {"type": "interrupt"}                      kill the running command's process tree
    {"type": "eof"}                            close the running command's stdin
    {"type": "ping"}

Server -> client::

    {"type": "ready", "cwd": "...", "root": "...", "shell": "PowerShell", "platform": "nt"}
    {"type": "started"}
    {"type": "output", "stream": "stdout" | "stderr", "data": "..."}
    {"type": "exit", "code": 0, "cwd": "...", "interrupted": false}
    {"type": "error", "message": "..."}
    {"type": "pong"}

The Host/Origin guard (``LocalOnlyMiddleware``) already rejected any non-local handshake. An unknown
project closes the socket with 4404 without ever starting a shell. The shell dies with the socket.
"""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.platform.registry import get_project
from backend.services import terminal_service

router = APIRouter()

_QUEUE_MAX = 4000


@router.websocket("/ws/projects/{project_id}/terminal")
async def terminal_socket(ws: WebSocket, project_id: str) -> None:
    from backend.api import access
    from backend.services import sandbox

    project = get_project(project_id)
    if project is None or not access.owns(project):
        await ws.close(code=4404)
        return
    if access.remote_mode() and not access.current_user().is_admin and not sandbox.status()["ready"]:
        await ws.close(code=4403)  # a regular user's shell must run in their sandbox
        return
    await ws.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=_QUEUE_MAX)

    def push(event: dict) -> None:  # called from worker threads
        def put() -> None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A client that cannot keep up: drop output, never the exit or an error.
                if event.get("type") != "output":
                    while queue.full():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    queue.put_nowait(event)

        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:  # loop already closed at shutdown
            pass

    service = terminal_service.get_service()
    try:
        root = await asyncio.to_thread(terminal_service.project_root, list(project.folders), project.id)
    except OSError:
        await ws.close(code=1011)
        return
    box = None
    if access.remote_mode() and not access.current_user().is_admin:
        box = sandbox.for_user(access.current_user().id)  # a regular user's shell: their sandbox
    session = service.open(project.id, root, push, sandbox=box)
    try:
        await ws.send_json(
            {
                "type": "ready",
                "cwd": str(session.cwd),
                "root": str(root),
                "shell": terminal_service.shell_label(),
                "platform": os.name,
            }
        )

        async def sender() -> None:
            while True:
                event = await queue.get()
                if event is None:
                    return
                await ws.send_json(event)

        async def receiver() -> None:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "run":
                    await asyncio.to_thread(session.run, str(msg.get("command") or ""))
                elif kind == "input":
                    if not session.send_input(str(msg.get("data") or "")):
                        push({"type": "error", "message": "Nothing is running to receive that input."})
                elif kind == "interrupt":
                    session.interrupt()
                elif kind == "eof":
                    session.close_stdin()
                elif kind == "ping":
                    await ws.send_json({"type": "pong"})

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        done, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                raise exc
    except WebSocketDisconnect:
        pass
    finally:
        service.release(session)  # kills any running command: nothing outlives the socket
