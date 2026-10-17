"""Shipped connector discovery presets (no secrets)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .model import CatalogEntry

CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CatalogEntry, ...]:
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(raw, list):
        return ()
    out: list[CatalogEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = CatalogEntry.from_dict(item)
        if entry.id:
            out.append(entry)
    return tuple(out)


def catalog_by_id(connector_id: str) -> CatalogEntry | None:
    needle = str(connector_id or "").strip().lower()
    if not needle:
        return None
    for entry in load_catalog():
        if entry.id.lower() == needle:
            return entry
    return None


def reload_catalog() -> tuple[CatalogEntry, ...]:
    load_catalog.cache_clear()
    return load_catalog()
