"""Find photos on the public web and save copies for chat bubbles."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests

from backend.config import BASE_DIR

IMAGE_DIR = BASE_DIR / "data" / "image_search"
MAX_BYTES = 8 * 1024 * 1024
_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def _http_url(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    return text if text.startswith("http://") or text.startswith("https://") else ""


def _stem(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _ext_for(url: str, content_type: str) -> str:
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype in _IMAGE_TYPES:
        return _IMAGE_TYPES[ctype]
    path = urlparse(url).path.lower()
    for suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        if path.endswith(suffix):
            return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def download_image(url: str, *, referer: str = "") -> Path | None:
    target = _http_url(url)
    if not target:
        return None
    headers = dict(_HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        response = requests.get(target, timeout=22, headers=headers, stream=True)
        response.raise_for_status()
    except requests.RequestException:
        return None
    ctype = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if ctype and not ctype.startswith("image/") and ctype not in {"application/octet-stream", "binary/octet-stream"}:
        response.close()
        return None
    chunks: list[bytes] = []
    total = 0
    try:
        for piece in response.iter_content(64 * 1024):
            if not piece:
                continue
            total += len(piece)
            if total > MAX_BYTES:
                return None
            chunks.append(piece)
    finally:
        response.close()
    blob = b"".join(chunks)
    if len(blob) < 32:
        return None
    dest = IMAGE_DIR / f"{_stem(target)}{_ext_for(target, ctype)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    return dest


def parse_brave_images(payload: Any, limit: int = 8) -> list[tuple[str, str, str]]:
    rows = payload.get("results") if isinstance(payload, dict) else None
    hits: list[tuple[str, str, str]] = []
    if not isinstance(rows, list):
        return hits
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        thumb = row.get("thumbnail") if isinstance(row.get("thumbnail"), dict) else {}
        image = _http_url(str(props.get("url") or thumb.get("src") or ""))
        page = _http_url(str(row.get("url") or row.get("source") or "")) or image
        title = str(row.get("title") or "").strip() or "Image"
        if not image or image in seen:
            continue
        seen.add(image)
        hits.append((title, page, image))
        if len(hits) >= max(1, min(int(limit or 8), 12)):
            break
    return hits


def search_brave_images(query: str, limit: int = 8) -> list[tuple[str, str, str]]:
    from backend.services.api_access import brave_search_key

    key = brave_search_key()
    if not key:
        return []
    response = requests.get(
        "https://api.search.brave.com/res/v1/images/search",
        params={"q": query, "count": max(1, min(int(limit or 8), 12))},
        timeout=18,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
    )
    response.raise_for_status()
    return parse_brave_images(response.json(), limit)


def parse_wikimedia_images(payload: Any, limit: int = 8) -> list[tuple[str, str, str]]:
    pages = ((payload or {}).get("query") or {}).get("pages") if isinstance(payload, dict) else None
    hits: list[tuple[str, str, str]] = []
    if not isinstance(pages, dict):
        return hits
    seen: set[str] = set()
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        info = page.get("imageinfo")
        if not isinstance(info, list) or not info:
            continue
        row = info[0] if isinstance(info[0], dict) else {}
        image = _http_url(str(row.get("thumburl") or row.get("url") or ""))
        page_url = _http_url(str(row.get("descriptionurl") or row.get("descriptionshorturl") or "")) or image
        title = str(page.get("title") or "").replace("File:", "").strip() or "Image"
        if not image or image in seen:
            continue
        seen.add(image)
        hits.append((title, page_url, image))
        if len(hits) >= max(1, min(int(limit or 8), 12)):
            break
    return hits


def search_wikimedia_images(query: str, limit: int = 8) -> list[tuple[str, str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    response = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": q,
            "gsrnamespace": "6",
            "gsrlimit": str(max(1, min(int(limit or 8), 12))),
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "iiurlwidth": "1280",
            "format": "json",
        },
        timeout=18,
        headers={
            "User-Agent": "CatCrewAI/0.1 (local desktop assistant)",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    return parse_wikimedia_images(response.json(), limit)


def collect_image_hits(query: str, limit: int = 6) -> list[tuple[str, str, str]]:
    q = (query or "").strip()
    cap = max(1, min(int(limit or 6), 8))
    if not q:
        return []
    hits: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def take(chunk: list[tuple[str, str, str]]) -> None:
        for title, page, image in chunk:
            if image in seen:
                continue
            seen.add(image)
            hits.append((title, page, image))
            if len(hits) >= cap:
                return

    from backend.services.ddgs_search import search_ddgs_images
    from backend.services.searxng import search_searxng_images

    for searcher in (
        search_ddgs_images,
        search_searxng_images,
        search_brave_images,
        search_wikimedia_images,
    ):
        try:
            take(searcher(q, cap))
        except (requests.RequestException, ValueError, TypeError, RuntimeError, OSError) as exc:
            print(f"[image_search] {searcher.__name__} failed: {exc}", flush=True)
        if len(hits) >= cap:
            break
    return hits[:cap]


def image_search(query: str, limit: int = 6) -> str:
    q = (query or "").strip()
    if not q:
        return "Error: empty image search."
    cap = max(1, min(int(limit or 6), 8))
    hits = collect_image_hits(q, cap)
    if not hits:
        return (
            f"Image search: {q}\n\nNo photos found. Try a more concrete subject."
        )
    saved: list[str] = []
    lines = [f"Image search: {q}", ""]
    for title, page, image in hits:
        path = download_image(image, referer=page)
        safe_title = re.sub(r"[\r\n]+", " ", title)[:80]
        if path is not None:
            posix = path.as_posix()
            saved.append(f"![{safe_title}]({posix})")
            lines.append(f"- {safe_title}\n  {page}\n  {posix}")
        else:
            lines.append(f"- {safe_title}\n  {page}\n  {image} (could not download)")
    if saved:
        lines.append("")
        lines.extend(saved)
    elif not any("could not download" in line for line in lines):
        return f"Image search: {q}\n\nFound URLs but none could be downloaded."
    return "\n".join(lines).strip()
