"""Auxiliary / sub-agent routing: optionally send lightweight, low-stakes background calls --
context-summary compaction today, the same idea applies to markdown formatting or simple status
parsing if a caller wants it -- to a free OpenRouter model instead of the user's chosen brain for
that role. Off by default (``openrouter.free_tier_enabled``, Settings -> Models). Only ever touches
an OpenRouter call: the vendors' own APIs have no free tier to redirect to.
"""

from __future__ import annotations


def route(host: str, model: str) -> str:
    """``model`` unchanged, unless free-tier routing is on and ``host`` is OpenRouter, in which
    case the configured free-tier slug is used instead."""
    if str(host or "").strip().lower() != "openrouter":
        return model
    from backend.services.settings_store import openrouter_settings

    cfg = openrouter_settings()
    if not cfg.get("free_tier_enabled"):
        return model
    return str(cfg.get("free_tier_model") or "").strip() or model
