"""Aegis (qa) role-specific tools."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request


def browser_mcp_screenshot(url: str, path: str = "") -> str:
    """Load a URL in headless Chromium and save a full-page PNG screenshot.

    Distinct from the global ``browser_check`` tool (which inspects console
    errors/network failures but saves nothing) - this is for when QA needs
    actual visual evidence of what a page rendered, e.g. to attach to a bug
    report.

    Args:
        url: Page to load. ``http://`` is assumed if no scheme is given.
        path: Optional output path for the PNG. Defaults to a timestamped
            file in the workspace.

    Returns:
        The saved screenshot path, or an ``Error:`` message.
    """
    from .. import tools as _tools

    target = (url or "").strip()
    if not target:
        return "Error: empty url."
    if "://" not in target:
        target = "http://" + target
    err = _tools._ensure_playwright()
    if err:
        return f"Error: headless browser is not available.\n{err}"
    if path:
        out_path = _tools.resolve_user_path(path)
    else:
        out_path = _tools.current_workspace() / f"screenshot_{int(time.time())}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                nav_error = ""
                try:
                    page.goto(target, wait_until="load", timeout=20000)
                except Exception as exc:
                    nav_error = str(exc)
                page.screenshot(path=str(out_path), full_page=True)
            finally:
                browser.close()
    except Exception as exc:
        return f"Error capturing screenshot of {target}: {exc}"
    if nav_error:
        return f"Screenshot saved to {out_path} (note: navigation warning: {nav_error})"
    return f"Screenshot saved to {out_path}"


def http_endpoint_audit(url: str, method: str = "GET", timeout: float = 10.0) -> str:
    """Hit an HTTP(S) endpoint directly (no browser) and report status code,
    response time, and key headers.

    For API/endpoint health, not page rendering - use ``browser_check`` or
    ``browser_mcp_screenshot`` for how a page actually renders in a browser.

    Args:
        url: Endpoint to call. ``http://`` is assumed if no scheme is given.
        method: HTTP method (GET, POST, HEAD, ...). Defaults to GET.
        timeout: Seconds to wait before giving up.

    Returns:
        A short audit report, or an ``Error:`` message.
    """
    target = (url or "").strip()
    if not target:
        return "Error: empty url."
    if "://" not in target:
        target = "http://" + target
    req = urllib.request.Request(target, method=(method or "GET").upper())
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or 10.0)) as resp:
            elapsed = (time.monotonic() - start) * 1000
            status = resp.status
            headers = dict(resp.headers.items())
            body_len = len(resp.read(65536))
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - start) * 1000
        status = exc.code
        headers = dict(exc.headers.items()) if exc.headers else {}
        body_len = 0
    except Exception as exc:
        return f"Error: could not reach {target}: {exc}"
    lines = [
        f"{method.upper()} {target}",
        f"status: {status}",
        f"response time: {elapsed:.0f}ms",
        f"content-type: {headers.get('Content-Type', headers.get('content-type', '(none)'))}",
        f"content-length header: {headers.get('Content-Length', headers.get('content-length', '(none)'))}",
        f"body bytes read (capped 64KB): {body_len}",
    ]
    server = headers.get("Server", headers.get("server", ""))
    if server:
        lines.append(f"server: {server}")
    verdict = "PASS" if 200 <= status < 400 else "FAIL"
    lines.append(f"verdict: {verdict}")
    return "\n".join(lines)


_TEST_RESULT_RE = re.compile(
    r"(\d+)\s+(passed|failed|error|errors|skipped)", re.IGNORECASE
)


def run_test_suite(command: str = "", cwd: str = "") -> str:
    """Run the project's test suite and summarize pass/fail counts.

    If ``command`` is omitted, auto-detects a runner from files present in
    the workspace (pytest for a Python project, ``npm test`` for a Node
    project). Delegates the actual run to the sandboxed ``run_command`` in
    ``backend/tools.py``, then extracts a pass/fail summary from the output
    so QA does not have to re-read the full log to know the verdict.

    Args:
        command: Test command to run. Auto-detected if omitted.
        cwd: Optional working directory, relative to the workspace.

    Returns:
        A pass/fail summary followed by the raw test output.
    """
    from .. import tools as _tools

    workdir = _tools.resolve_user_path(cwd) if cwd else _tools.current_workspace()
    cmd = (command or "").strip()
    if not cmd:
        if (workdir / "pytest.ini").exists() or (workdir / "pyproject.toml").exists() or list(workdir.glob("test_*.py")) or list(workdir.glob("**/test_*.py")):
            cmd = "python -m pytest -q"
        elif (workdir / "package.json").exists():
            cmd = "npm test"
        else:
            return (
                "Error: no test command given and no recognizable test setup "
                "found (no pytest.ini/pyproject.toml/test_*.py, no package.json). "
                "Pass an explicit command."
            )
    output = _tools.run_command(cmd, cwd or None)
    counts: dict[str, int] = {}
    for match in _TEST_RESULT_RE.finditer(output or ""):
        num, label = int(match.group(1)), match.group(2).lower()
        key = "failed" if label in ("failed", "error", "errors") else label
        counts[key] = counts.get(key, 0) + num
    if counts:
        summary = ", ".join(f"{v} {k}" for k, v in counts.items())
        verdict = "PASS" if counts.get("failed", 0) == 0 else "FAIL"
        header = f"`{cmd}` -> {verdict} ({summary})"
    else:
        header = f"`{cmd}` ran (could not parse a pass/fail summary from the output - read it below)."
    return f"{header}\n\n--- output ---\n{output}"
