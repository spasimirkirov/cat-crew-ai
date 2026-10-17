"""Event bus -> WebSocket fan-out, and the internal-event -> wire-shape mapping.

``EventHub`` subscribes to ``platform.bus`` once. Emitters run on arbitrary
worker threads and hold the bus lock while delivering, so the subscriber does
the bare minimum: hand the event to each client's asyncio queue through
``loop.call_soon_threadsafe`` (non-blocking). Each client task then builds the
wire envelope, enriching events that carry only ids (``task.changed`` ->
the full ``Task``) with SQLite reads done in a worker thread, strictly in
order per client.

A client that stops reading is not allowed to grow memory forever: when its
queue overflows it is closed with code 1013 and reconnects, refetching state
over REST (there is no replay, by contract).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from backend.api import API_VERSION, views
from backend.db import ledger
from backend.platform import bus
from backend.platform.registry import get_project, load_registry

QUEUE_MAX = 2000


def _ts_iso(ts: float) -> str:
    return views.iso_from_ts(ts) or ""


class Client:
    def __init__(self, loop: asyncio.AbstractEventLoop, project_id: str | None) -> None:
        self.loop = loop
        self.project_id = project_id or None
        self.queue: asyncio.Queue[bus.Event | None] = asyncio.Queue(maxsize=QUEUE_MAX)
        self.overflowed = False

    def wants(self, event: bus.Event) -> bool:
        # bot.activity (and any other project-less event) is global -> everyone gets it.
        return self.project_id is None or not event.project_id or event.project_id == self.project_id

    def push(self, event: bus.Event | None) -> None:  # runs on the client's loop
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed = True
            # Make room for the sentinel that ends the sender loop.
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(None)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


class EventHub:
    def __init__(self) -> None:
        self._clients: set[Client] = set()
        self._lock = threading.Lock()
        self._unsub = None

    def attach(self) -> None:
        if self._unsub is None:
            self._unsub = bus.get_bus().subscribe(self._on_event)

    def detach(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def register(self, client: Client) -> None:
        with self._lock:
            self._clients.add(client)

    def unregister(self, client: Client) -> None:
        with self._lock:
            self._clients.discard(client)

    def _on_event(self, event: bus.Event) -> None:  # any thread; must not block
        with self._lock:
            targets = [c for c in self._clients if c.wants(event)]
        for client in targets:
            try:
                client.loop.call_soon_threadsafe(client.push, event)
            except RuntimeError:  # that client's loop already closed
                self.unregister(client)


# -- enrichment ---------------------------------------------------------------------


def hello_envelope(seq: int) -> dict[str, Any]:
    import time

    return {
        "v": API_VERSION,
        "seq": seq,
        "ts": _ts_iso(time.time()),
        "type": "hello",
        "project_id": None,
        "data": {"server_seq": bus.get_bus().last_seq},
    }


def _staffed(event: bus.Event) -> dict[str, Any] | None:
    registry = load_registry()
    bot = registry.bots.get(str(event.data.get("bot_id") or ""))
    project = get_project(event.project_id)
    if bot is None or project is None:
        return None
    joined = views._member_joined_at(project.id).get(bot.id)
    return {"bot_id": bot.id, "agent": views.agent_view(bot), "entry": views.roster_entry(bot, joined)}


def enrich(event: bus.Event) -> dict[str, Any] | None:
    """The ``data`` object for ``event`` in its documented wire shape, or ``None``
    to drop it (the row it refers to no longer exists)."""
    t, d = event.type, event.data
    if t == bus.BOT_STAFFED:
        return _staffed(event)
    if t == bus.BOT_ACTIVITY:
        return {
            "bot_id": d["bot_id"],
            "status": d["status"],
            "state": d["state"],
            "text": d["text"] if d["status"] != "idle" else "",
            "updated_at": _ts_iso(event.ts),
        }
    if t == bus.TASK_CHANGED:
        row = ledger.get_task_with_summary(str(d.get("task_id") or ""))
        return {"task": views.task_view(row)} if row else None
    if t == bus.FILE_CHANGED:
        row = ledger.get_file_change(str(d.get("diff_uuid") or ""))
        return {"change": views.change_view(row)} if row else None
    if t == bus.PROJECT_CHANGED:
        project = get_project(event.project_id)
        return {"project": views.project_view(project)} if project else None
    if t == bus.SKILL_PROPOSED:
        row = next((s for s in ledger.list_skills() if s.get("id") == d.get("skill_id")), None)
        if row is None:
            return None
        skill = views.skill_view(row, agent_ids=sorted(ledger.agent_ids_for_skill(row["id"])))
        return {"skill": skill, "agent_id": d.get("agent_id") or "", "task_id": d.get("task_id") or ""}
    # bot.released, phase.started, chat.*, turn.*, approval.*, ask.* already carry their wire shape.
    return dict(d)


async def envelope_for(event: bus.Event) -> dict[str, Any] | None:
    needs_db = event.type in (
        bus.BOT_STAFFED,
        bus.TASK_CHANGED,
        bus.FILE_CHANGED,
        bus.PROJECT_CHANGED,
        bus.SKILL_PROPOSED,
    )
    data = await asyncio.to_thread(enrich, event) if needs_db else enrich(event)
    if data is None:
        return None
    return {
        "v": API_VERSION,
        "seq": event.seq,
        "ts": _ts_iso(event.ts),
        "type": event.type,
        "project_id": event.project_id or None,
        "data": data,
    }
