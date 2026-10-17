"""Aegis/Pixel interactive browser tools: real headless Chromium against a local page."""

from __future__ import annotations

import http.server
import threading

import pytest

pytest.importorskip("playwright")

from backend import tools as tools_mod
from backend.platform import runtime
from backend.tool_functions import qa_tools as qa

PAGES = {
    "/": """<!doctype html><title>Booking</title>
      <body style="margin:0;font-family:sans-serif">
      <h1 id="hero">Book a car</h1>
      <p style="color:#777;background:#888;font-size:14px" id="faint">Faint hint text</p>
      <p style="color:#111;background:#fff;font-size:14px" id="crisp">Clear body text</p>
      <form id="f" onsubmit="event.preventDefault();document.getElementById('out').textContent='Booked for '+document.getElementById('email').value">
        <label for="email">Email</label><input id="email" name="email" type="email" placeholder="you@example.com" required>
        <input name="pw" type="password" value="hunter2">
        <select name="size"><option value="s">Small</option><option value="l">Large</option></select>
        <button type="submit" id="go">Reserve</button>
      </form>
      <div id="out" role="alert"></div></body>""",
    "/wide": """<!doctype html><title>Wide</title><body style="margin:0"><div style="width:900px;height:40px;background:#ccc">wide</div></body>""",
    "/broken": """<!doctype html><title>Broken</title><body><h1>Hi</h1><script>throw new Error('boom')</script></body>""",
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib name
        body = PAGES.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def _browser_ready():
    err = tools_mod._ensure_playwright()
    if err:
        pytest.skip(f"headless Chromium unavailable: {err[:120]}")
    yield
    qa.close_all_sessions()


def _in_thread(fn, *args):
    box: dict[str, object] = {}

    def run():
        box["value"] = fn(*args)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return box["value"]


def test_session_survives_calls_from_different_threads(site):
    # Agent turns run on different threads; Playwright's sync API is thread-bound,
    # so this only works because every call is funnelled to one browser thread.
    assert "HTTP 200" in _in_thread(qa.browser_navigate, site)
    assert "Clicked" in _in_thread(qa.browser_click, "#go")
    assert "Booking" in _in_thread(qa.browser_snapshot)


def test_snapshot_is_text_with_selectors_and_hides_passwords(site):
    qa.browser_navigate(site)
    out = qa.browser_snapshot()
    assert isinstance(out, str)
    assert '[h1] "Book a car"' in out
    assert "→ #hero" in out
    assert '[input:email] "Email"' in out and 'name="email"' in out
    assert "[select]" in out and "options=[s*, l]" in out
    assert '[button] "Reserve"' in out
    assert "hunter2" not in out  # password values never reach the model
    assert "no horizontal overflow" in out


def test_snapshot_scoped_and_missing_selector(site):
    qa.browser_navigate(site)
    scoped = qa.browser_snapshot("#f")
    assert "Book a car" not in scoped and "Reserve" in scoped
    assert qa.browser_snapshot("#nope").startswith("Error: No element matches")


def test_fill_submit_and_read_result_as_text(site):
    qa.browser_navigate(site)
    assert "Typed" in qa.browser_type("#email", "a@b.co")
    assert "Selected" in qa.browser_select_option("select[name=size]", "l")
    assert "Clicked" in qa.browser_click("#go")
    assert "Booked for a@b.co" in qa.browser_snapshot()


def test_inspect_reports_contrast_pass_and_fail(site):
    qa.browser_navigate(site)
    bad = qa.browser_inspect("#faint")
    good = qa.browser_inspect("#crisp")
    assert "→ FAIL" in bad
    assert "→ PASS" in good
    assert "font-size: 14px" in good and "Box:" in good


def test_viewport_reports_horizontal_overflow(site):
    qa.browser_navigate(f"{site}/wide")
    narrow = qa.browser_set_viewport(375, 700)
    assert "HORIZONTAL OVERFLOW" in narrow and "375" in narrow
    assert "No horizontal overflow" in qa.browser_set_viewport(1280, 800)
    assert qa.browser_set_viewport(50, 50).startswith("Error:")


def test_console_errors_are_reported_as_text(site):
    out = qa.browser_navigate(f"{site}/broken")
    assert "1 error(s)" in out and "boom" in out
    assert "Console:" in qa.browser_snapshot()


def test_calls_without_a_session_explain_what_to_do():
    for out in (
        qa.browser_snapshot(),
        qa.browser_click("#x"),
        qa.browser_type("#x", "y"),
        qa.browser_inspect("#x"),
        qa.browser_set_viewport(800, 600),
    ):
        assert out.startswith("Error: no active browser session")


def test_only_http_pages_can_be_opened():
    assert qa.browser_navigate("file:///etc/passwd").startswith("Error: only http")
    assert qa.browser_navigate("data:text/html,<h1>x</h1>").startswith("Error: only http")
    assert qa.browser_navigate("  ").startswith("Error: empty url")
    assert qa._normalize_url("localhost:5173") == ("http://localhost:5173", "")


def test_screenshot_is_saved_and_says_the_model_cannot_view_it(site, tmp_path, monkeypatch):
    monkeypatch.setattr(tools_mod, "resolve_user_path", lambda raw: tmp_path / raw)
    qa.browser_navigate(site)
    out = qa.browser_take_screenshot("sub/shot")  # a path with folders: where it was asked
    assert (tmp_path / "sub" / "shot.png").stat().st_size > 500
    assert "cannot view" in out and isinstance(out, str)


def test_a_bare_screenshot_name_stays_out_of_the_project(site, tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "_evidence_path", lambda name: tmp_path / "evidence" / name)
    monkeypatch.setattr(tools_mod, "resolve_user_path", lambda raw: (_ for _ in ()).throw(AssertionError("not the project")))
    qa.browser_navigate(site)
    qa.browser_take_screenshot("home")
    assert (tmp_path / "evidence" / "home.png").stat().st_size > 500


def test_each_cat_gets_its_own_page(site):
    tok = runtime.set_bot_id("aegis")
    try:
        qa.browser_navigate(site)
    finally:
        runtime.reset_bot_id(tok)
    tok = runtime.set_bot_id("pixel")
    try:
        assert qa.browser_snapshot().startswith("Error: no active browser session")
        qa.browser_navigate(f"{site}/wide")
        assert "Wide" in qa.browser_snapshot()
    finally:
        runtime.reset_bot_id(tok)
    tok = runtime.set_bot_id("aegis")
    try:
        assert "Booking" in qa.browser_snapshot()  # untouched by pixel's navigation
        assert qa.browser_close() == "Browser session closed."
        assert qa.browser_close() == "No browser session was open."
    finally:
        runtime.reset_bot_id(tok)


def test_tool_schemas_are_compact_and_text_returning():
    from backend.platform.tool_registry import function_to_schema

    for fn in (
        qa.browser_navigate, qa.browser_snapshot, qa.browser_inspect, qa.browser_set_viewport,
        qa.browser_click, qa.browser_type, qa.browser_select_option, qa.browser_take_screenshot,
        qa.browser_close,
    ):
        schema = function_to_schema(fn)["function"]
        assert schema["description"] and len(schema["description"]) < 900, fn.__name__
        assert fn.__annotations__["return"] in (str, "str")


def test_playwright_failure_is_remembered_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(tools_mod, "_PLAYWRIGHT_READY", None)
    monkeypatch.setattr(tools_mod, "_PLAYWRIGHT_FAILED", None)
    monkeypatch.setattr(tools_mod, "_chromium_installed", lambda: False)

    class Done:
        returncode = 1
        stderr = "offline"
        stdout = ""

    monkeypatch.setattr(tools_mod.subprocess, "run", lambda *a, **k: calls.append(a) or Done())
    first = tools_mod._ensure_playwright()
    second = tools_mod._ensure_playwright()
    assert first and "playwright install chromium" in first
    assert second == first and len(calls) == 1


class _FakeVision:
    """Stands in for the Vision-role model: records what it was sent, answers in text."""

    def __init__(self, answer="A booking form with a blue Reserve button."):
        self.answer = answer
        self.sent: list = []

    def chat(self, messages, tools=None):
        self.sent.append(messages)

        class _Reply:
            content = self.answer

        return _Reply()


def test_look_without_a_vision_model_points_to_the_text_tools(site, monkeypatch):
    monkeypatch.setattr("backend.core.provider_factory.provider_if_explicit", lambda role, *_a, **_k: None)
    qa.browser_navigate(site)
    out = qa.browser_look("is it readable?")
    assert "No Vision model is set" in out and "browser_snapshot" in out


def test_look_sends_the_screenshot_to_the_vision_model_and_returns_text(site, monkeypatch):
    vision = _FakeVision()
    asked: list[str] = []

    def pick(role, *_a, **_k):
        asked.append(role)
        return vision

    monkeypatch.setattr("backend.core.provider_factory.provider_if_explicit", pick)
    qa.browser_navigate(site)
    out = qa.browser_look("is the Reserve button visible?")
    assert out == "A booking form with a blue Reserve button."
    assert asked == ["vision"]
    (messages,) = vision.sent
    parts = messages[0]["content"]
    assert "is the Reserve button visible?" in parts[0]["text"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert len(parts[1]["image_url"]["url"]) > 500


def test_look_can_target_one_element(site, monkeypatch):
    vision = _FakeVision()
    monkeypatch.setattr("backend.core.provider_factory.provider_if_explicit", lambda role, *_a, **_k: vision)
    qa.browser_navigate(site)
    whole = qa.browser_look()
    full_len = len(vision.sent[0][0]["content"][1]["image_url"]["url"])
    assert whole
    qa.browser_look(selector="#hero")
    hero_len = len(vision.sent[1][0]["content"][1]["image_url"]["url"])
    assert hero_len < full_len
    assert qa.browser_look(selector="#does-not-exist").startswith("Error capturing the page")


def test_look_needs_an_open_page(monkeypatch):
    monkeypatch.setattr("backend.core.provider_factory.provider_if_explicit", lambda role, *_a, **_k: _FakeVision())
    assert qa.browser_look().startswith("Error: no active browser session")


def test_look_reports_a_vision_model_failure_as_text(site, monkeypatch):
    class _Down:
        def chat(self, messages, tools=None):
            raise RuntimeError("LM Studio is offline")

    monkeypatch.setattr("backend.core.provider_factory.provider_if_explicit", lambda role, *_a, **_k: _Down())
    qa.browser_navigate(site)
    out = qa.browser_look()
    assert out.startswith("Error: the Vision model could not describe the page")
    assert "LM Studio is offline" in out
