"""``/ws/events``: live pop-in, task/file events, project filter, ping/subscribe."""

from __future__ import annotations

import queue
import threading

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.chat_service import ChatService
from backend.platform import bus

BASE = "http://127.0.0.1:8765"


def _connect(client, path, **headers):
    # Starlette's WS test client hard-codes Host "testserver"; use the real loopback one.
    return client.websocket_connect(path, headers={"host": "127.0.0.1:8765", **headers})


class Reader:
    """Reads WS frames on a helper thread so a missing event fails fast, not hangs."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.q: queue.Queue = queue.Queue()
        self.frames: list[dict] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                self.q.put(self.ws.receive_json())
        except Exception as exc:  # noqa: BLE001 - socket closed
            self.q.put(exc)

    def next(self, timeout: float = 5.0) -> dict:
        item = self.q.get(timeout=timeout)
        if isinstance(item, Exception):
            raise item
        self.frames.append(item)
        return item

    def until(self, pred, timeout: float = 5.0) -> dict:
        while True:
            frame = self.next(timeout)
            if pred(frame):
                return frame

    def quiet(self, wait: float = 0.4) -> list[dict]:
        out = []
        while True:
            try:
                out.append(self.next(wait))
            except queue.Empty:
                return out


@pytest.fixture
def client(isolated):
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url=BASE) as c:
        yield c


def _project(client, tmp_path, name="Alpha", **extra):
    r = client.post("/api/projects", json={"name": name, "folders": [str(tmp_path / name)], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_hello_ping_pong(client):
    with _connect(client, "/ws/events") as ws:
        r = Reader(ws)
        hello = r.next()
        assert hello["type"] == "hello" and hello["v"] == 1 and hello["project_id"] is None
        assert "server_seq" in hello["data"]
        ws.send_json({"type": "ping"})
        assert r.until(lambda f: f.get("type") == "pong")["type"] == "pong"


def test_bot_pops_into_the_sidebar_the_moment_it_is_staffed(client, tmp_path):
    p = _project(client, tmp_path)
    with _connect(client, f"/ws/events?project_id={p['id']}") as ws:
        r = Reader(ws)
        r.next()  # hello
        added = client.post(f"/api/projects/{p['id']}/roster", json={"bot_id": "aegis"})
        assert added.status_code == 201
        frame = r.until(lambda f: f["type"] == "bot.staffed")
        assert frame["project_id"] == p["id"]
        assert frame["data"]["bot_id"] == "aegis"
        assert frame["data"]["agent"]["name"]  # full agent card: no refetch needed
        assert frame["data"]["entry"]["agent"]["id"] == "aegis"
        assert isinstance(frame["seq"], int)


def test_task_assignment_pops_the_bot_then_announces_the_task(client, tmp_path):
    p = _project(client, tmp_path)
    with _connect(client, f"/ws/events?project_id={p['id']}") as ws:
        r = Reader(ws)
        r.next()
        made = client.post(
            f"/api/projects/{p['id']}/tasks", json={"assigned_to": "pulse", "title": "Ship it"}
        ).json()
        staffed = r.until(lambda f: f["type"] == "bot.staffed")
        task = r.until(lambda f: f["type"] == "task.changed")
        assert staffed["data"]["bot_id"] == "pulse"
        assert staffed["seq"] < task["seq"], "the specialist appears before its task"
        assert task["data"]["task"]["id"] == made["id"]
        assert task["data"]["task"]["status"] == "pending"

        client.patch(f"/api/projects/{p['id']}/tasks/{made['id']}", json={"status": "in_progress"})
        upd = r.until(lambda f: f["type"] == "task.changed" and f["data"]["task"]["status"] == "in_progress")
        assert upd["seq"] > task["seq"]


def test_activity_status_transitions_are_streamed_to_everyone(client, tmp_path):
    p = _project(client, tmp_path)
    q = _project(client, tmp_path, name="Beta")
    from backend.platform import activity_board

    with _connect(client, f"/ws/events?project_id={q['id']}") as ws:
        r = Reader(ws)
        r.next()
        activity_board.set_working("pulse", "coding", "editing app.py")
        working = r.until(lambda f: f["type"] == "bot.activity")
        assert working["project_id"] is None  # global: reaches a client filtered to another project
        assert working["data"]["bot_id"] == "pulse"
        assert working["data"]["status"] == "coding" and working["data"]["text"] == "editing app.py"
        activity_board.mark_idle("pulse")
        idle = r.until(lambda f: f["type"] == "bot.activity" and f["data"]["status"] == "idle")
        assert idle["data"]["text"] == ""
    assert p["id"] != q["id"]


def test_project_filter_and_subscribe(client, tmp_path):
    a = _project(client, tmp_path, name="A")
    b = _project(client, tmp_path, name="B")
    with _connect(client, f"/ws/events?project_id={a['id']}") as ws:
        r = Reader(ws)
        r.next()
        client.post(f"/api/projects/{b['id']}/roster", json={"bot_id": "lex"})
        assert [f for f in r.quiet() if f["type"] == "bot.staffed"] == []

        ws.send_json({"type": "subscribe", "project_id": b["id"]})
        # let the receiver apply the subscription before the next event
        ws.send_json({"type": "ping"})
        r.until(lambda f: f.get("type") == "pong")
        client.post(f"/api/projects/{b['id']}/roster", json={"bot_id": "blaze"})
        got = r.until(lambda f: f["type"] == "bot.staffed")
        assert got["project_id"] == b["id"] and got["data"]["bot_id"] == "blaze"


def test_unfiltered_client_sees_every_project(client, tmp_path):
    a = _project(client, tmp_path, name="A")
    b = _project(client, tmp_path, name="B")
    with _connect(client, "/ws/events") as ws:
        r = Reader(ws)
        r.next()
        client.post(f"/api/projects/{a['id']}/roster", json={"bot_id": "lex"})
        client.post(f"/api/projects/{b['id']}/roster", json={"bot_id": "blaze"})
        first = r.until(lambda f: f["type"] == "bot.staffed")
        second = r.until(lambda f: f["type"] == "bot.staffed")
        assert {first["project_id"], second["project_id"]} == {a["id"], b["id"]}
        assert first["seq"] < second["seq"]


def test_file_change_event_carries_the_change_row(client, tmp_path):
    from backend.db import ledger

    p = _project(client, tmp_path)
    with _connect(client, f"/ws/events?project_id={p['id']}") as ws:
        r = Reader(ws)
        r.next()
        uid = ledger.log_file_change(p["id"], "", "pulse", "src/x.py", "+x = 1\n")
        frame = r.until(lambda f: f["type"] == "file.changed")
        assert frame["data"]["change"]["file_path"] == "src/x.py"
        assert frame["data"]["change"]["diff_uuid"] == uid
        assert "diff_content" not in frame["data"]["change"]  # heavy body is fetched on demand


def test_event_for_a_deleted_row_is_dropped_not_fatal(client, tmp_path):
    p = _project(client, tmp_path)
    with _connect(client, f"/ws/events?project_id={p['id']}") as ws:
        r = Reader(ws)
        r.next()
        bus.emit_task_changed(p["id"], "task_that_does_not_exist")
        bus.emit_bot_staffed(p["id"], "cipher")
        frame = r.until(lambda f: f["type"] in ("task.changed", "bot.staffed"))
        assert frame["type"] == "bot.staffed"  # the dangling task event vanished, the stream lives


def test_overflowing_client_queue_trips_the_close_sentinel():
    """A client that stops reading must not grow memory: on overflow the queue is
    trimmed, flagged, and ends with the sentinel that makes the socket close 1013."""
    import asyncio

    from backend.api import events

    async def scenario():
        client = events.Client(asyncio.get_running_loop(), None)
        client.queue = asyncio.Queue(maxsize=3)
        for i in range(10):
            client.push(bus.Event("chat.activity", "p", {"n": i}, i, 0.0))
        return client

    client = asyncio.run(scenario())
    assert client.overflowed
    assert client.queue.qsize() <= 3
    drained = []
    while not client.queue.empty():
        drained.append(client.queue.get_nowait())
    assert drained[-1] is None, "the sentinel ends the sender loop"


def test_wire_ordering_matches_emission_order(client, tmp_path):
    """Frames for one project arrive in strictly increasing seq (the ordering promise in docs/API.md)."""
    p = _project(client, tmp_path)
    with _connect(client, f"/ws/events?project_id={p['id']}") as ws:
        r = Reader(ws)
        r.next()
        for bot in ("cipher", "vanguard", "pixel", "pulse"):
            client.post(f"/api/projects/{p['id']}/tasks", json={"assigned_to": bot, "title": f"t-{bot}"})
        seen = [f for f in r.quiet(0.8) if "seq" in f]
    seqs = [f["seq"] for f in seen]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert [f["data"]["bot_id"] for f in seen if f["type"] == "bot.staffed"] == ["cipher", "vanguard", "pixel", "pulse"]
