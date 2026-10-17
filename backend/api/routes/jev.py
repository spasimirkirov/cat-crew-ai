"""Jev ("System One") gatekeeper: settings and telemetry.

Jev runs on OpenRouter's Decisions API (the OpenRouter key) or on TypeSafe's official API (the
TypeSafe key). This route sets where it runs and how it behaves; it never takes or returns a key --
every key lives in the one key catalog, set through ``api/routes/keys.py`` (Models -> Hosts).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.services import api_access, jev_metrics, settings_store

router = APIRouter(prefix="/api/jev", tags=["jev"])


class JevSettingsBody(BaseModel):
    enabled: bool | None = None
    #: Where Jev runs. Its key is the OpenRouter host's or the TypeSafe entry's, set under Models -> Hosts.
    backend: Literal["openrouter", "typesafe"] | None = None
    guard_tool_calls: bool | None = None
    skip_tools_precheck: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


def _public(cfg: dict) -> dict:
    """The settings view the UI may see. Jev holds no key of its own: ``key_source`` says whether the
    chosen backend's key is set in the key catalog -- ``openrouter`` (Jev uses the OpenRouter key),
    ``saved`` / ``environment`` (the TypeSafe key), or ``none``."""
    out = dict(cfg)
    if cfg.get("backend") == "typesafe":
        source = api_access.key_source("typesafe")
        out["api_key_configured"] = source != "none"
        out["key_source"] = source
    else:
        ready = api_access.has_api_key("openrouter")
        out["api_key_configured"] = ready
        out["key_source"] = "openrouter" if ready else "none"
    out["model"] = "jev-latest" if cfg.get("backend") == "typesafe" else "typesafe/jev-latest"
    return out


@router.get("/settings")
def get_settings() -> dict:
    return _public(settings_store.jev_settings())


@router.put("/settings")
def put_settings(body: JevSettingsBody) -> dict:
    update = body.model_dump(exclude_none=True)
    return _public(settings_store.save_jev_settings(update))


@router.get("/metrics")
def get_metrics() -> dict:
    """Pre-execution interceptions, an estimate of tokens saved, and average decision latency."""
    return jev_metrics.snapshot()
