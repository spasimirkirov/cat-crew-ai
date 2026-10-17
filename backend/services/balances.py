"""What is left to spend: OpenRouter's unified credit, the one cloud host's balance.

OpenRouter has a small read-only endpoint for this (``GET /api/v1/credits``). The answer is cached (a
poll every few seconds must not become a request every few seconds), refreshed sooner right after the
host was spent on (:func:`mark_stale`, called by the usage meter), and never carries the key: a
failure is a short fixed sentence, not the exception text.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from backend.services import api_access

TTL_S = 60.0  # how long an answer is reused
STALE_MIN_S = 10.0  # even when marked stale, not more often than this
ERROR_TTL_S = 30.0  # a failed look is retried after this
TIMEOUT_S = 4.0

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}
_fetched_at: dict[str, float] = {}
_stale: set[str] = set()

_SYMBOLS = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£"}


def money(amount: float, currency: str) -> str:
    symbol = _SYMBOLS.get(currency.upper())
    body = f"{amount:,.2f}"
    return f"{symbol}{body}" if symbol else f"{body} {currency.upper()}".strip()


def _base(host: str, **extra: Any) -> dict[str, Any]:
    return {"host": host, "configured": True, "status": "ok", "balance": None, "currency": "", "display": "", "message": "", **extra}


def _failure(host: str, status_code: int | None) -> dict[str, Any]:
    if status_code in (401, 403):
        message = "The key was rejected."
    elif status_code:
        message = f"The host answered HTTP {status_code}."
    else:
        message = "Could not reach the host."
    return _base(host, status="error", message=message)


def parse_openrouter(payload: Any) -> dict[str, Any]:
    """``{"data": {"total_credits": 10.0, "total_usage": 2.5}}``: what is left is the difference."""
    out = _base("openrouter")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        out.update(status="error", message="The host sent no credit.")
        return out
    total, used = _num(data.get("total_credits")), _num(data.get("total_usage"))
    if total is None:
        out.update(status="error", message="The host sent an unreadable credit.")
        return out
    left = round(total - (used or 0.0), 4)
    out.update(
        status="empty" if left <= 0 else "ok",
        balance=left,
        currency="USD",
        display=f"{money(left, 'USD')} remaining",
        total=total,
        used=used,
    )
    return out


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get(url: str, key: str) -> Any:
    response = requests.get(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=TIMEOUT_S)
    if response.status_code >= 400:
        raise _Http(response.status_code)
    return response.json()


class _Http(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


def _key(host: str) -> str:
    return api_access.resolved_key(host) if api_access.key_enabled(host) else ""


_URL_SUFFIX = {"openrouter": "/credits"}


def _url(host: str) -> str:
    spec = api_access.spec_for(host)
    root = (spec.base_url if spec else "https://openrouter.ai/api/v1").rstrip("/")
    return root + _URL_SUFFIX.get(host, "/credits")


_PARSERS = {"openrouter": parse_openrouter}


def get_balance(host: str, *, force: bool = False) -> dict[str, Any]:
    """The balance of ``host`` (``openrouter``, the one cloud host): ``{"configured": False}`` when there is no key."""
    host = str(host or "").strip().lower()
    if host not in _PARSERS:
        return {"host": host, "configured": False, "status": "unsupported"}
    key = _key(host)
    if not key:
        return {"host": host, "configured": False, "status": "no_key"}
    now = time.monotonic()
    with _lock:
        hit = _cache.get(host)
        age = now - _fetched_at.get(host, 0.0)
        fresh = hit is not None and (
            age < (ERROR_TTL_S if hit.get("status") == "error" else TTL_S)
            and not (host in _stale and age >= STALE_MIN_S)
        )
    if hit is not None and fresh and not force:
        return dict(hit)
    try:
        result = _PARSERS[host](_get(_url(host), key))
    except _Http as exc:
        result = _failure(host, exc.status)
    except Exception:  # noqa: BLE001 - a dead network must never fail the telemetry call (or leak a URL)
        result = _failure(host, None)
    with _lock:
        _cache[host] = result
        _fetched_at[host] = time.monotonic()
        _stale.discard(host)
    return dict(result)


def mark_stale(host: str) -> None:
    """The host was just spent on: the next look may re-read the balance (within ``STALE_MIN_S``)."""
    with _lock:
        _stale.add(str(host or "").strip().lower())


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _fetched_at.clear()
        _stale.clear()
