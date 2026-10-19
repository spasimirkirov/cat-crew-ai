"""Blaze (marketing & PR) role-specific tools."""

from __future__ import annotations

import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

# What search engines show: Google cuts a title around 60 characters and a description around 160.
_TITLE_RANGE = (15, 60)
_DESCRIPTION_RANGE = (70, 160)


class _HeadParser(HTMLParser):
    """Collects what a search result and a social share card are built from."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.headings: list[tuple[str, str]] = []
        self.images = 0
        self.images_without_alt = 0
        self._in_title = False
        self._heading: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = values.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (values.get("name") or values.get("property") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = values.get("content", "").strip()
        elif tag == "link" and "canonical" in values.get("rel", "").lower():
            self.canonical = values.get("href", "")
        elif tag in ("h1", "h2"):
            self._heading, self._text = tag, []
        elif tag == "img":
            self.images += 1
            if not values.get("alt", "").strip():
                self.images_without_alt += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == self._heading:
            self.headings.append((tag, " ".join("".join(self._text).split())))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._heading:
            self._text.append(data)


def _get(url: str, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "CatCrewAI-SEO-Audit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(2_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def _length_note(text: str, low: int, high: int) -> str:
    if not text:
        return "MISSING"
    n = len(text)
    verdict = "ok" if low <= n <= high else ("too short" if n < low else "too long, will be cut")
    return f"{n} chars, {verdict}"


def seo_page_audit(url: str, timeout: float = 15.0) -> str:
    """Audit one page the way a search engine and a social share card see it.

    Reports the title and meta description (with their length against what Google shows),
    canonical URL, robots meta, the html lang attribute, Open Graph / Twitter card tags, every
    H1 and H2, images without alt text, and whether the site serves robots.txt and sitemap.xml.
    Ends with a list of problems to fix. Plain HTTP (no browser, no JavaScript), so a page that
    only renders client-side shows up nearly empty, which is itself an SEO problem.

    Args:
        url: The page to audit, e.g. the site's APP_URL plus a path. ``http://`` is assumed
            when no scheme is given.
        timeout: Seconds to wait for each request.

    Returns:
        The audit report, or an ``Error:`` message.
    """
    target = (url or "").strip()
    if not target:
        return "Error: empty url."
    if "://" not in target:
        target = "http://" + target
    try:
        status, body = _get(target, float(timeout or 15.0))
    except Exception as exc:  # noqa: BLE001 - unreachable host, bad URL ...
        return f"Error: could not reach {target}: {exc}"
    if status >= 400 or not body:
        return f"Error: GET {target} answered HTTP {status}; nothing to audit."

    page = _HeadParser()
    page.feed(body)
    meta = page.meta
    title = " ".join(page.title.split())
    description = meta.get("description", "")
    h1s = [text for tag, text in page.headings if tag == "h1"]
    h2s = [text for tag, text in page.headings if tag == "h2"]

    origin = "{0.scheme}://{0.netloc}".format(urlsplit(target))
    extras: dict[str, str] = {}
    for path in ("/robots.txt", "/sitemap.xml"):
        try:
            code, _ = _get(urljoin(origin, path), float(timeout or 15.0))
            extras[path] = "present" if code == 200 else f"HTTP {code}"
        except Exception:  # noqa: BLE001
            extras[path] = "not reachable"

    lines = [
        f"SEO audit of {target} (HTTP {status})",
        f"title: {title or '(none)'} [{_length_note(title, *_TITLE_RANGE)}]",
        f"meta description: {description or '(none)'} [{_length_note(description, *_DESCRIPTION_RANGE)}]",
        f"canonical: {page.canonical or '(none)'}",
        f"robots meta: {meta.get('robots') or '(none, defaults to index,follow)'}",
        f"html lang: {page.lang or '(none)'}",
        "open graph: "
        + (", ".join(f"{k}={meta[k][:80]}" for k in ("og:title", "og:description", "og:image", "og:type") if meta.get(k)) or "(none)"),
        f"twitter card: {meta.get('twitter:card') or '(none)'}",
        f"H1 ({len(h1s)}): " + (" | ".join(h1s[:5]) or "(none)"),
        f"H2 ({len(h2s)}): " + (" | ".join(h2s[:12]) or "(none)"),
        f"images: {page.images}, without alt text: {page.images_without_alt}",
        f"robots.txt: {extras['/robots.txt']}; sitemap.xml: {extras['/sitemap.xml']}",
    ]

    problems: list[str] = []
    if not title:
        problems.append("add a <title>")
    elif not _TITLE_RANGE[0] <= len(title) <= _TITLE_RANGE[1]:
        problems.append(f"title should be {_TITLE_RANGE[0]}-{_TITLE_RANGE[1]} characters")
    if not description:
        problems.append('add <meta name="description">')
    elif not _DESCRIPTION_RANGE[0] <= len(description) <= _DESCRIPTION_RANGE[1]:
        problems.append(f"description should be {_DESCRIPTION_RANGE[0]}-{_DESCRIPTION_RANGE[1]} characters")
    if len(h1s) != 1:
        problems.append(f"use exactly one H1 (found {len(h1s)})")
    if not page.lang:
        problems.append('set <html lang="...">')
    if not page.canonical:
        problems.append('add <link rel="canonical">')
    if not meta.get("og:title") or not meta.get("og:image"):
        problems.append("add og:title / og:description / og:image for share cards")
    if page.images_without_alt:
        problems.append(f"give the {page.images_without_alt} image(s) without alt a short description")
    if "noindex" in meta.get("robots", "").lower():
        problems.append("robots meta says noindex: the page will not be listed")
    for path, state in extras.items():
        if state != "present":
            problems.append(f"serve {path}")
    lines.append("problems: " + ("; ".join(problems) if problems else "none found"))
    return "\n".join(lines)
