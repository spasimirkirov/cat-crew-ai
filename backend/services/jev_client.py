"""Adapter to TypeSafe AI's "Jev" (System One) decision model: a small, fast, non-generative model,
reached through **OpenRouter's Decisions endpoint** (``https://openrouter.ai/api/alpha/decisions``,
model slug ``typesafe/jev-latest``) -- the same OpenRouter account and key already used for chat
models (``services/api_access.py``), not a separate TypeSafe account. OpenRouter announced this as a
beta "Decisions API" integration of TypeSafe's System One models; it carries the identical wire shape
TypeSafe's own direct API uses (``model`` + ``state`` + ``questions`` in, ``answers`` out -- see
below), just at OpenRouter's URL and behind OpenRouter's key, so this client only had to change its
defaults, not its request/response handling. See ``backend/middleware/jev_gatekeeper.py`` for what
this is used for, and ``backend/config.py`` for the ``CATCREW_JEV_URL`` / ``CATCREW_JEV_KEY`` escape
hatch that points this client at TypeSafe's own endpoint (or anything else) instead.

Wire contract (identical on both TypeSafe's direct API and OpenRouter's Decisions endpoint):
``POST .../decisions``, ``Authorization: Bearer <key>``, a JSON body of ``model`` + ``state`` +
a ``questions`` map keyed by question id, each a ``noul`` (yes/no probability), ``choice`` (pick one
of up to 255 labelled options) or ``score`` (an ordered scale of 2-10 levels) primitive. The response
is an ``answers`` map, one entry per question id, carrying the typed value and -- for ``choice`` and
``score`` -- a ``confidence``. **System One models never generate free text: Jev classifies, it does
not draft replies.** There is deliberately no "answer text" anywhere in this module; see
``jev_gatekeeper.py`` for how a confident classification is used instead of a drafted answer.

Not independently verified against a live call (no Jev-entitled key was available at implementation
time): the endpoint path and model slug above are corroborated by OpenRouter's own community SDK guide
and multiple third-party client integrations, but this is a beta OpenRouter feature not yet in its main
API reference. If Jev never records a successful decision after being enabled with a valid OpenRouter
key (``GET /api/jev/metrics`` stays at zero, or every decision comes back ``ok=False``), that path or
slug is the first thing to double-check against OpenRouter's current docs.

Documented jaggedness this client's callers should keep in mind: Jev reads a question at face value
(no arithmetic, no date reasoning, no multi-hop inference) and does not treat its input as
adversarial, so ``state`` should stay short and narrowly scoped rather than relying on Jev to notice
something is "off" about it. TypeSafe's own docs also note the model does not guarantee
``P(x) == 1 - P(not x)`` across two separately worded questions, so a caller should always ask a
``noul`` question in the one polarity it actually needs ("is this safe") and read the returned
probability directly, never derive it by negating the complementary question.

Any failure -- network, an unrecognised model, a malformed response -- never raises:
:meth:`JevClient.decide` returns one ``Decision(ok=False, ...)`` per question, and callers treat
``ok=False`` as "no opinion, let it through": Jev exists to save tokens, never to become a new reason
a turn cannot proceed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

PRIMITIVES = ("noul", "choice", "score")

_TIMEOUT_S = 8.0
#: The full Decisions endpoint URL (not a root a suffix gets appended to: TypeSafe's own API and
#: OpenRouter's Decisions API use different path suffixes -- ``/v1/systemone`` vs ``/alpha/decisions``
#: -- so ``base_url`` below is POSTed to exactly as given).
_DEFAULT_BASE_URL = "https://openrouter.ai/api/alpha/decisions"
_DEFAULT_MODEL = "typesafe/jev-latest"


@dataclass
class Question:
    id: str
    type: str  # one of PRIMITIVES
    instructions: str
    #: noul: ``{"true": "...", "false": "..."}`` description of each side.
    #: choice: ``{option: description, ...}``, at most 255 options.
    #: score: ``["lowest level", ..., "highest level"]``, 2-10 ordered levels.
    criteria: Any = None
    #: The value used (with ``ok=False``) if Jev cannot be reached.
    default: Any = None


@dataclass
class Decision:
    id: str
    type: str
    #: noul: ``True``/``False`` from the returned probability (``> 0.5``); choice: the chosen
    #: option string; score: the (possibly fractional) scored value.
    value: Any
    #: 0..1, how sure Jev is in the direction ``value`` reports. For a ``noul`` question this *is*
    #: the returned probability -- ask the question in the polarity you care about and use this
    #: number directly (see the module docstring on why not to derive it by negation).
    confidence: float
    #: Only set on failure (Jev was never reached, or gave back something unusable): the reason
    #: ``ok`` is False. System One models do not explain their answers, so this is empty on success.
    reason: str = ""
    #: False = the call failed, was skipped, or Jev did not answer this question. Never a block
    #: signal by itself -- callers decide what "no opinion" means for them.
    ok: bool = True
    latency_ms: float = 0.0
    #: Raw per-option (choice) / per-level (score) probabilities, when Jev returned them.
    probabilities: dict[str, float] = field(default_factory=dict)


def _default_for(question: Question) -> Any:
    if question.default is not None:
        return question.default
    return {"noul": False, "choice": "", "score": 0.0}[question.type]


def _fallback(questions: list[Question], *, reason: str, latency_ms: float) -> list[Decision]:
    return [
        Decision(id=q.id, type=q.type, value=_default_for(q), confidence=0.0, reason=reason, ok=False, latency_ms=latency_ms)
        for q in questions
    ]


def _question_payload(q: Question) -> dict[str, Any]:
    row: dict[str, Any] = {"type": q.type, "instructions": q.instructions}
    if q.criteria is not None:
        row["criteria"] = q.criteria
    return row


def _clamp01(value: Any, fallback: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def _decision_from_row(q: Question, row: dict[str, Any], latency_ms: float) -> Decision:
    probs_raw = row.get("probabilities")
    probabilities = {str(k): _clamp01(v) for k, v in probs_raw.items()} if isinstance(probs_raw, dict) else {}
    if q.type == "noul":
        p = _clamp01(row.get("noul"))
        return Decision(id=q.id, type="noul", value=p > 0.5, confidence=p, ok=True, latency_ms=latency_ms, probabilities=probabilities)
    if q.type == "choice":
        choice = str(row.get("choice") or "")
        return Decision(id=q.id, type="choice", value=choice, confidence=_clamp01(row.get("confidence")), ok=True, latency_ms=latency_ms, probabilities=probabilities)
    if q.type == "score":
        try:
            score = float(row.get("score"))
        except (TypeError, ValueError):
            score = 0.0
        return Decision(id=q.id, type="score", value=score, confidence=_clamp01(row.get("confidence")), ok=True, latency_ms=latency_ms, probabilities=probabilities)
    return Decision(id=q.id, type=q.type, value=_default_for(q), confidence=0.0, reason="unknown answer type", ok=False, latency_ms=latency_ms)


class JevClient:
    """One call = one round-trip to the Decisions endpoint (OpenRouter's by default; TypeSafe's own
    ``/v1/systemone`` when ``base_url`` is pointed at it instead)."""

    def __init__(self, *, api_key: str, model: str = "", base_url: str = _DEFAULT_BASE_URL, timeout: float = _TIMEOUT_S) -> None:
        self.api_key = api_key
        self.model = model or _DEFAULT_MODEL
        self.base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def decide(self, state: dict[str, Any], questions: list[Question]) -> list[Decision]:
        """Ask all ``questions`` about ``state`` in one request. Always returns one ``Decision`` per
        question, in the order given, even when the call fails."""
        if not questions:
            return []
        if not self.api_key:
            return _fallback(questions, reason="no Jev API key configured", latency_ms=0.0)
        started = time.monotonic()
        body = {
            "model": self.model,
            "state": state,
            "questions": {q.id: _question_payload(q) for q in questions},
        }
        try:
            response = requests.post(
                self.base_url,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - Jev must never fail a turn
            return _fallback(questions, reason=f"{type(exc).__name__}: {str(exc)[:160]}", latency_ms=(time.monotonic() - started) * 1000)
        latency_ms = (time.monotonic() - started) * 1000
        answers = payload.get("answers") if isinstance(payload, dict) else None
        answers = answers if isinstance(answers, dict) else {}
        out: list[Decision] = []
        for q in questions:
            row = answers.get(q.id)
            if not isinstance(row, dict):
                out.append(
                    Decision(id=q.id, type=q.type, value=_default_for(q), confidence=0.0, reason="Jev did not answer this question", ok=False, latency_ms=latency_ms)
                )
                continue
            out.append(_decision_from_row(q, row, latency_ms))
        return out
