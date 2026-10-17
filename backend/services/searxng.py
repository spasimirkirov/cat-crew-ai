"""Optional SearXNG JSON search — only if a URL is configured. No Docker."""

from __future__ import annotations

import os
from typing import Any

import requests

from backend.config import SEARXNG_URL


def searxng_urls() -> list[str]:
    from backend.services.settings_store import brains_settings

    brains = brains_settings()
    urls: list[str] = []
    primary = (str(brains.get("searxng_url") or "") or SEARXNG_URL).strip().rstrip("/")
    extra = os.environ.get("CATCREW_SEARXNG_URLS", "")
    for raw in [primary, *[part.strip() for part in extra.split(",")]]:
        text = raw.rstrip("/")
        if text and text not in urls:
            urls.append(text)
    return urls


def parse_searxng(payload: Any, query: str = "") -> list[tuple[str, str, str]]:
    rows = payload.get("results") if isinstance(payload, dict) else None
    hits: list[tuple[str, str, str]] = []
    if not isinstance(rows, list):
        return hits
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("href") or "").strip()
        snippet = str(row.get("content") or row.get("snippet") or "").strip()
        if title and url.startswith("http"):
            hits.append((title, url, snippet))
        if len(hits) >= 8:
            break
    del query
    return hits


def parse_searxng_images(payload: Any, limit: int = 8) -> list[tuple[str, str, str]]:
    """Return (title, page_url, image_url) from a SearXNG images JSON payload."""
    rows = payload.get("results") if isinstance(payload, dict) else None
    hits: list[tuple[str, str, str]] = []
    cap = max(1, min(int(limit or 8), 12))
    if not isinstance(rows, list):
        return hits
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip() or "Image"
        page = str(row.get("url") or row.get("href") or "").strip()
        image = str(
            row.get("img_src")
            or row.get("thumbnail_src")
            or row.get("thumbnail")
            or ""
        ).strip()
        if image.startswith("//"):
            image = "https:" + image
        if page.startswith("//"):
            page = "https:" + page
        if not image.startswith("http"):
            continue
        if image in seen:
            continue
        seen.add(image)
        if not page.startswith("http"):
            page = image
        hits.append((title, page, image))
        if len(hits) >= cap:
            break
    return hits


def _get(url: str, params: dict[str, str]) -> Any:
    response = requests.get(
        url,
        params=params,
        timeout=5,
        headers={
            "User-Agent": "CatCrewAI/0.1 (local desktop assistant)",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    ctype = (response.headers.get("Content-Type") or "").lower()
    if "json" not in ctype and not (response.text or "").lstrip().startswith("{"):
        raise ValueError("SearXNG did not return JSON (enable search.formats: [html, json])")
    return response.json()


def search_searxng(query: str) -> list[tuple[str, str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    origins = searxng_urls()
    if not origins:
        return []
    last_error = ""
    for origin in origins:
        try:
            data = _get(f"{origin}/search", {"q": q, "format": "json", "language": "en"})
            hits = parse_searxng(data, q)
            if hits:
                return hits
            last_error = "empty"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            continue
    if last_error and last_error != "empty":
        raise RuntimeError(last_error)
    return []


def search_searxng_images(query: str, limit: int = 8) -> list[tuple[str, str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    origins = searxng_urls()
    if not origins:
        return []
    last_error = ""
    for origin in origins:
        try:
            data = _get(
                f"{origin}/search",
                {
                    "q": q,
                    "format": "json",
                    "language": "en",
                    "categories": "images",
                },
            )
            hits = parse_searxng_images(data, limit)
            if hits:
                return hits
            last_error = "empty"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            continue
    if last_error and last_error != "empty":
        raise RuntimeError(last_error)
    return []


