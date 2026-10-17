"""In-memory counters for the Jev gatekeeper (``jev_client.py``, ``middleware/jev_gatekeeper.py``):
how often it ran, how often it actually stepped in, an estimate of the tokens that saved, and how
fast it answered. Nothing here talks to the network or to Jev itself -- it only counts decisions the
gatekeeper already made. Read by ``services/telemetry.py`` for the Telemetry tab.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.jev_client import Decision

_MAX_LATENCIES = 500


@dataclass
class _State:
    precheck_calls: int = 0
    bypasses: int = 0
    guard_calls: int = 0
    blocked_tool_calls: int = 0
    #: The clarify / skill-proposal gates: questions asked, and model calls they let the turn skip.
    gate_calls: int = 0
    skipped_calls: int = 0
    #: A rough estimate, not a measured saving: the size (tokens) of the conversation that was never
    #: sent to the coding model (a bypass) or that would have gone into another repair round (a block).
    tokens_saved_estimate: int = 0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=_MAX_LATENCIES))


_lock = threading.Lock()
_state = _State()


def record_precheck(decision: "Decision", *, bypassed: bool, tokens: int = 0) -> None:
    with _lock:
        _state.precheck_calls += 1
        _state.latencies_ms.append(decision.latency_ms)
        if bypassed:
            _state.bypasses += 1
            _state.tokens_saved_estimate += max(0, int(tokens))


def record_guard(decision: "Decision", *, blocked: bool, tokens: int = 0) -> None:
    with _lock:
        _state.guard_calls += 1
        _state.latencies_ms.append(decision.latency_ms)
        if blocked:
            _state.blocked_tool_calls += 1
            _state.tokens_saved_estimate += max(0, int(tokens))


def record_gate(decision: "Decision", *, skipped: bool, tokens: int = 0) -> None:
    with _lock:
        _state.gate_calls += 1
        _state.latencies_ms.append(decision.latency_ms)
        if skipped:
            _state.skipped_calls += 1
            _state.tokens_saved_estimate += max(0, int(tokens))


def snapshot() -> dict[str, int | float]:
    with _lock:
        latencies = list(_state.latencies_ms)
        avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
        return {
            "precheck_calls": _state.precheck_calls,
            "bypasses": _state.bypasses,
            "guard_calls": _state.guard_calls,
            "blocked_tool_calls": _state.blocked_tool_calls,
            "gate_calls": _state.gate_calls,
            "skipped_calls": _state.skipped_calls,
            "interceptions": _state.bypasses + _state.blocked_tool_calls + _state.skipped_calls,
            "tokens_saved_estimate": _state.tokens_saved_estimate,
            "avg_decision_latency_ms": avg_latency,
        }


def clear() -> None:
    global _state
    with _lock:
        _state = _State()
