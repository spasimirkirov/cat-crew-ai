"""Clock, home city, and 'should we fetch weather' heuristics."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any


_TZ_HOME = {
    "sofia": "Sofia",
    "fle": "Sofia",
    "eest": "Sofia",
    "eet": "Sofia",
    "europe/sofia": "Sofia",
    "berlin": "Berlin",
    "paris": "Paris",
    "london": "London",
    "athens": "Athens",
    "bucharest": "Bucharest",
    "istanbul": "Istanbul",
    "madrid": "Madrid",
    "rome": "Rome",
    "vienna": "Vienna",
    "warsaw": "Warsaw",
    "prague": "Prague",
    "amsterdam": "Amsterdam",
    "new york": "New York",
    "chicago": "Chicago",
    "los angeles": "Los Angeles",
    "tokyo": "Tokyo",
}

_YES = re.compile(
    r"^\s*(yes|yep|yeah|yea|ok|okay|sure|please|do it|go ahead|do that|"
    r"да|ок|добре|моля)\b",
    re.I,
)
_WEATHER_HINT = re.compile(
    r"(weather|forecast|temperature|времето|\bвреме\b|прогноза)",
    re.I,
)
_OFFERED = (
    "search the web",
    "search for",
    "would you like me",
    "real-time weather",
    "latest weather",
    "i can help you find",
    "want me to look",
)


def locale_city() -> str:
    from .settings_store import locale_settings

    saved = str(locale_settings().get("home_city") or "").strip()
    return saved or infer_home_city()


def infer_home_city() -> str:
    bits = [
        str(datetime.now().astimezone().tzname() or ""),
        str(datetime.now().astimezone().tzinfo or ""),
        *(time.tzname or ()),
    ]
    blob = " ".join(bits).lower()
    for key, city in _TZ_HOME.items():
        if key in blob:
            return city
    return ""


def clock_now() -> datetime:
    return datetime.now().astimezone()


def here_and_now() -> str:
    now = clock_now()
    tomorrow = now + timedelta(days=1)
    city = locale_city()
    home = (
        f"Home city: {city}. If they say tomorrow/today/here and name no city, use {city}."
        if city
        else "Home city: not set — use the IP location from wttr.in / search."
    )
    offset = now.strftime("%z")
    utc = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    now_txt = f"{now.strftime('%A')} {now.day} {now.strftime('%B %Y, %H:%M')}"
    tom_txt = f"{tomorrow.strftime('%A')} {tomorrow.day} {tomorrow.strftime('%B %Y')}"
    return (
        f"Now: {now_txt} ({now.tzname() or 'local'}, {utc}). "
        f"Tomorrow is {tom_txt}. "
        f"{home} "
        "You already have the date and time. Do not claim you lack a clock. "
        "Do not ask permission to search the web — call web_search. "
        "If they already said yes, search immediately. "
        "Never list weather websites. Never say you cannot give a forecast."
    )


def looks_like_weather(query: str) -> bool:
    return bool(_WEATHER_HINT.search(query or ""))


def looks_like_affirmation(text: str) -> bool:
    return bool(_YES.search((text or "").strip()))


def assistant_offered_search(history: list[dict[str, Any]] | None) -> bool:
    for msg in reversed(history or []):
        role = msg.get("role")
        if role == "assistant":
            low = str(msg.get("content") or "").lower()
            return any(token in low for token in _OFFERED)
        if role == "user":
            break
    return False


def should_attach_forecast(text: str, history: list[dict[str, Any]] | None = None) -> bool:
    if looks_like_weather(text):
        return True
    return looks_like_affirmation(text) and assistant_offered_search(history)
