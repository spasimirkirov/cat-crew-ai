"""DuckDuckGo search via the ddgs library (https://github.com/deedy5/ddgs).

No Docker. This is the default web/image search backend.
"""

from __future__ import annotations

from typing import Any


def parse_ddgs_text(payload: Any, limit: int = 8) -> list[tuple[str, str, str]]:
    rows = payload if isinstance(payload, list) else []
    hits: list[tuple[str, str, str]] = []
    cap = max(1, min(int(limit or 8), 10))
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("href") or row.get("url") or "").strip()
        snippet = str(row.get("body") or row.get("description") or "").strip()
        if not title or not url.startswith("http"):
            continue
        if url in seen:
            continue
        seen.add(url)
        hits.append((title, url, snippet))
        if len(hits) >= cap:
            break
    return hits


def parse_ddgs_images(payload: Any, limit: int = 8) -> list[tuple[str, str, str]]:
    """Return (title, page_url, image_url)."""
    rows = payload if isinstance(payload, list) else []
    hits: list[tuple[str, str, str]] = []
    cap = max(1, min(int(limit or 8), 12))
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip() or "Image"
        page = str(row.get("url") or row.get("source") or "").strip()
        image = str(row.get("image") or row.get("thumbnail") or "").strip()
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


def _client():
    from ddgs import DDGS

    return DDGS()


def search_ddgs(query: str, limit: int = 8) -> list[tuple[str, str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        client = _client()
    except ImportError as exc:
        raise RuntimeError("ddgs is not installed") from exc
    cap = max(1, min(int(limit or 8), 10))
    rows = list(client.text(q, max_results=cap))
    return parse_ddgs_text(rows, cap)


def search_ddgs_images(query: str, limit: int = 8) -> list[tuple[str, str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        client = _client()
    except ImportError as exc:
        raise RuntimeError("ddgs is not installed") from exc
    cap = max(1, min(int(limit or 8), 12))
    rows = list(client.images(q, max_results=cap))
    return parse_ddgs_images(rows, cap)
