"""Wake / sleep phrases for hands-free talk (Siri-style session)."""

from __future__ import annotations

import re

_FILLER = frozenset(
    {"um", "uh", "ah", "er", "erm", "hmm"}
)
_GREET = frozenset(
    {
        "hey",
        "hi",
        "hello",
        "okay",
        "ok",
        "yo",
    }
)
_NAME = frozenset(
    {
        "jinx",
        "jinks",
        "jynx",
        "jynks",
        "ginx",
    }
)
_LEAD = frozenset(
    {"thanks", "thank", "you", "ok", "okay", "please", "alright", "well", "so", "and"}
)
_SLEEP = frozenset(
    {
        ("bye",),
        ("goodbye",),
        ("good", "bye"),
        ("ok", "bye"),
        ("okay", "bye"),
        ("bye", "bye"),
        ("see", "you"),
        ("see", "ya"),
        ("thats", "all"),
        ("that", "is", "all"),
        ("thats", "it"),
        ("that", "is", "it"),
        ("thats", "everything"),
        ("go", "to", "sleep"),
        ("go", "sleep"),
        ("good", "night"),
        ("goodnight",),
        ("stop", "listening"),
        ("never", "mind"),
        ("nevermind",),
        ("cancel",),
        ("were", "done"),
        ("we", "are", "done"),
        ("im", "done"),
        ("i", "am", "done"),
        ("ciao",),
    }
)


def _tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[^A-Za-z0-9']+", " ", (text or "").lower())
    return [tok for tok in cleaned.split() if tok]


def _norm(tok: str) -> str:
    return tok.replace("'", "").replace("’", "")


def split_wake(text: str) -> tuple[bool, str]:
    """Return (wake_heard, remainder). Remainder is empty for a bare wake."""
    raw = " ".join((text or "").split())
    if not raw:
        return False, ""
    tokens = _tokens(raw)
    name_at = None
    for i, tok in enumerate(tokens[:5]):
        if tok in _NAME:
            name_at = i
            break
    if name_at is None:
        return False, raw
    prefix = tokens[:name_at]
    if any(tok not in _GREET and tok not in _FILLER for tok in prefix):
        return False, raw
    i = name_at + 1
    while i < len(tokens) and tokens[i] in _FILLER | _GREET | _NAME:
        i += 1
    return True, " ".join(tokens[i:])


def is_sleep(text: str) -> bool:
    """True when the utterance is a dismiss / go-to-sleep phrase."""
    tokens = [_norm(tok) for tok in _tokens(text)]
    names = {_norm(n) for n in _NAME}
    while tokens and tokens[-1] in names:
        tokens.pop()
    if not tokens:
        return False
    if tuple(tokens) in _SLEEP:
        return True
    peeled = list(tokens)
    while peeled and peeled[0] in _LEAD:
        peeled.pop(0)
        if tuple(peeled) in _SLEEP:
            return True
    return False


def interpret_utterance(text: str, *, awake: bool) -> tuple[str, str]:
    """Classify one transcript.

    Returns (kind, payload):
      ignore  — still asleep, not a wake
      wake    — start / keep the listening session (no command yet)
      command — user task (session should be / stay awake)
      sleep   — goodbye / that's all
    """
    raw = " ".join((text or "").split())
    if len(raw) < 2:
        return ("wake", "") if awake else ("ignore", "")
    hit, rest = split_wake(raw)
    if awake:
        body = rest if hit else raw
        if is_sleep(raw) or is_sleep(body):
            return "sleep", ""
        if len(body) < 2:
            return "wake", ""
        return "command", body
    if not hit:
        return "ignore", raw
    if not rest:
        return "wake", ""
    if is_sleep(rest):
        return "sleep", ""
    return "command", rest
