"""Live model usage: what each call sent, how fast a host answers, how full the context is.

Nothing here talks to the network. Providers report every request they make
(``providers/openai_compat.py`` -> :func:`record_call`) and the agent loop reports how big the
conversation is before each model call (:func:`report_context`). The Telemetry tab of the
project inspector reads the result through ``GET /api/telemetry/live`` and follows the context
gauge live over the project WebSocket (``context.usage`` events).

* **Requests and tokens per window.** Google's free tier is limited per minute (RPM / TPM) and per
  day (RPD) and exposes no quota headers, so the only honest meter is our own count of the calls
  this process made. It is a lower bound: another program sharing the same key is not seen.
* **Tokens** are the provider's own ``usage`` numbers when it sends them, else an estimate of
  ~4 characters a token (the same estimate the context budget uses); ``estimated`` says which.
* **Generation speed** is output tokens over the time from the first streamed token to the last.
* **Context** is the whole conversation the next call will carry (system prompt included) against the
  model's real window (``services/provider_limits``: 64k, 128k, 200k, 1M).
  It is summarized at 85 % of the window, which is where the gauge turns red.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

MINUTE = 60.0
DAY = 86_400.0
_MAX_CALLS = 20_000
CHARS_PER_TOKEN = 4

# Gauge colours (the UI colours by ``level``): green below 60 %, yellow to 85 %, red above (compression is near).
WARN_PERCENT = 60
DANGER_PERCENT = 85

#: Hosts whose calls count against a quota worth showing. Everything is recorded; this is only for docs/tests.
CLOUD_KINDS = ("openrouter",)

# OpenRouter prompt caching: a documented ASSUMPTION, not a vendor-quoted price. The Anthropic-family
# caching scheme OpenRouter's cache_control breakpoints use typically discounts a cached prompt read
# by about 90%; this constant only turns the token count the host itself reports as "cached" into a
# rough saved-spend estimate for the telemetry tab -- it is never shown as an exact dollar figure.
CACHED_TOKEN_DISCOUNT = 0.9


def level_for(percent: float) -> str:
    """``ok`` / ``warn`` / ``danger`` for a fill percentage."""
    if percent > DANGER_PERCENT:
        return "danger"
    if percent >= WARN_PERCENT:
        return "warn"
    return "ok"


def estimate_text_tokens(text: str) -> int:
    return -(-len(text or "") // CHARS_PER_TOKEN)


def _estimate_cache_usd(host: str, calls: "list[Call]") -> float | None:
    """``None`` unless every priced call's model is in the last-known OpenRouter catalog snapshot
    (``services/model_discovery``, never a network call from here) -- an honest "unknown" beats a
    silently wrong dollar figure. Never raises: a catalog miss just leaves the estimate at ``None``."""
    if host != "openrouter" or not calls:
        return None
    try:
        from backend.services import model_discovery

        snap = model_discovery.peek_snapshot("openrouter")
    except Exception:  # noqa: BLE001
        return None
    if not snap or not snap.models:
        return None
    prices = {m.name: m.price_prompt_per_1m for m in snap.models}
    total = 0.0
    priced_any = False
    for c in calls:
        if not c.cached_prompt:
            continue
        price = prices.get(c.model)
        if price is None:
            continue
        priced_any = True
        total += (c.cached_prompt / 1_000_000) * price * CACHED_TOKEN_DISCOUNT
    return round(total, 4) if priced_any else None


@dataclass(frozen=True)
class Call:
    ts: float
    host: str
    model: str
    prompt: int
    completion: int
    seconds: float
    estimated: bool
    ok: bool = True
    #: Of ``prompt``, the part the host itself reported as a prompt-cache hit (0 when it said nothing,
    #: or the host does not support caching). OpenRouter only.
    cached_prompt: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt + self.completion


class UsageMeter:
    """Thread-safe: providers run on worker threads, the API reads from the event loop's pool."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._calls: deque[Call] = deque(maxlen=_MAX_CALLS)
        self._context: dict[str, dict[str, Any]] = {}
        self._speed: dict[str, tuple[float, float]] = {}  # host -> (at, tokens per second)
        self._errors: dict[str, dict[str, Any]] = {}  # host -> last failed request

    # -- writers -----------------------------------------------------------------------

    def record_call(
        self,
        host: str,
        model: str,
        *,
        prompt: int = 0,
        completion: int = 0,
        seconds: float = 0.0,
        estimated: bool = False,
        ok: bool = True,
        cached_prompt: int = 0,
    ) -> Call:
        host = str(host or "").strip().lower() or "other"
        call = Call(
            self._clock(),
            host,
            str(model or ""),
            max(0, int(prompt)),
            max(0, int(completion)),
            max(0.0, float(seconds)),
            bool(estimated),
            bool(ok),
            max(0, min(int(cached_prompt), max(0, int(prompt)))),
        )
        with self._lock:
            self._calls.append(call)
            if ok and call.completion >= 8 and call.seconds >= 0.05:
                self._speed[host] = (call.ts, call.completion / call.seconds)
            if ok:
                self._errors.pop(host, None)
        return call

    def record_error(self, host: str, model: str, status: int, message: str = "") -> None:
        """A request the host refused (429 = the quota is spent). It still counts as a request."""
        host = str(host or "").strip().lower() or "other"
        call = Call(self._clock(), host, str(model or ""), 0, 0, 0.0, True, False)
        with self._lock:
            self._calls.append(call)
            self._errors[host] = {"at": call.ts, "status": int(status or 0), "message": str(message or "")[:160]}

    def set_context(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        row = dict(data, updated_at=self._clock())
        with self._lock:
            self._context[str(project_id or "")] = row
        return row

    def forget_project(self, project_id: str) -> None:
        with self._lock:
            self._context.pop(str(project_id or ""), None)

    def clear(self) -> None:
        with self._lock:
            self._calls.clear()
            self._context.clear()
            self._speed.clear()
            self._errors.clear()

    # -- readers -----------------------------------------------------------------------

    def context(self, project_id: str) -> dict[str, Any] | None:
        """The latest context reading of a project; runs that had no project id are the fallback."""
        with self._lock:
            row = self._context.get(str(project_id or "")) or self._context.get("")
            return dict(row) if row else None

    def window(self, host: str, seconds: float, *, model: str = "") -> tuple[int, int]:
        """(requests, tokens) this host handled in the last ``seconds`` (one model when named)."""
        host = str(host or "").strip().lower()
        since = self._clock() - seconds
        requests = tokens = 0
        with self._lock:
            for call in reversed(self._calls):
                if call.ts < since:
                    break
                if call.host != host or (model and call.model != model):
                    continue
                requests += 1
                tokens += call.tokens
        return requests, tokens

    def last_model(self, host: str) -> str:
        host = str(host or "").strip().lower()
        with self._lock:
            for call in reversed(self._calls):
                if call.host == host and call.model:
                    return call.model
        return ""

    def speed(self, host: str, *, max_age: float = 600.0) -> tuple[float, float] | None:
        """(tokens per second, when it was measured) of the host's latest generation, if recent."""
        host = str(host or "").strip().lower()
        with self._lock:
            hit = self._speed.get(host)
        if hit is None or self._clock() - hit[0] > max_age:
            return None
        return hit[1], hit[0]

    def last_error(self, host: str, *, max_age: float = 300.0) -> dict[str, Any] | None:
        host = str(host or "").strip().lower()
        with self._lock:
            row = self._errors.get(host)
        if row is None or self._clock() - row["at"] > max_age:
            return None
        return dict(row)

    def call_count(self) -> int:
        with self._lock:
            return len(self._calls)

    def cache_savings(self, host: str = "openrouter") -> dict[str, Any]:
        """Prompt-cache token savings, summed over the calls still in the window. Both
        ``estimated_pct_spend_saved`` and ``estimated_usd_saved`` rest on the same documented
        assumption (``CACHED_TOKEN_DISCOUNT``), never a vendor-quoted figure. The dollar figure is
        additionally ``None`` whenever a call's model has no known OpenRouter prompt price (an
        offline/never-probed catalog, or a model the live ``/v1/models`` list no longer carries)."""
        host = str(host or "").strip().lower()
        with self._lock:
            calls = [c for c in self._calls if c.host == host and c.ok]
        prompt_tokens = sum(c.prompt for c in calls)
        cached_tokens = sum(c.cached_prompt for c in calls)
        hit_pct = round(100 * cached_tokens / prompt_tokens) if prompt_tokens else 0
        return {
            "host": host,
            "calls": len(calls),
            "prompt_tokens": prompt_tokens,
            "cached_tokens": cached_tokens,
            "cache_hit_pct": hit_pct,
            "estimated_pct_spend_saved": round(hit_pct * CACHED_TOKEN_DISCOUNT),
            "estimated_usd_saved": _estimate_cache_usd(host, calls),
        }


_meter = UsageMeter()


def get_meter() -> UsageMeter:
    return _meter


def reset_meter() -> UsageMeter:
    """Fresh meter (tests)."""
    global _meter
    _meter = UsageMeter()
    return _meter


# -- what providers and the agent loop call ---------------------------------------------


def _host_of(provider: Any) -> str:
    return str(getattr(provider, "host_id", "") or "").strip().lower()


def _messages_tokens(messages: list[dict[str, Any]]) -> int:
    from backend.services.compaction import estimate_tokens

    return estimate_tokens(messages)


def record_call(
    provider: Any,
    messages: list[dict[str, Any]],
    *,
    content: str | None,
    tool_arg_chars: int = 0,
    usage: dict[str, Any] | None = None,
    seconds: float = 0.0,
) -> None:
    """One finished request of ``provider``. Never raises: metering must not break a turn."""
    try:
        prompt = completion = cached = 0
        estimated = True
        if isinstance(usage, dict):
            prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            estimated = not (prompt or completion)
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                cached = int(details.get("cached_tokens") or 0)
            if not cached:
                # Anthropic's own usage field name, when OpenRouter passes the provider's native usage through.
                cached = int(usage.get("cache_read_input_tokens") or usage.get("cached_tokens") or 0)
        if estimated:
            prompt = _messages_tokens(messages)
            completion = estimate_text_tokens(content or "") + -(-int(tool_arg_chars) // CHARS_PER_TOKEN)
        host = _host_of(provider)
        get_meter().record_call(
            host, str(getattr(provider, "model", "") or ""), prompt=prompt, completion=completion, seconds=seconds, estimated=estimated, cached_prompt=cached
        )
        if host == "openrouter":
            from backend.services import balances

            balances.mark_stale("openrouter")  # spending just happened: the next look re-reads the balance
    except Exception:  # noqa: BLE001
        pass


def record_error(provider: Any, status: int, message: str = "") -> None:
    try:
        get_meter().record_error(_host_of(provider), str(getattr(provider, "model", "") or ""), status, message)
    except Exception:  # noqa: BLE001
        pass


def report_context(
    provider: Any,
    messages: list[dict[str, Any]],
    limit: int,
    *,
    compressed: bool = False,
    project_id: str | None = None,
    threshold: int | None = None,
) -> dict[str, Any] | None:
    """Measure the conversation the next model call carries, remember it and announce it
    (``context.usage`` on the event bus, so an open project room updates its gauge live)."""
    try:
        from backend.platform import bus
        from backend.platform.runtime import current_project_id

        pid = current_project_id() if project_id is None else str(project_id or "")
        system = _messages_tokens(messages[:1]) if messages and messages[0].get("role") == "system" else 0
        tokens = max(0, _messages_tokens(messages))
        limit = max(1, int(limit))
        threshold = int(threshold) if threshold else int(limit * 0.85)
        percent = round(100.0 * tokens / limit)
        data = {
            "host": _host_of(provider),
            "model": str(getattr(provider, "model", "") or ""),
            "tokens": tokens,
            "system_tokens": system,
            "limit": limit,
            "threshold": threshold,
            "percent": percent,
            "level": level_for(percent),
            "compressed": bool(compressed),
        }
        row = get_meter().set_context(pid, data)
        bus.emit_context_usage(pid, data)
        return row
    except Exception:  # noqa: BLE001
        return None
