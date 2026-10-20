"""Remote access: accounts, sessions, invites, admin-only routes and per-user projects."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.api import access
from backend.api.app import create_app
from backend.core.chat_service import ChatService
from backend.services import auth

HOST = "cats.example.com"
BASE = f"https://{HOST}"


# -- the account store ---------------------------------------------------------------------


def test_passwords_are_salted_scrypt_and_verify() -> None:
    first, second = auth.hash_password("correct horse"), auth.hash_password("correct horse")
    assert first != second and first.startswith("scrypt$")
    assert auth.verify_password("correct horse", first)
    assert not auth.verify_password("wrong horse", first)
    assert not auth.verify_password("x", "garbage")


def test_the_first_admin_needs_the_console_code_and_only_one_can_register() -> None:
    with pytest.raises(auth.AuthError, match="setup code"):
        auth.register_first_admin("ada", "a-long-password")
    code = auth.issue_setup_code()
    with pytest.raises(auth.AuthError, match="setup code"):
        auth.register_first_admin("ada", "a-long-password", "WRONG")
    admin = auth.register_first_admin("ada", "a-long-password", code.lower())
    assert admin.is_admin and not auth.needs_setup()
    with pytest.raises(auth.AuthError):
        auth.register_first_admin("bob", "a-long-password", auth.issue_setup_code())


def test_invites_are_one_time_and_expire() -> None:
    admin = auth.register_first_admin("ada", "a-long-password", auth.issue_setup_code())
    token = auth.create_invite(admin.id, "user")
    assert auth.invite_info(token)["role"] == "user"
    bob = auth.redeem_invite(token, "bob", "another-long-pw")
    assert bob.role == "user"
    with pytest.raises(auth.AuthError, match="not valid"):
        auth.redeem_invite(token, "carl", "another-long-pw")
    old = auth.create_invite(admin.id, "user", hours=0.0001)
    time.sleep(0.5)
    assert auth.invite_info(old) is None


def test_login_is_throttled_after_repeated_failures() -> None:
    auth.register_first_admin("ada", "a-long-password", auth.issue_setup_code())
    for _ in range(auth.MAX_FAILURES):
        assert auth.authenticate("ada", "nope", "1.2.3.4") is None
    assert auth.login_locked("ada", "1.2.3.4")
    assert auth.authenticate("ada", "a-long-password", "1.2.3.4") is None  # locked even when right


def test_sessions_resolve_and_revoke_and_the_last_admin_stays() -> None:
    admin = auth.register_first_admin("ada", "a-long-password", auth.issue_setup_code())
    token = auth.create_session(admin.id)
    assert auth.session_user(token).id == admin.id
    auth.revoke_session(token)
    assert auth.session_user(token) is None
    with pytest.raises(auth.AuthError, match="at least one admin"):
        auth.set_role(admin.id, "user")
    with pytest.raises(auth.AuthError, match="at least one admin"):
        auth.disable_user(admin.id)


# -- the API in remote mode ----------------------------------------------------------------


@pytest.fixture
def remote(isolated):
    app = create_app(chat_service=ChatService(use_memory=False), remote=True)
    with TestClient(app, base_url=BASE) as client:
        yield client


def _setup_admin(client) -> dict:
    res = client.post("/api/auth/register", json={"username": "ada", "password": "a-long-password", "setup_code": auth.issue_setup_code()})
    assert res.status_code == 200, res.text
    return res.json()["user"]


def _client_for(app, username: str, password: str) -> TestClient:
    other = TestClient(app, base_url=BASE)
    assert other.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return other


def test_remote_mode_needs_a_login_for_everything_but_health_and_auth(remote) -> None:
    assert remote.get("/api/health").status_code == 200
    assert remote.get("/api/projects").status_code == 401
    assert remote.get("/api/bootstrap").status_code == 401
    status = remote.get("/api/auth/status").json()
    assert status == {"mode": "remote", "needs_setup": True, "user": None}

    admin = _setup_admin(remote)
    assert admin["role"] == "admin"
    assert remote.cookies.get(access.COOKIE)
    assert remote.get("/api/projects").status_code == 200
    assert remote.get("/api/auth/status").json()["user"]["username"] == "ada"
    # Registration is closed now.
    again = remote.post("/api/auth/register", json={"username": "eve", "password": "a-long-password", "setup_code": auth.issue_setup_code()})
    assert again.status_code == 403


def test_the_session_cookie_is_httponly_and_secure_behind_https(remote) -> None:
    res = remote.post("/api/auth/register", json={"username": "ada", "password": "a-long-password", "setup_code": auth.issue_setup_code()})
    cookie = res.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie


def test_wrong_password_and_logout(remote) -> None:
    _setup_admin(remote)
    remote.post("/api/auth/logout")
    assert remote.get("/api/projects").status_code == 401
    assert remote.post("/api/auth/login", json={"username": "ada", "password": "wrong-password"}).status_code == 401
    assert remote.post("/api/auth/login", json={"username": "ada", "password": "a-long-password"}).status_code == 200


def test_regular_users_cannot_touch_global_settings(remote, monkeypatch) -> None:
    from backend.services import sandbox

    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"ready": False, "detail": "not here"})
    admin = _setup_admin(remote)
    token = auth.create_invite(admin["id"], "user")  # the API refuses user invites until the sandbox
    assert remote.post("/api/users/invites", json={"role": "user"}).status_code == 409
    assert remote.post("/api/users/invites", json={"role": "admin"}).status_code == 201
    bob = TestClient(remote.app, base_url=BASE)
    assert bob.post(f"/api/auth/invite/{token}", json={"username": "bob", "password": "bobs-long-password"}).status_code == 200
    for method, path in (
        ("get", "/api/keys"),
        ("put", "/api/keys/openrouter"),
        ("put", "/api/models/roles"),
        ("get", "/api/users"),
        ("post", "/api/users/invites"),
        ("get", "/api/mcp/servers"),
        ("put", "/api/jev/settings"),
        ("post", "/api/shutdown"),
        ("post", "/api/tools"),
        ("post", "/api/skills"),
    ):
        assert bob.request(method.upper(), path, json={}).status_code == 403, (method, path)
    assert bob.get("/api/models").status_code == 200  # reading which models are chosen is fine
    assert remote.get("/api/users").json()["users"][1]["username"] == "bob"


def test_every_settings_route_is_admin_only() -> None:
    """A new route under a settings prefix cannot forget its guard: the prefixes are the rule."""
    app = create_app(chat_service=ChatService(use_memory=False))
    for route in app.routes:
        path = getattr(route, "path", "")
        for prefix in ("/api/keys", "/api/users", "/api/mcp", "/api/jev", "/api/openrouter"):
            if path.startswith(prefix):
                for method in getattr(route, "methods", {"GET"}):
                    assert access.needs_admin(method, path.replace("{", "").replace("}", "")), (method, path)


def test_each_user_sees_only_their_own_projects_in_their_own_workspace(remote) -> None:
    admin = _setup_admin(remote)
    token = auth.create_invite(admin["id"], "user")
    bob = TestClient(remote.app, base_url=BASE)
    bob.post(f"/api/auth/invite/{token}", json={"username": "bob", "password": "bobs-long-password"})

    mine = remote.post("/api/projects", json={"name": "Ada site"}).json()
    theirs = bob.post("/api/projects", json={"name": "Bob shop"}).json()
    bob_user = next(u for u in auth.list_users() if u.username == "bob")
    assert theirs["folders"][0].startswith(str(access.USERS_DIR / bob_user.id / "workspace"))

    assert [p["name"] for p in bob.get("/api/projects").json()] == ["Bob shop"]
    assert [p["name"] for p in remote.get("/api/projects").json()] == ["Ada site"]
    assert bob.get(f"/api/projects/{mine['id']}").status_code == 404
    assert bob.get(f"/api/projects/{mine['id']}/tasks").status_code == 404
    assert bob.post(f"/api/projects/{mine['id']}/threads/crew/messages", json={"text": "hi"}).status_code == 404
    assert bob.get(f"/api/projects/{mine['id']}/threads").status_code == 404
    assert bob.get(f"/api/projects/{mine['id']}/checkpoints").status_code == 404
    with pytest.raises(Exception):
        with bob.websocket_connect(f"/ws/projects/{mine['id']}/terminal", headers={"host": HOST}):
            pass
    assert [p["name"] for p in bob.get("/api/bootstrap").json()["projects"]] == ["Bob shop"]

    # Folders: only inside the user's own workspace.
    outside = bob.post("/api/projects", json={"name": "Escape", "folders": [str(access.USERS_DIR / admin["id"])]})
    assert outside.status_code == 422
    listing = bob.get("/api/workspaces/browse", params={"path": "/"}).json()
    assert listing["path"].startswith(str(access.USERS_DIR / bob_user.id)) and listing["parent"] is None


def test_projects_made_before_remote_access_go_to_the_first_admin(remote) -> None:
    from backend.platform.models import Project
    from backend.platform.registry import load_registry, save_registry

    registry = load_registry(refresh=True)
    registry.projects["old"] = Project(id="old", name="Desktop project")
    save_registry(registry)
    _setup_admin(remote)
    assert [p["id"] for p in remote.get("/api/projects").json()] == ["old"]


def test_events_of_another_users_project_never_reach_a_socket(isolated) -> None:
    import asyncio

    from backend.api.events import Client
    from backend.platform import bus
    from backend.platform.models import Project
    from backend.platform.registry import load_registry, save_registry

    registry = load_registry(refresh=True)
    registry.projects["pa"] = Project(id="pa", name="A", owner_id="u_a")
    registry.projects["pb"] = Project(id="pb", name="B", owner_id="u_b")
    save_registry(registry)
    loop = asyncio.new_event_loop()
    client = Client(loop, project_id=None, owner_id="u_a")
    assert client.wants(bus.Event(seq=1, type="chat.message", data={}, project_id="pa", ts=0))
    assert not client.wants(bus.Event(seq=2, type="chat.message", data={}, project_id="pb", ts=0))
    assert not client.wants(bus.Event(seq=3, type="bot.activity", data={"project_id": "pb"}, project_id="", ts=0))
    client.project_id = "pb"  # subscribing to it does not help
    assert not client.wants(bus.Event(seq=4, type="chat.message", data={}, project_id="pb", ts=0))
    loop.close()


def test_a_regular_users_turn_cannot_reach_the_install_tree(tmp_path) -> None:
    from backend import tools
    from backend.config import BASE_DIR
    from backend.services.workspace import bind_confined, bind_folders, reset_confined, reset_folders

    folders, confined = bind_folders([tmp_path]), bind_confined(True)
    try:
        assert BASE_DIR.resolve() not in tools._allowed_roots()
        with pytest.raises(ValueError):
            tools.resolve_user_path(str(BASE_DIR / "backend" / "config.py"))
        with pytest.raises(ValueError, match="outside the workspace"):
            tools.read_file(str(BASE_DIR / ".env"))
    finally:
        reset_confined(confined)
        reset_folders(folders)
    assert BASE_DIR.resolve() in tools._allowed_roots()  # local mode / admins: unchanged


def test_local_mode_is_unchanged(isolated) -> None:
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/projects").status_code == 200
        assert client.get("/api/auth/status").json()["mode"] == "local"
        assert client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 409
