"""Universal human-friendly activity timestamp (UI Refactor spec, Section 3).

One rule, everywhere a "when did this happen" label is shown — Recent
Changes rows, task results, and chat activity cards all called this
instead of each screen inventing its own formatting (platform_page.py's
``_iso_to_epoch`` + ``activity_board.humanize_age`` combo, chat_transcript
cards doing their own thing, etc.):

* Under 24 hours old -> relative ("just now", "5 minutes ago", "1 hour ago").
* 24 hours or older -> an absolute date ("Sep 18, 2026").

Deliberately has no UI or framework import — every screen that renders a timestamp
(a plain QWidget page, a headless report, a future non-GUI export) can use
it without dragging in Qt, and it never raises on bad input; unparsable
input degrades to ``""`` the same way the rest of this codebase treats
"no data" (see platform_page.py's old ``_iso_to_epoch``), not a crash.
"""

from __future__ import annotations

import calendar
import time
from datetime import datetime, timezone

# db.py's _now() writes UTC as "%Y-%m-%dT%H:%M:%SZ" — the format essentially
# every created_at column in this app uses. A couple of extra shapes are
# accepted defensively (a stray offset, a space instead of "T") since this
# helper is meant to be the one place every screen can hand a raw DB value
# to without pre-parsing it first.
_KNOWN_FORMATS = (
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


def _parse_to_epoch(value: object) -> float | None:
    """Best-effort: accept an epoch number, an ISO-ish string, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    for fmt in _KNOWN_FORMATS:
        try:
            return float(calendar.timegm(time.strptime(text, fmt)))
        except ValueError:
            continue
    return None


def _relative(age_seconds: float) -> str:
    seconds = int(age_seconds)
    if seconds < 45:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def _absolute(epoch: float) -> str:
    # Avoid strftime's "%-d"/"%#d" no-leading-zero day flags entirely —
    # "%-d" is a glibc extension that isn't portable to Windows' msvcrt
    # strftime, and this app ships on Windows — so the day number is
    # built by hand instead of relying on a platform-specific format code.
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone()
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def format_activity_timestamp(created_at: object) -> str:
    """Relative under 24h ("5 minutes ago"), absolute date at/after 24h
    ("Sep 18, 2026"). Accepts an epoch float/int or an ISO-ish string
    (whatever a ``created_at`` DB column holds). Returns "" for anything
    that can't be parsed, so callers can render it exactly like an empty
    optional field rather than special-casing failures."""
    epoch = _parse_to_epoch(created_at)
    if epoch is None:
        return ""
    age = max(0.0, time.time() - epoch)
    if age < 24 * 3600:
        return _relative(age)
    return _absolute(epoch)
