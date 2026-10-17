"""Interactive browser tools for Aegis (QA) and Pixel (UI/UX) — Playwright.

An LLM calls these one at a time across several turns (navigate, read the
page, fill a field, click submit, read again), so the whole point of this
module is a single shared Playwright page that survives *between* calls
instead of each tool opening and closing its own browser.

Three properties matter:

* **Text first.** CatCrewAI does not attach images to a chat, so a model —
  vision-capable or not — never sees a screenshot. Every tool therefore
  returns plain text, and ``browser_snapshot`` / ``browser_inspect`` give the
  model the facts a screenshot would (what is on the page, where, how it is
  styled, whether it overflows, whether the text is readable). A model
  without vision loses nothing; ``browser_take_screenshot`` only writes a PNG
  for the user or a bug report. When the user has set a **Vision** model,
  ``browser_look`` sends the screenshot to *that* model and returns its
  description as text, so the calling model still only ever reads text.
* **One owner thread.** Playwright's sync API is bound to the thread that
  started it, and agent turns run on different threads (the engine thread,
  ``asyncio.to_thread`` workers). Every browser call is therefore funnelled
  through one dedicated worker thread.
* **One page per cat.** Aegis and Pixel each get their own browser context, so
  two cats testing at once do not navigate each other's page.

Registered into the ``tools`` catalog and assigned to Aegis and Pixel by
``platform/role_tools.py``.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

_CALL_TIMEOUT_S = 90.0
_MAX_SNAPSHOT_ITEMS = 150
_MAX_CONSOLE_KEPT = 100
_DEFAULT_VIEWPORT = {"width": 1280, "height": 800}

# All Playwright objects below are created and used on this one thread only.
_worker = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="catcrew-browser"
)
_pw: Any = None
_browser: Any = None
_sessions: dict[str, dict[str, Any]] = {}  # cat id -> {"context", "page", "console"}


def _run(fn: Callable[[], Any]) -> Any:
    """Run ``fn`` on the browser thread and return its result (an ``Error:`` string on failure)."""
    try:
        return _worker.submit(fn).result(timeout=_CALL_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        return (
            f"Error: the browser did not answer within {int(_CALL_TIMEOUT_S)}s. "
            "Call browser_close and start again with browser_navigate."
        )
    except Exception as exc:  # noqa: BLE001 - a tool call must never raise into the loop
        return f"Error: {exc}"


def _session_key() -> str:
    """The calling cat's id, read on the caller's thread (a ContextVar does not
    follow work onto the browser thread)."""
    try:
        from ..platform.runtime import current_bot_id

        return current_bot_id() or "default"
    except Exception:  # noqa: BLE001
        return "default"


# --------------------------------------------------------------------------
# Session management (browser thread only)
# --------------------------------------------------------------------------


def _start_browser() -> Any:
    global _pw, _browser
    if _browser is not None:
        try:
            if _browser.is_connected():
                return _browser
        except Exception:  # noqa: BLE001
            pass
        _stop_browser()

    from .. import tools as _tools

    err = _tools._ensure_playwright()
    if err:
        raise RuntimeError(err)

    from playwright.sync_api import sync_playwright

    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(headless=True)
    return _browser


def _stop_browser() -> None:
    global _pw, _browser
    for entry in list(_sessions.values()):
        try:
            entry["context"].close()
        except Exception:  # noqa: BLE001
            pass
    _sessions.clear()
    if _browser is not None:
        try:
            _browser.close()
        except Exception:  # noqa: BLE001
            pass
    _browser = None
    if _pw is not None:
        try:
            _pw.stop()
        except Exception:  # noqa: BLE001
            pass
    _pw = None


def _open_page(key: str) -> Any:
    """The cat's live page, opening a fresh context/page when there is none (or
    the previous one died)."""
    page = _existing_page(key)
    if page is not None:
        return page
    browser = _start_browser()
    context = browser.new_context(viewport=dict(_DEFAULT_VIEWPORT))
    page = context.new_page()
    console: list[str] = []

    def _note(line: str) -> None:
        console.append(line)
        del console[:-_MAX_CONSOLE_KEPT]

    page.on(
        "console",
        lambda m: _note(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None,
    )
    page.on("pageerror", lambda e: _note(f"[exception] {e}"))
    _sessions[key] = {"context": context, "page": page, "console": console}
    return page


def _existing_page(key: str) -> Any | None:
    entry = _sessions.get(key)
    if entry is None:
        return None
    try:
        _ = entry["page"].url  # cheap liveness probe: raises if the page/browser died
        return entry["page"]
    except Exception:  # noqa: BLE001
        _sessions.pop(key, None)
        return None


_NO_SESSION = "Error: no active browser session — call browser_navigate first."


def close_all_sessions() -> None:
    """Close every browser session and the browser itself (app shutdown)."""
    if _browser is None and _pw is None and not _sessions:
        return
    try:
        _worker.submit(_stop_browser).result(timeout=15)
    except Exception:  # noqa: BLE001
        pass


atexit.register(close_all_sessions)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


_BARE_SCHEME = re.compile(
    r"^(data|javascript|file|about|chrome|blob|view-source|ftp|mailto|ws|wss):", re.IGNORECASE
)


def _normalize_url(raw: str) -> tuple[str, str]:
    """``(url, error)``. ``http://`` is assumed when there is no scheme. Only
    http(s) is allowed — a file:// or data: page would let a model read local
    files through the browser."""
    target = (raw or "").strip()
    if not target:
        return "", "Error: empty url."
    bare = _BARE_SCHEME.match(target)  # data:, javascript:, about: ... carry no "://"
    if bare:
        return "", f"Error: only http:// and https:// pages can be opened (got {bare.group(1).lower()}:)."
    if "://" not in target:
        target = "http://" + target  # also makes "localhost:5173" work
    scheme = urlparse(target).scheme.lower()
    if scheme not in ("http", "https"):
        return "", f"Error: only http:// and https:// pages can be opened (got {scheme}://)."
    return target, ""


def _console_summary(key: str, limit: int = 5) -> str:
    entry = _sessions.get(key)
    if not entry:
        return ""
    errors = [c for c in entry["console"] if c.startswith(("[error]", "[exception]"))]
    if not errors:
        return ""
    lines = [f"Console: {len(errors)} error(s) since the last navigation:"]
    lines.extend(f"  - {c[:300]}" for c in errors[-limit:])
    return "\n".join(lines)


def _clip(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [output truncated at {limit} characters]"


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


def browser_navigate(url: str) -> str:
    """Open a URL in the shared headless-Chromium session (first step of any browser test).

    Starts the browser on the first call; browser_snapshot, browser_inspect, browser_click, browser_type, browser_select_option, browser_set_viewport and browser_take_screenshot then act on this same page. Only http(s) URLs are allowed. Returns the final URL, HTTP status, page title and any console errors, all as text.

    Args:
        url: Page to load. ``http://`` is assumed when there is no scheme, so ``localhost:5173`` works.

    Returns:
        A short result with status and title, or an ``Error:`` message.
    """
    target, error = _normalize_url(url)
    if error:
        return error
    key = _session_key()

    def _do() -> str:
        page = _open_page(key)
        _sessions[key]["console"].clear()
        try:
            response = page.goto(target, wait_until="load", timeout=20000)
        except Exception as exc:  # noqa: BLE001
            return f"Error navigating to {target}: {exc}"
        status = response.status if response is not None else "?"
        try:
            title = page.title()
        except Exception:  # noqa: BLE001
            title = ""
        out = f"Navigated to {page.url} — HTTP {status} — title: {title or '(none)'}"
        problems = _console_summary(key)
        return out + ("\n" + problems if problems else "")

    return _run(_do)


_SNAPSHOT_JS = r"""
({ rootSel, limit }) => {
  const root = rootSel ? document.querySelector(rootSel) : document.body;
  if (!root) return { error: "No element matches " + rootSel };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const cssPath = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    const t = el.tagName.toLowerCase();
    for (const a of ["data-testid", "name", "aria-label"]) {
      const v = el.getAttribute(a);
      if (v) {
        const c = t + "[" + a + '="' + v.replace(/"/g, '\\"') + '"]';
        if (document.querySelectorAll(c).length === 1) return c;
      }
    }
    const parts = [];
    let n = el;
    while (n && n !== document.body && n.nodeType === 1) {
      let p = n.tagName.toLowerCase();
      if (n.id) { parts.unshift("#" + CSS.escape(n.id)); break; }
      const par = n.parentElement;
      if (par) {
        const same = Array.from(par.children).filter((c) => c.tagName === n.tagName);
        if (same.length > 1) p += ":nth-of-type(" + (same.indexOf(n) + 1) + ")";
      }
      parts.unshift(p);
      n = n.parentElement;
    }
    return parts.join(" > ");
  };
  const clean = (s, n) => {
    s = (s || "").replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  };
  const labelOf = (el) => {
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label");
    if (el.id) {
      const l = document.querySelector('label[for="' + el.id.replace(/"/g, '\\"') + '"]');
      if (l) return l.textContent;
    }
    const wrap = el.closest("label");
    return wrap ? wrap.textContent : "";
  };
  const sel = "h1,h2,h3,h4,h5,h6,a[href],button,input,select,textarea,summary," +
    "[role=button],[role=link],[role=tab],[role=checkbox],[role=switch],[role=alert],[role=dialog]," +
    "img[alt],p,li,label,th,td,figcaption,[aria-live]";
  const all = Array.from(root.querySelectorAll(sel)).filter(visible);
  const items = [];
  for (const el of all) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role");
    let kind = tag, text = "", extra = "";
    if (/^h[1-6]$/.test(tag)) { text = clean(el.textContent, 100); }
    else if (tag === "a") { kind = "link"; text = clean(el.textContent || el.getAttribute("aria-label"), 80); extra = 'href="' + clean(el.getAttribute("href"), 80) + '"'; }
    else if (tag === "button" || role === "button" || tag === "summary") { kind = "button"; text = clean(el.textContent || el.getAttribute("aria-label"), 80); if (el.disabled) extra = "disabled"; }
    else if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      kind = "input:" + type;
      const bits = [];
      if (el.name) bits.push('name="' + el.name + '"');
      if (el.placeholder) bits.push('placeholder="' + clean(el.placeholder, 40) + '"');
      if (type === "checkbox" || type === "radio") bits.push(el.checked ? "checked" : "unchecked");
      else if (type !== "password" && el.value) bits.push('value="' + clean(el.value, 40) + '"');
      if (el.required) bits.push("required");
      if (el.disabled) bits.push("disabled");
      text = clean(labelOf(el), 60); extra = bits.join(" ");
    }
    else if (tag === "select") {
      kind = "select";
      const opts = Array.from(el.options).slice(0, 12).map((o) => o.value + (o.selected ? "*" : ""));
      text = clean(labelOf(el), 60); extra = 'name="' + (el.name || "") + '" options=[' + opts.join(", ") + "]";
    }
    else if (tag === "textarea") { kind = "textarea"; text = clean(labelOf(el), 60); extra = el.placeholder ? 'placeholder="' + clean(el.placeholder, 40) + '"' : ""; }
    else if (tag === "img") { kind = "img"; text = clean(el.getAttribute("alt"), 80); }
    else if (role === "alert" || role === "dialog" || el.hasAttribute("aria-live")) { kind = role || "live-region"; text = clean(el.textContent, 120); }
    else if (["p", "li", "label", "th", "td", "figcaption"].includes(tag)) {
      // Plain text: skip wrappers of interactive controls (already listed) and empties.
      if (el.querySelector("a[href],button,input,select,textarea")) continue;
      text = clean(el.textContent, 120); if (!text) continue;
    }
    else { text = clean(el.textContent, 80); }
    items.push({ kind, text, extra, selector: cssPath(el) });
  }
  const doc = document.documentElement;
  return {
    title: document.title,
    url: location.href,
    viewport: [window.innerWidth, window.innerHeight],
    scroll: [doc.scrollWidth, doc.scrollHeight],
    overflowX: doc.scrollWidth > window.innerWidth + 1,
    total: items.length,
    items: items.slice(0, limit),
  };
}
"""


def browser_snapshot(selector: str = "") -> str:
    """Read the current page as text: headings, links, buttons, form fields, images and visible copy, each with a CSS selector you can pass to browser_click, browser_type or browser_inspect.

    This is how you "see" the page, because you are never given an image. Also reports viewport size, page height and whether the page scrolls sideways (a layout bug). Call it after every navigation or click to see what changed. Requires an open session, so call browser_navigate first.

    Args:
        selector: Optional CSS selector to limit the snapshot to one part of the page. Defaults to the whole page.

    Returns:
        A text outline of the page, or an ``Error:`` message.
    """
    key = _session_key()
    root = (selector or "").strip()

    def _do() -> str:
        page = _existing_page(key)
        if page is None:
            return _NO_SESSION
        try:
            data = page.evaluate(_SNAPSHOT_JS, {"rootSel": root, "limit": _MAX_SNAPSHOT_ITEMS})
        except Exception as exc:  # noqa: BLE001
            return f"Error reading the page: {exc}"
        if data.get("error"):
            return f"Error: {data['error']}"
        vw, vh = data["viewport"]
        sw, sh = data["scroll"]
        lines = [
            f"Page: {data['title'] or '(no title)'} — {data['url']}",
            f"Viewport {vw}x{vh}; page {sw}x{sh}; "
            + (
                "HORIZONTAL OVERFLOW (page scrolls sideways)"
                if data["overflowX"]
                else "no horizontal overflow"
            ),
        ]
        problems = _console_summary(key)
        if problems:
            lines.append(problems)
        shown = len(data["items"])
        lines.append(
            f"Elements ({shown}" + (f" of {data['total']}" if data["total"] > shown else "") + "):"
        )
        for item in data["items"]:
            parts = [f"[{item['kind']}]"]
            if item["text"]:
                parts.append(f"\"{item['text']}\"")
            if item["extra"]:
                parts.append(item["extra"])
            parts.append(f"  → {item['selector']}")
            lines.append(" ".join(parts))
        if data["total"] > shown:
            lines.append("… more elements omitted — pass a narrower `selector` to see the rest.")
        return _clip("\n".join(lines))

    return _run(_do)


_INSPECT_JS = r"""
(sel) => {
  const els = Array.from(document.querySelectorAll(sel));
  if (!els.length) return { error: "No element matches " + sel };
  const el = els[0];
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const parse = (c) => {
    const m = /^rgba?\(([^)]+)\)$/.exec((c || "").trim());
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    if (p.length < 3 || p.slice(0, 3).some(Number.isNaN)) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, bottom) => {
    const a = top.a;
    return { r: top.r * a + bottom.r * (1 - a), g: top.g * a + bottom.g * (1 - a), b: top.b * a + bottom.b * (1 - a), a: 1 };
  };
  let bg = { r: 255, g: 255, b: 255, a: 1 };
  const layers = [];
  for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
    const c = parse(getComputedStyle(n).backgroundColor);
    if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
  }
  for (const c of layers.reverse()) bg = over(c, bg);
  const fg = parse(cs.color);
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  let contrast = null;
  if (fg) {
    const f = fg.a < 1 ? over(fg, bg) : fg;
    const l1 = lum(f), l2 = lum(bg);
    contrast = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400;
  const large = size >= 24 || (size >= 18.66 && weight >= 700);
  const rgb = (c) => "rgb(" + Math.round(c.r) + ", " + Math.round(c.g) + ", " + Math.round(c.b) + ")";
  const props = ["display", "position", "z-index", "overflow", "opacity", "font-family", "font-size", "font-weight",
    "line-height", "letter-spacing", "text-align", "color", "background-color", "background-image", "margin", "padding",
    "border", "border-radius", "box-shadow", "backdrop-filter", "gap", "flex-direction", "justify-content", "align-items",
    "grid-template-columns"];
  const dull = new Set(["none", "normal", "auto", "0px", "visible", "static", "1", "rgba(0, 0, 0, 0)",
    "0px none rgb(0, 0, 0)", "start", "stretch", "row", "flex-start"]);
  const styles = [];
  for (const p of props) {
    const v = (cs.getPropertyValue(p) || "").trim();
    if (v && !dull.has(v)) styles.push(p + ": " + v.slice(0, 120));
  }
  return {
    matches: els.length,
    tag: el.tagName.toLowerCase(),
    text: (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 100),
    box: [Math.round(r.x + scrollX), Math.round(r.y + scrollY), Math.round(r.width), Math.round(r.height)],
    inViewport: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth,
    clipped: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1,
    fg: fg ? rgb(fg) : null,
    bg: rgb(bg),
    bgLayers: layers.length,
    contrast: contrast === null ? null : Math.round(contrast * 100) / 100,
    large,
    styles,
  };
}
"""


def browser_inspect(selector: str) -> str:
    """Report how one element is laid out and styled: position and size, computed typography, colours, spacing, borders, shadows, and the text/background contrast ratio with a WCAG AA verdict.

    Use it to check a design or a bug you cannot see: unreadable text, cramped padding, an element cut off by its container, a wrong colour or font. Requires an open session, so call browser_navigate first.

    Args:
        selector: CSS selector of the element to inspect (the first match is used). Take one from browser_snapshot.

    Returns:
        A text report, or an ``Error:`` message.
    """
    sel = (selector or "").strip()
    if not sel:
        return "Error: empty selector."
    key = _session_key()

    def _do() -> str:
        page = _existing_page(key)
        if page is None:
            return _NO_SESSION
        try:
            d = page.evaluate(_INSPECT_JS, sel)
        except Exception as exc:  # noqa: BLE001
            return f"Error inspecting {sel!r}: {exc}"
        if d.get("error"):
            return f"Error: {d['error']}"
        x, y, w, h = d["box"]
        lines = [
            f"{sel} → <{d['tag']}>"
            + (f" (first of {d['matches']} matches)" if d["matches"] > 1 else "")
            + (f' "{d["text"]}"' if d["text"] else ""),
            f"Box: {w}x{h} at x={x}, y={y} (page coordinates); "
            + (
                "in the current viewport"
                if d["inViewport"]
                else "outside the current viewport (scrolled away)"
            ),
        ]
        if d["clipped"]:
            lines.append("Content is larger than the box (overflowing or scrollable).")
        if d["contrast"] is not None:
            need = 3.0 if d["large"] else 4.5
            verdict = "PASS" if d["contrast"] >= need else "FAIL"
            note = "" if d["bgLayers"] else " (no solid background found; assumed white)"
            lines.append(
                f"Contrast: {d['contrast']}:1 text {d['fg']} on {d['bg']} — "
                f"WCAG AA needs {need}:1 for {'large' if d['large'] else 'normal'} text → {verdict}{note}"
            )
        else:
            lines.append("Contrast: could not be computed (colour is not plain rgb()).")
        lines.append("Computed style:")
        lines.extend(f"  {s}" for s in d["styles"])
        return "\n".join(lines)

    return _run(_do)


def browser_set_viewport(width: int, height: int = 800) -> str:
    """Resize the browser window to check a responsive layout, then report whether the page now scrolls sideways.

    Try 375 (phone), 768 (tablet) and 1280 (desktop), then call browser_snapshot to see how the page reflowed. Requires an open session, so call browser_navigate first.

    Args:
        width: Viewport width in CSS pixels (200-3840).
        height: Viewport height in CSS pixels (200-2160). Defaults to 800.

    Returns:
        The new viewport and an overflow verdict, or an ``Error:`` message.
    """
    try:
        w, h = int(width), int(height or 800)
    except (TypeError, ValueError):
        return "Error: width and height must be whole numbers."
    if not (200 <= w <= 3840 and 200 <= h <= 2160):
        return "Error: width must be 200-3840 and height 200-2160."
    key = _session_key()

    def _do() -> str:
        page = _existing_page(key)
        if page is None:
            return _NO_SESSION
        try:
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(150)  # let media queries and resize handlers settle
            size = page.evaluate(
                "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
            )
        except Exception as exc:  # noqa: BLE001
            return f"Error resizing the viewport: {exc}"
        if size["sw"] > size["iw"] + 1:
            verdict = (
                f"HORIZONTAL OVERFLOW: the page is {size['sw']}px wide in a {size['iw']}px viewport."
            )
        else:
            verdict = "No horizontal overflow."
        return f"Viewport is now {w}x{h}. {verdict}"

    return _run(_do)


def browser_click(selector: str) -> str:
    """Click the first element matching a CSS selector on the current page.

    Requires an open session, so call browser_navigate first. Call browser_snapshot afterwards to see what changed.

    Args:
        selector: A CSS selector, e.g. ``"#submit-button"`` or ``"button[type=submit]"``.

    Returns:
        Confirmation (with the page's URL after the click, since a click often navigates), or an ``Error:`` message.
    """
    sel = (selector or "").strip()
    if not sel:
        return "Error: empty selector."
    key = _session_key()

    def _do() -> str:
        page = _existing_page(key)
        if page is None:
            return _NO_SESSION
        try:
            page.click(sel, timeout=10000)
        except Exception as exc:  # noqa: BLE001
            return f"Error clicking {sel!r}: {exc}"
        out = f"Clicked {sel!r} — page is now at {page.url}"
        problems = _console_summary(key, limit=3)
        return out + ("\n" + problems if problems else "")

    return _run(_do)


def browser_type(selector: str, text: str) -> str:
    """Fill a text input or textarea matching a CSS selector.

    Replaces the field's value rather than appending keystrokes, which suits a form's name, email and date fields filled one at a time. Requires an open session, so call browser_navigate first.

    Args:
        selector: A CSS selector for the input/textarea, e.g. ``"input[name=email]"``.
        text: The text to put into it.

    Returns:
        Confirmation, or an ``Error:`` message.
    """
    sel = (selector or "").strip()
    if not sel:
        return "Error: empty selector."
    key = _session_key()

    def _do() -> str:
        page = _existing_page(key)
        if page is None:
            return _NO_SESSION
        try:
            page.fill(sel, text or "", timeout=10000)
        except Exception as exc:  # noqa: BLE001
            return f"Error typing into {sel!r}: {exc}"
        return f"Typed {text!r} into {sel!r}."

    return _run(_do)


def browser_select_option(selector: str, value: str) -> str:
    """Select an option by its ``value`` attribute in a ``<select>``.

    Requires an open session, so call browser_navigate first. browser_snapshot lists each select's option values.

    Args:
        selector: A CSS selector for the ``<select>`` element.
        value: The ``value`` attribute of the ``<option>`` to select (not its visible label).

    Returns:
        Confirmation, or an ``Error:`` message.
    """
    sel = (selector or "").strip()
    if not sel:
        return "Error: empty selector."
    key = _session_key()

    def _do() -> str:
        page = _existing_page(key)
        if page is None:
            return _NO_SESSION
        try:
            page.select_option(sel, value or "", timeout=10000)
        except Exception as exc:  # noqa: BLE001
            return f"Error selecting {value!r} on {sel!r}: {exc}"
        return f"Selected {value!r} on {sel!r}."

    return _run(_do)


def _evidence_path(name: str):
    """Where a bare screenshot name goes: CatCrewAI's own data folder, per project -- never the
    client's project folder, where QA screenshots used to pile up next to composer.json."""
    from ..config import BASE_DIR
    from ..platform.runtime import current_project_id

    return BASE_DIR / "data" / "screenshots" / (current_project_id() or "general") / name


def browser_take_screenshot(filename: str = "") -> str:
    """Save a full-page PNG of the current page for the user or a bug report.

    You cannot view the image; it is evidence for the person reading your report. To find out what the page looks like, use browser_snapshot and browser_inspect. Requires an open session, so call browser_navigate first.

    Args:
        filename: Optional output filename (``.png`` appended if missing). A bare name is saved in CatCrewAI's own screenshots folder (not the project); a path with folders is saved there.

    Returns:
        The saved screenshot path, or an ``Error:`` message.
    """
    from .. import tools as _tools

    name = (filename or "").strip() or f"e2e_{int(time.time())}.png"
    if not name.lower().endswith(".png"):
        name += ".png"
    try:
        out_path = _evidence_path(name) if not any(sep in name for sep in "/\\") else _tools.resolve_user_path(name)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"
    key = _session_key()

    def _do() -> str:
        page = _existing_page(key)
        if page is None:
            return _NO_SESSION
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out_path), full_page=True)
        except Exception as exc:  # noqa: BLE001
            return f"Error capturing screenshot: {exc}"
        return (
            f"Screenshot saved to {out_path} (an image for the user; you cannot view it. "
            "Use browser_snapshot / browser_inspect to check the page, or browser_look "
            "if a Vision model is set)."
        )

    return _run(_do)


_NO_VISION = (
    "No Vision model is set, so the page cannot be looked at. Use browser_snapshot and "
    "browser_inspect: they give the text, positions, styles and contrast as plain text. "
    "(The user can pick a Vision model on the Models page.)"
)
_VISION_OFF = (
    "Vision is turned off on the Models page, so the page cannot be looked at. Use "
    "browser_snapshot and browser_inspect: they give the text, positions, styles and contrast "
    "as plain text."
)
_LOOK_PROMPT = (
    "You are the eyes of a QA / UI reviewer who cannot see this web page. Describe the "
    "screenshot factually: the layout, the visible text, colours, alignment, spacing, and "
    "anything that looks broken (overlap, clipping, overflow, unreadable text, empty areas, "
    "error messages). Be concise and concrete."
)


def _capture_jpeg(key: str, selector: str, full_page: bool) -> bytes | str:
    """The page (or one element) as JPEG bytes, or an ``Error:`` string. Browser thread."""
    page = _existing_page(key)
    if page is None:
        return _NO_SESSION
    try:
        if selector:
            return page.locator(selector).first.screenshot(type="jpeg", quality=70, timeout=15000)
        return page.screenshot(type="jpeg", quality=70, full_page=full_page)
    except Exception as exc:  # noqa: BLE001
        return f"Error capturing the page: {exc}"


def browser_look(question: str = "", selector: str = "") -> str:
    """Have the Vision model look at the current page and describe it in words.

    Use it only when the text tools are not enough (a chart, an image, a visual glitch); browser_snapshot and browser_inspect are faster and exact. It works only when the user has set a Vision model on the Models page, and it answers in plain text, so it is safe with any chat model. Requires an open session, so call browser_navigate first.

    Args:
        question: What to check, e.g. "is the login button visible and readable?". Empty asks for a general description.
        selector: Optional CSS selector to look at one element instead of the visible viewport.

    Returns:
        The Vision model's description, or an ``Error:`` / guidance message.
    """
    try:
        from ..core.provider_factory import provider_if_explicit
        from ..platform.runtime import current_project_id
        from ..services.model_resolver import OFF, get_effective_model

        if get_effective_model(current_project_id(), "vision").host == OFF:
            return _VISION_OFF
        provider = provider_if_explicit("vision", current_project_id())
    except Exception as exc:  # noqa: BLE001
        return f"Error: the Vision model could not be set up: {exc}"
    if provider is None:
        return _NO_VISION
    key = _session_key()
    shot = _run(lambda: _capture_jpeg(key, (selector or "").strip(), full_page=False))
    if isinstance(shot, str):
        return shot
    import base64

    text = _LOOK_PROMPT + (f"\n\nQuestion: {question.strip()}" if (question or "").strip() else "")
    data_url = "data:image/jpeg;base64," + base64.b64encode(shot).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    try:
        reply = provider.chat(messages)
    except Exception as exc:  # noqa: BLE001 - provider errors are already user-safe sentences
        return f"Error: the Vision model could not describe the page: {exc}"
    answer = (getattr(reply, "content", "") or "").strip()
    if not answer:
        return "Error: the Vision model returned no description. Use browser_snapshot instead."
    return _clip(answer, 4000)


def browser_close() -> str:
    """Close your browser page and free its memory. Call it when a test or design review is finished.

    Returns:
        Confirmation.
    """
    key = _session_key()

    def _do() -> str:
        entry = _sessions.pop(key, None)
        if entry is None:
            return "No browser session was open."
        try:
            entry["context"].close()
        except Exception:  # noqa: BLE001
            pass
        return "Browser session closed."

    return _run(_do)
