"""OpenRouter savings/performance settings: provider routing (sort/fallback), the optional
context-compression plugin, and auxiliary free-tier routing. Model pricing itself is not a
separate endpoint -- it rides on the existing ``GET /api/models/hosts/openrouter`` live discovery
(each model row carries ``price_prompt_per_1m`` / ``price_completion_per_1m`` / ``is_free`` /
``supports_prompt_cache`` when OpenRouter reports them).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services import settings_store

router = APIRouter(prefix="/api/openrouter", tags=["openrouter"])

_SORTS = ("", "price", "throughput", "latency")


class OpenRouterSettingsBody(BaseModel):
    provider_sort: str | None = None
    allow_fallbacks: bool | None = None
    compression_enabled: bool | None = None
    free_tier_enabled: bool | None = None
    free_tier_model: str | None = None


@router.get("/settings")
def get_settings() -> dict:
    return settings_store.openrouter_settings()


@router.put("/settings")
def put_settings(body: OpenRouterSettingsBody) -> dict:
    update = body.model_dump(exclude_none=True)
    if "provider_sort" in update and update["provider_sort"].strip().lower() not in _SORTS:
        allowed = ", ".join(repr(s) for s in _SORTS if s)
        raise HTTPException(422, f"provider_sort must be one of {allowed}, or empty for the OpenRouter default.")
    if "free_tier_model" in update and not update["free_tier_model"].strip():
        raise HTTPException(422, "The free-tier model slug cannot be empty.")
    return settings_store.save_openrouter_settings(update)
