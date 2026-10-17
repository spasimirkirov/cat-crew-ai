"""API keys, write-only.

``PUT /api/keys/{id}`` stores a key, ``DELETE`` forgets it, ``GET`` lists what is set. No response ever
carries a key (only ``configured`` and ``source``), no error message repeats one, and the body is parsed
by hand so a rejected payload is never echoed back in a validation error.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.services import api_access, model_discovery, model_health

router = APIRouter(prefix="/api", tags=["keys"])

#: Every key CatCrewAI keeps lives in one place, the api_access catalog: the model hosts, Brave
#: search and TypeSafe (Jev). This route is the only way in or out, and nothing ever comes out.
KEYABLE = tuple(spec.id for spec in api_access.CATALOG)


def _view(kind: str) -> dict[str, Any]:
    """What the UI may know about one key: never the key itself."""
    spec = api_access.spec_for(kind)
    assert spec is not None
    return {
        "id": spec.id,
        "title": spec.title,
        "subtitle": spec.subtitle,
        "group": "host" if spec.kind == "llm" else "service",
        "configured": api_access.has_api_key(kind),
        "source": api_access.key_source(kind),
        "signup": spec.signup,
    }


def _known(kind: str) -> str:
    key = str(kind or "").strip().lower()
    if key not in KEYABLE:
        raise HTTPException(404, f"Unknown key {kind!r}.")
    return key


def _changed() -> None:
    """A key changed: forget the cached host probes so the next call sees the new state."""
    model_discovery.clear_cache()
    model_health.invalidate()


def _store(kind: str, raw: Any) -> None:
    api_access.set_api_key(kind, api_access.clean_key(raw))


def _forget(kind: str) -> None:
    api_access.clear_api_key(kind)


@router.get("/keys")
def list_keys() -> list[dict[str, Any]]:
    return [_view(kind) for kind in KEYABLE]


@router.put("/keys/{kind}")
async def put_key(kind: str, request: Request) -> dict[str, Any]:
    kind = _known(kind)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "Send a JSON body like {\"key\": \"...\"}.") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "Send a JSON body like {\"key\": \"...\"}.")
    try:
        await asyncio.to_thread(_store, kind, body.get("key"))
    except api_access.InvalidKey as exc:
        raise HTTPException(422, str(exc)) from None
    _changed()
    return _view(kind)


@router.delete("/keys/{kind}")
async def delete_key(kind: str) -> dict[str, Any]:
    kind = _known(kind)
    await asyncio.to_thread(_forget, kind)
    _changed()
    return _view(kind)
