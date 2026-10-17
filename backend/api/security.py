"""Local-only guard for the API (loopback bind + Origin/Host allow-list).

The server is a desktop app's backend, so the threat is not a remote attacker
but a *web page in the user's browser* talking to ``127.0.0.1`` (CSRF, or DNS
rebinding to make a hostile hostname resolve to loopback). Two checks stop
both, for HTTP and WebSocket alike:

* ``Host`` must be a loopback name (``127.0.0.1``, ``localhost``, ``[::1]``);
  a rebound ``evil.example`` is rejected.
* ``Origin``, when present, must be on the allow-list (the Tauri webview and the
  Vite dev server). Requests with no ``Origin`` (curl, the Tauri Rust side,
  tests) are allowed: browsers always send it on cross-origin requests.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

DEFAULT_ORIGINS = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
)
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def allowed_origins() -> list[str]:
    extra = [o.strip().rstrip("/") for o in os.environ.get("CATCREW_ALLOWED_ORIGINS", "").split(",")]
    return list(dict.fromkeys([*DEFAULT_ORIGINS, *[o for o in extra if o]]))


def _host_only(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):  # [::1]:8765
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.split(":", 1)[0]


def host_allowed(host_header: str) -> bool:
    if not host_header:
        return False
    return _host_only(host_header) in _LOOPBACK_HOSTS


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    if origin is None:
        return True
    return origin.rstrip("/") in allowed


ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


class LocalOnlyMiddleware:
    """Pure-ASGI middleware (works for ``http`` and ``websocket`` scopes)."""

    def __init__(self, app: ASGIApp, *, extra_origins: list[str] | None = None) -> None:
        self.app = app
        self.allowed = list(dict.fromkeys([*allowed_origins(), *(extra_origins or [])]))

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = ""
        if not host_allowed(headers.get("host", "")):
            reason = "Host not allowed"
        elif not origin_allowed(headers.get("origin"), self.allowed):
            reason = "Origin not allowed"
        if not reason:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # Reject the handshake: never accept, so no frame is ever exchanged.
            await send({"type": "websocket.close", "code": 1008})
            return
        body = f'{{"detail": "{reason}"}}'.encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
