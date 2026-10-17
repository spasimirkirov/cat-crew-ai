"""The Telemetry tab's data: one snapshot of everything that says "how is the model doing".

``live(project_id)`` gathers, without failing when a part is unavailable:

* ``context`` -- how full the running conversation is (``services/usage_meter``), the number the
  gauge shows; the same value is pushed as ``context.usage`` events while a turn runs;
* ``active`` -- the host and model the project's Coding role runs on and whether that host answers;
* ``openrouter`` -- what is left to spend on the one cloud host (``services/balances``);
* ``openrouter_cache`` -- prompt-cache token savings on OpenRouter (``services/usage_meter``);
* ``jev`` -- the Jev ("System One") gatekeeper's settings and interception counters
  (``services/jev_metrics``), when it has ever run;
* ``gpu`` -- VRAM, load and temperature of the local GPU, plus the local model's generation speed.

Nothing here returns an API key. A section for a host with no key is ``{"configured": false}``, which
the UI hides.
"""

from __future__ import annotations

import time
from typing import Any

from backend.services import api_access, balances, hardware, usage_meter


def _iso(ts: float | None) -> str | None:
    from backend.api import views

    return views.iso_from_ts(ts) if ts else None


def _context(project_id: str) -> dict[str, Any] | None:
    row = usage_meter.get_meter().context(project_id)
    if row is None:
        return None
    return {**row, "updated_at": _iso(row.get("updated_at"))}


def _active(project_id: str) -> dict[str, Any]:
    from backend.services import model_health
    from backend.services.lm_runtime import host_label
    from backend.services.model_resolver import get_effective_model

    eff = get_effective_model(project_id, "coding")
    out: dict[str, Any] = {
        "role": "coding",
        "host": eff.host,
        "host_label": host_label(eff.host),
        "model": eff.model,
        "source": eff.source,
        "local": not api_access.is_cloud_host(eff.host),
        "status": "unknown",
        "message": "",
    }
    try:
        report = model_health.current()
    except Exception:  # noqa: BLE001 - a probe failure is "unknown", not an error page
        return out
    issue = next((i for i in report.issues if i.host == eff.host and i.model == eff.model), None)
    if issue is not None:
        out.update(status=issue.reason, message=issue.message)
    elif eff.host in report.hosts:
        out["status"] = "ready" if report.hosts[eff.host].online else "offline"
    elif api_access.is_cloud_host(eff.host) and api_access.cloud_host_ready(eff.host):
        out["status"] = "ready"
    return out


def _jev_usd(tokens: int) -> float | None:
    """The saved token count, priced at the Coding role's current OpenRouter model -- ``None``
    when that role isn't on OpenRouter or the model isn't in the last-known catalog snapshot.
    Same honest-unknown-over-wrong-number rule as ``usage_meter._estimate_cache_usd``."""
    if not tokens:
        return 0.0
    try:
        from backend.platform.runtime import current_project_id
        from backend.services import model_discovery
        from backend.services.model_resolver import get_effective_model

        eff = get_effective_model(current_project_id(), "coding")
        if eff.host != "openrouter":
            return None
        snap = model_discovery.peek_snapshot("openrouter")
        if not snap or not snap.models:
            return None
        price = next((m.price_prompt_per_1m for m in snap.models if m.name == eff.model), None)
        return round((tokens / 1_000_000) * price, 4) if price is not None else None
    except Exception:  # noqa: BLE001
        return None


def _jev() -> dict[str, Any]:
    from backend.services import jev_metrics, settings_store

    # jev_settings() carries the raw api_key (write-only past settings_store) -- never let it
    # reach an HTTP response through this telemetry snapshot.
    cfg = {k: v for k, v in settings_store.jev_settings().items() if k != "api_key"}
    snapshot = jev_metrics.snapshot()
    return {**cfg, **snapshot, "estimated_usd_saved": _jev_usd(int(snapshot.get("tokens_saved_estimate", 0)))}


def _gpu() -> dict[str, Any]:
    stats = hardware.gpu_stats()
    speed = None  # no local host: generation speed is not measured here
    return {
        **stats,
        "gen_speed_tps": round(speed[0], 1) if speed else None,
        "gen_speed_at": _iso(speed[1]) if speed else None,
    }


def live(project_id: str = "") -> dict[str, Any]:
    """Everything for the Telemetry tab. Each part is independent: one failing leaves the rest intact."""
    active = _active(project_id)
    out: dict[str, Any] = {
        "project_id": project_id or None,
        "updated_at": _iso(time.time()),
        "context": _context(project_id),
        "active": active,
    }
    out.update(_window(active))
    for name, fn in (
        ("openrouter", lambda: balances.get_balance("openrouter")),
        ("openrouter_cache", lambda: usage_meter.get_meter().cache_savings("openrouter")),
        ("jev", _jev),
        ("gpu", _gpu),
    ):
        try:
            out[name] = fn()
        except Exception:  # noqa: BLE001
            out[name] = {"configured": False, "status": "error"}
    return out


def _window(active: dict[str, Any]) -> dict[str, Any]:
    """The active coding model's context window, for the gauge before (and between) runs."""
    from backend.services.provider_limits import get_model_context_limit

    try:
        found = get_model_context_limit(str(active.get("host") or ""), str(active.get("model") or ""))
    except Exception:  # noqa: BLE001 - the gauge must still draw
        found = {"max_tokens": 16_384, "compression_threshold": 14_000, "source": "default"}
    return {
        "context_limit": int(found["max_tokens"]),
        "context_threshold": int(found["compression_threshold"]),
        "context_limit_source": str(found.get("source") or "default"),
    }
