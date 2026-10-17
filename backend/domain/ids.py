"""ID and timestamp helpers shared by every domain model."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id(prefix: str = "id") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def str_list(raw: object) -> list[str]:
    """Coerce a loosely-typed JSON value into a deduped list[str].

    Accepts a list of strings, a list of ``{"id": ...}`` dicts (legacy shape
    some catalogs used), or anything falsy -> ``[]``.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("id") or "").strip()
        else:
            text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out
