"""In-memory, process-local live status for the sidebar roster.

Master Architecture Document Phase 2 (UI & Agent Visibility): the sidebar
needs to answer "what is Ekko doing right now, and when did we last hear
from her" without the user having to open her 1:1 chat. That is inherently
transient, per-process state — it stops mattering the instant the app
restarts — so this is a plain thread-safe dict, not a ``state/`` repository
row.

Writers are background threads: the turn runner (the bot the user is chatting
with directly) and ``platform/runner.run_bot`` (a bot running as a delegate,
on the engine's worker thread). Readers are API request handlers — so reads
must never block on agent work. A ``threading.Lock`` around a dict satisfies
both.

Real-time push: every write that changes what a sidebar card should show is
also announced on ``platform/bus.py`` as a ``bot.activity`` event (after the
lock is released, de-duplicated so an unchanged snapshot is never
re-emitted). The API layer forwards it over ``/ws/events``, so the fleet
reacts the instant a bot starts, changes tool or finishes — no polling.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
# bot_id (lowercased) -> (activity text, last-updated unix time)
_activity: dict[str, tuple[str, float]] = {}
# bot_id (lowercased) -> "working" | "idle". Separate from _activity so
# existing (text, ts) readers are unaffected; added so a 1:1 chat can ask
# "is this bot busy right now" (e.g. running as a Cat Crew delegate in
# a completely different session) without caring which session recorded
# that work. Load-bearing: callers compare this against the
# literal string "working" to decide busy/idle — never repurpose its
# vocabulary.
_state: dict[str, str] = {}
# bot_id (lowercased) -> one of the five sidebar status words
# (idle/thinking/coding/testing/chatting — UI Refactor spec, Section 2).
# Deliberately separate from _state above: this is purely the richer
# display word for the sidebar's status pill, so the pushed event and a
# REST read of the same bot can never disagree about what a card should show.
_status_word: dict[str, str] = {}
# bot_id (lowercased) -> last (status, state, text) announced on the bus, so
# back-to-back writes that end up at the same snapshot emit exactly once.
_published: dict[str, tuple[str, str, str]] = {}

# Tool name -> sidebar status word, for a bot running as a delegate (no
# phase label available at tool granularity). Anything not listed that only
# reads/plans stays "thinking".
_CODING_TOOLS = frozenset(
    {"write_file", "replace_in_file", "edit_file", "apply_patch", "create_file", "delete_file"}
)
_TESTING_TOOLS = frozenset(
    {"browser_check", "run_smoke_test", "run_tests", "run_test", "verify_build", "check_build"}
)
_COMMAND_TOOLS = frozenset({"run_command", "run_shell", "run_code_mode"})


def _key(bot_id: str) -> str:
    return (bot_id or "").strip().lower()


def set_activity(bot_id: str, text: str) -> None:
    """Record what ``bot_id`` is doing right now, timestamped as of now."""
    key = _key(bot_id)
    if not key:
        return
    with _lock:
        _activity[key] = (str(text or "").strip(), time.time())
    _publish(bot_id)


def touch(bot_id: str) -> None:
    """Bump the last-active time without changing the activity text."""
    key = _key(bot_id)
    if not key:
        return
    with _lock:
        prev_text = _activity.get(key, ("", 0.0))[0]
        _activity[key] = (prev_text, time.time())


def mark_idle(bot_id: str, note: str = "Idle") -> None:
    key = _key(bot_id)
    if not key:
        return
    # One atomic write (text + state + status word) so the bus announces a
    # single "went idle" snapshot instead of a half-updated intermediate.
    with _lock:
        _activity[key] = (str(note or "").strip(), time.time())
        _state[key] = "idle"
        _status_word[key] = "idle"
    _publish(bot_id)


def set_state(bot_id: str, state: str) -> None:
    """Record whether ``bot_id`` is currently "working" or "idle",
    independent of which session's turn is the one doing the recording —
    a specialist runs as a delegate inside whichever session owns the
    Cat Crew turn, not its own 1:1 session, so this has to be a
    global lookup rather than something read off session state."""
    key = _key(bot_id)
    if not key:
        return
    with _lock:
        _state[key] = (str(state or "idle").strip().lower() or "idle")
    _publish(bot_id)


def set_working(bot_id: str, word: str, text: str = "") -> None:
    """Mark ``bot_id`` busy with a status word and caption in ONE write.

    Three separate setters would announce three intermediate snapshots (an
    "idle" card carrying a caption, then "thinking", then "coding"); this
    announces a single, already-correct one, so a card never flickers."""
    key = _key(bot_id)
    if not key:
        return
    with _lock:
        _state[key] = "working"
        _status_word[key] = (str(word or "thinking").strip().lower() or "thinking")
        _activity[key] = (str(text or "").strip(), time.time())
    _publish(bot_id)


def get_state(bot_id: str) -> str:
    """Return "working" or "idle" (default) for ``bot_id``."""
    key = _key(bot_id)
    with _lock:
        return _state.get(key, "idle")


def set_status_word(bot_id: str, word: str) -> None:
    """Record the sidebar's current live-status word for ``bot_id``
    (idle/thinking/coding/testing/chatting)."""
    key = _key(bot_id)
    if not key:
        return
    with _lock:
        _status_word[key] = (str(word or "idle").strip().lower() or "idle")
    _publish(bot_id)


def display_status(bot_id: str) -> str:
    """The word a sidebar status pill should show *right now*.

    ``idle`` whenever the bot is not busy (a stale "coding" word left over
    from a finished turn must never keep a pill lit); a busy bot with no
    specific word yet shows "thinking" instead of a misleading "Idle".
    """
    key = _key(bot_id)
    with _lock:
        return _display_status_locked(key)


def _display_status_locked(key: str) -> str:
    state = _state.get(key, "idle")
    word = _status_word.get(key, "idle")
    if state != "working":
        return "idle"
    return word if word and word != "idle" else "thinking"


def status_word_for_tool(tool_name: str) -> str:
    """Map a tool call onto the sidebar's status vocabulary."""
    name = (tool_name or "").strip().lower()
    if name in _TESTING_TOOLS:
        return "testing"
    if name in _CODING_TOOLS or name in _COMMAND_TOOLS:
        return "coding"
    return "thinking"


_publish_lock = threading.Lock()


def _publish(bot_id: str) -> None:
    """Announce this bot's current snapshot on the bus, if it changed.

    Snapshot + emit happen under ``_publish_lock`` (separate from ``_lock``,
    which guards the state dicts) so two threads can never deliver their
    snapshots out of order — a stale "idle" landing after "working" would
    leave a card looking idle mid-task. Bus subscribers are non-blocking by
    contract, so holding the lock across ``emit`` is safe. Best-effort: a
    failed bus must never break a writer.
    """
    key = _key(bot_id)
    if not key:
        return
    with _publish_lock:
        with _lock:
            snapshot = (
                _display_status_locked(key),
                _state.get(key, "idle"),
                _activity.get(key, ("", 0.0))[0],
            )
            if _published.get(key) == snapshot:
                return
            _published[key] = snapshot
        try:
            from .bus import emit_bot_activity
            from .runtime import current_project_id

            emit_bot_activity(str(bot_id).strip(), snapshot[0], snapshot[1], snapshot[2], current_project_id())
        except Exception:  # noqa: BLE001
            pass


def get_activity(bot_id: str) -> tuple[str, float]:
    """Return (text, last_active_unix_ts). ``("", 0.0)`` means never seen."""
    key = _key(bot_id)
    with _lock:
        return _activity.get(key, ("", 0.0))


def humanize_age(ts: float) -> str:
    """"Last active" phrasing for a sidebar timestamp label."""
    if not ts:
        return "Never active"
    delta = max(0.0, time.time() - ts)
    if delta < 5:
        return "Active now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def reset() -> None:
    """Test/teardown hook — clear all recorded activity."""
    with _lock:
        _activity.clear()
        _state.clear()
        _status_word.clear()
        _published.clear()
