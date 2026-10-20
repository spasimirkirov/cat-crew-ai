"""Browser mode: the backend serves the built front end and trusts only its own loopback origin."""

from __future__ import annotations

import socket

import pytest
from fastapi.testclient import TestClient

from backend import __main__ as entry
from backend.api.app import create_app
from backend.api.security import origin_allowed
from backend.core.chat_service import ChatService


@pytest.fixture
def dist(tmp_path):
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (folder / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    return folder


@pytest.fixture
def client(isolated, dist):
    app = create_app(chat_service=ChatService(use_memory=False), ui_dir=dist)
    with TestClient(app, base_url="http://127.0.0.1:51234") as c:
        yield c


def test_the_page_and_its_assets_are_served_and_the_api_still_wins(client) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "id=root" in page.text
    assert client.get("/assets/app.js").text == "console.log('app')"
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/no-such-route").status_code == 404


def test_the_pages_own_origin_is_allowed_and_any_other_is_not(client) -> None:
    own = {"origin": "http://127.0.0.1:51234"}
    assert client.get("/api/health", headers=own).status_code == 200
    assert client.get("/api/health", headers={"origin": "http://127.0.0.1:9999"}).status_code == 403
    assert client.get("/api/health", headers={"origin": "https://evil.example"}).status_code == 403
    # Starlette's WebSocket test client sends Host "testserver" unless told otherwise.
    with client.websocket_connect("/ws/events", headers={**own, "host": "127.0.0.1:51234"}) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_same_origin_never_covers_a_rebound_host() -> None:
    assert origin_allowed("http://127.0.0.1:51234", [], "127.0.0.1:51234")
    assert origin_allowed("http://localhost:8765", [], "localhost:8765")
    assert not origin_allowed("http://evil.example:8765", [], "evil.example:8765")
    assert not origin_allowed("https://127.0.0.1:51234", [], "127.0.0.1:51234")


def test_without_ui_dir_nothing_is_served_at_the_root(isolated) -> None:
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        assert c.get("/").status_code == 404


def test_free_port_keeps_the_preferred_one_when_it_is_free_and_falls_back_when_taken() -> None:
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        taken = holder.getsockname()[1]
        other = entry.free_port("127.0.0.1", taken)
        assert other != taken and other > 0
    assert entry.free_port("127.0.0.1", 0) > 0


def test_ui_without_a_build_explains_how_to_make_one(monkeypatch, tmp_path, capsys) -> None:
    from backend.api import ui

    monkeypatch.setattr(ui, "FRONTEND_DIST", tmp_path / "missing")
    assert entry.main(["--ui"]) == 2
    assert "npm run build" in capsys.readouterr().err
