"""Who may do what: the one place the API's access rules live.

Two modes, decided at start-up (``configure``):

* **local** (loopback bind, the default): one person at their own computer. No login; every
  request runs as ``auth.LOCAL_ADMIN``. Exactly the app as it always was.
* **remote** (bound to a network address): every request needs a session (``auth.py``), each
  user sees only their own projects and events, and the global settings are the admins'.

``AuthMiddleware`` signs each request in and refuses what the user may not do; route code asks
``current_user()``, ``owns(project)`` and ``user_root()``. Admin-only routes are listed in
``ADMIN_ONLY`` here rather than at each route, so a new settings route cannot forget its guard
(``tests/test_auth.py`` checks the list against the app's routes).
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from backend.config import BASE_DIR
from backend.services import auth

COOKIE = "catcrew_session"
USERS_DIR = BASE_DIR / "data" / "users"

_remote = False
_user: ContextVar[auth.User | None] = ContextVar("catcrew_request_user", default=None)

# (method or "*", path regex): only admins. GETs of settings stay open where the UI needs to read
# them (which models are chosen); the keys, users and integrations are not even readable.
ADMIN_ONLY: tuple[tuple[str, str], ...] = (
    ("*", r"/api/keys(/.*)?"),
    ("*", r"/api/users(/.*)?"),
    ("*", r"/api/mcp(/.*)?"),
    ("*", r"/api/jev(/.*)?"),
    ("*", r"/api/openrouter(/.*)?"),
    ("*", r"/api/shutdown"),
    ("WRITE", r"/api/models(/.*)?"),
    ("WRITE", r"/api/hosts(/.*)?"),
    ("WRITE", r"/api/skills(/.*)?"),
    ("WRITE", r"/api/tools(/.*)?"),
    ("*", r"/api/tools/available"),
)
_ADMIN = [(m, re.compile(p)) for m, p in ADMIN_ONLY]
# Reachable without a session in remote mode.
PUBLIC = re.compile(r"/api/(health|auth/.*)")
# Every route under a project: checked here once, so no project route can forget the owner check.
PROJECT_PATH = re.compile(r"/(?:api|ws)/projects/([^/]+)(?:/.*)?")


def configure(*, remote: bool) -> None:
    global _remote
    _remote = bool(remote)


def remote_mode() -> bool:
    return _remote


def needs_admin(method: str, path: str) -> bool:
    write = method.upper() not in ("GET", "HEAD", "OPTIONS")
    for rule, pattern in _ADMIN:
        if pattern.fullmatch(path) and (rule == "*" or (rule == "WRITE" and write)):
            return True
    return False


def set_user(user: auth.User | None):
    return _user.set(user)


def reset_user(token) -> None:
    try:
        _user.reset(token)
    except (ValueError, LookupError):
        _user.set(None)


def current_user() -> auth.User:
    """The signed-in user; the local admin in local mode (and outside a request)."""
    user = _user.get()
    if user is not None:
        return user
    return auth.LOCAL_ADMIN


def owns(project: Any) -> bool:
    """May the current user see and change ``project``? Always, in local mode."""
    if not _remote:
        return True
    owner = str(getattr(project, "owner_id", "") or "")
    return bool(owner) and owner == current_user().id


def owner_of_project(project_id: str) -> str:
    from backend.platform.registry import get_project

    project = get_project(project_id)
    return str(getattr(project, "owner_id", "") or "") if project is not None else ""


def user_root(user: auth.User | None = None) -> Path | None:
    """The folder a user's projects live in (remote mode), created on first use; ``None`` locally."""
    if not _remote:
        return None
    who = user or current_user()
    root = USERS_DIR / who.id / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def inside(path: str | Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def session_token(headers: dict[str, str]) -> str:
    raw = headers.get("cookie", "")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value.strip()
    return ""


class AuthMiddleware:
    """Pure ASGI: signs the request in (remote mode) and enforces login and the admin routes."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        user: auth.User | None = auth.LOCAL_ADMIN
        if _remote:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            user = auth.session_user(session_token(headers)) if session_token(headers) else None
            guarded = path.startswith("/api/") or path.startswith("/ws/")
            if guarded and user is None and not PUBLIC.fullmatch(path):
                await _refuse(scope, send, 401, "Sign in first.")
                return
            if user is not None and not user.is_admin and needs_admin(scope.get("method", "GET"), path):
                await _refuse(scope, send, 403, "Only an admin can change this.")
                return
            match = PROJECT_PATH.fullmatch(path)
            if user is not None and match and owner_of_project(match.group(1)) != user.id:
                # Another user's project looks exactly like one that does not exist.
                await _refuse(scope, send, 404, "Unknown project.")
                return
        scope.setdefault("state", {})["user"] = user
        token = set_user(user)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_user(token)


async def _refuse(scope: dict[str, Any], send: Any, status: int, detail: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403})
        return
    body = ('{"detail": "%s"}' % detail).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
