"""Living desktop loop: daily journal, sleep, wrap-up. No chat spam."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import BASE_DIR
from .here import clock_now

LIFE_DIR = BASE_DIR / "data" / "life"
STATE_PATH = LIFE_DIR / "state.json"

_NOTE = 240
_WRAP_LINES = 80

_DEFAULT_STATE: dict[str, Any] = {
    "phase": "awake",
    "day": "",
    "last_tick": "",
    "last_user": "",
    "last_wrapup_day": "",
    "ticks": 0,
}


def life_dir() -> Path:
    LIFE_DIR.mkdir(parents=True, exist_ok=True)
    return LIFE_DIR


def journal_path(day: str) -> Path:
    return life_dir() / f"{day}.jsonl"


def wrap_path(day: str) -> Path:
    return life_dir() / f"{day}.wrap.md"


def load_state() -> dict[str, Any]:
    data = dict(_DEFAULT_STATE)
    try:
        if STATE_PATH.is_file():
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
    except (OSError, json.JSONDecodeError):
        pass
    data["phase"] = "sleeping" if data.get("phase") == "sleeping" else "awake"
    data["day"] = str(data.get("day") or "").strip()
    data["last_tick"] = str(data.get("last_tick") or "")
    data["last_user"] = str(data.get("last_user") or "")
    data["last_wrapup_day"] = str(data.get("last_wrapup_day") or "")
    try:
        data["ticks"] = int(data.get("ticks") or 0)
    except (TypeError, ValueError):
        data["ticks"] = 0
    return data


def save_state(update: dict[str, Any]) -> dict[str, Any]:
    data = load_state()
    data.update(update)
    life_dir()
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def day_key(now: datetime | None = None) -> str:
    return (now or clock_now()).date().isoformat()


def is_sleep_window(now: datetime, sleep_hour: int, wake_hour: int) -> bool:
    hour = int(now.hour)
    sleep_hour = int(sleep_hour)
    wake_hour = int(wake_hour)
    if sleep_hour == wake_hour:
        return False
    if sleep_hour > wake_hour:
        return hour >= sleep_hour or hour < wake_hour
    return sleep_hour <= hour < wake_hour


def idle_minutes(now: datetime, last_user: str) -> float:
    if not last_user:
        return 0.0
    try:
        then = datetime.fromisoformat(last_user)
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=now.tzinfo)
    delta = now - then
    return max(0.0, delta.total_seconds() / 60.0)


def clip_note(text: str, cap: int = _NOTE) -> str:
    blob = " ".join(str(text or "").split())
    if len(blob) <= cap:
        return blob
    return blob[: cap - 1] + "…"


def decide(
    now: datetime,
    *,
    enabled: bool,
    phase: str,
    day: str,
    last_user: str,
    last_wrapup_day: str,
    busy: bool,
    sleep_hour: int,
    wake_hour: int,
    idle_sleep_min: int,
    mind_every: int,
    ticks: int,
) -> str:
    """Return one of: off, skip_busy, skip_sleep, wake, wrap, thought, log."""
    if not enabled:
        return "off"
    if busy:
        return "skip_busy"
    if phase == "sleeping":
        if is_sleep_window(now, sleep_hour, wake_hour):
            return "skip_sleep"
        return "wake"
    if is_sleep_window(now, sleep_hour, wake_hour):
        wrapped = last_wrapup_day == (day or day_key(now))
        idle = idle_minutes(now, last_user)
        if not wrapped and idle >= max(1, int(idle_sleep_min)):
            return "wrap"
        if wrapped:
            return "skip_sleep"
    every = max(0, int(mind_every or 0))
    if every and ticks > 0 and ticks % every == 0:
        return "thought"
    return "log"


def log_event(kind: str, note: str = "", *, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
    state = load_state()
    kind = str(kind or "note").strip().lower() or "note"
    if state["phase"] == "sleeping" and kind not in {"wake", "wrapup", "start"}:
        return None
    now = clock_now()
    day = state["day"] or day_key(now)
    if kind == "wake":
        day = day_key(now)
    row: dict[str, Any] = {
        "t": now.isoformat(timespec="seconds"),
        "kind": kind,
        "note": clip_note(note),
    }
    if extra:
        for key, value in extra.items():
            if key in {"t", "kind", "note"}:
                continue
            row[key] = value
    path = journal_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    patch: dict[str, Any] = {"day": day, "last_tick": now.isoformat(timespec="seconds")}
    if kind == "user":
        patch["last_user"] = now.isoformat(timespec="seconds")
        patch["phase"] = "awake"
    if kind == "wake":
        patch["phase"] = "awake"
        patch["last_user"] = now.isoformat(timespec="seconds")
    if kind == "wrapup":
        patch["last_wrapup_day"] = day
        patch["phase"] = "sleeping"
    if kind == "heartbeat":
        patch["ticks"] = int(state.get("ticks") or 0) + 1
    save_state(patch)
    return row


def mark_user() -> None:
    now = clock_now()
    state = load_state()
    if state["phase"] == "sleeping":
        log_event("wake", "User is here")
    else:
        save_state({"last_user": now.isoformat(timespec="seconds"), "phase": "awake"})


def read_journal(day: str, *, limit: int = _WRAP_LINES) -> list[dict[str, Any]]:
    path = journal_path(day)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except OSError:
        return []
    if limit and len(rows) > limit:
        return rows[-limit:]
    return rows


def format_journal(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        stamp = str(row.get("t") or "")
        try:
            when = datetime.fromisoformat(stamp).strftime("%H:%M")
        except ValueError:
            when = stamp[11:16] if len(stamp) >= 16 else stamp
        kind = str(row.get("kind") or "note")
        note = str(row.get("note") or "")
        lines.append(f"{when} [{kind}] {note}".strip())
    return "\n".join(lines)


def wrapup_prompt(day: str) -> str:
    rows = read_journal(day, limit=_WRAP_LINES)
    body = format_journal(rows) or "(no events logged)"
    return (
        "You are Jinx. This is a private end-of-day wrap-up, not a chat with the user. "
        "Read the day's activity log and write a short first-person journal in English: "
        "what you did, what the user asked, tools you used, anything still open. "
        "Under 180 words. No greetings, no tools, no HEARTBEAT_OK.\n\n"
        f"Day: {day}\n\n{body}"
    )


def thought_prompt(snapshot: str, day: str) -> str:
    rows = read_journal(day, limit=24)
    tail = format_journal(rows) or "(quiet so far)"
    return (
        "You are Jinx on the desktop. This is a private heartbeat. "
        "If nothing needs attention, reply with exactly HEARTBEAT_OK. "
        "Otherwise one short inner note (under 40 words). No tools, no greeting.\n\n"
        f"{snapshot}\n\nRecent log:\n{tail}"
    )


def save_wrap(day: str, text: str) -> None:
    blob = (text or "").strip()
    if not blob:
        return
    wrap_path(day).write_text(blob + "\n", encoding="utf-8")
    log_event("wrapup", clip_note(blob, 400))


def event_count(day: str) -> int:
    path = journal_path(day)
    if not path.is_file():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def snapshot_line(
    *,
    mode: str,
    busy: bool,
    overlay: str,
    local: bool | None,
    comfyui: bool | None,
) -> str:
    now = clock_now()
    bits = [
        now.strftime("%A %H:%M"),
        str(mode or "developer"),
        "busy" if busy else "idle",
        f"orb {overlay or 'idle'}",
    ]
    for name, ok in (("Local", local), ("ComfyUI", comfyui)):
        if ok is True:
            bits.append(f"{name} up")
        elif ok is False:
            bits.append(f"{name} down")
    return " · ".join(bits)


def default_last_user(now: datetime | None = None) -> str:
    stamp = (now or clock_now()).isoformat(timespec="seconds")
    save_state({"last_user": stamp, "phase": "awake", "day": day_key(now)})
    return stamp
