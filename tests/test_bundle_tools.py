"""Blaze's SEO audit and Lex's personal-data inventory."""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from backend.tool_functions import lex_tools
from backend.tool_functions.blaze_tools import seo_page_audit

PAGE = """<!doctype html><html lang="en"><head>
<title>Acme Dental Sofia - a modern dental clinic in the centre</title>
<meta name="description" content="Short.">
<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">
<script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script></head>
<body><h1>Smile again</h1><h2>Services</h2><img src="a.png"><img src="b.png" alt="team">
<form method="post"><input type="email" name="email"><input name="phone"></form>
<script>document.cookie = "seen=1"</script></body></html>"""


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **kw)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.RequestHandlerClass.log_message = lambda *a, **kw: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_seo_page_audit_reports_what_a_search_result_shows_and_what_to_fix(site) -> None:
    out = seo_page_audit(site + "/index.html")
    assert "title: Acme Dental Sofia" in out and "ok]" in out
    assert "meta description: Short. [6 chars, too short]" in out
    assert "H1 (1): Smile again" in out
    assert "images: 2, without alt text: 1" in out
    problems = out.rsplit("problems: ", 1)[1]
    for fix in ("canonical", "og:title", "alt", "/robots.txt", "/sitemap.xml", "description should be"):
        assert fix in problems, fix
    assert "exactly one H1" not in problems


def test_seo_page_audit_says_when_there_is_nothing_to_audit(site) -> None:
    assert seo_page_audit(site + "/missing").startswith("Error: GET")
    assert seo_page_audit("").startswith("Error")


def test_data_collection_scan_lists_fields_third_parties_cookies_and_packages(tmp_path, monkeypatch) -> None:
    views = tmp_path / "resources" / "views"
    views.mkdir(parents=True)
    (views / "contact.blade.php").write_text(PAGE, encoding="utf-8")
    (tmp_path / "composer.json").write_text('{"require": {"laravel/framework": "^12", "stripe/stripe-php": "^15"}}')
    (tmp_path / ".env.example").write_text("APP_KEY=\nMAIL_MAILER=smtp\nSTRIPE_KEY=\n")
    (tmp_path / ".env").write_text("STRIPE_SECRET=sk_live_never_read\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("fbq('init')")  # dependencies are not the site
    from backend import tools

    monkeypatch.setattr(tools, "resolve_user_path", lambda p: Path(tmp_path))
    out = lex_tools.data_collection_scan(".")
    assert "- email: resources/views/contact.blade.php:7" in out
    assert "- phone:" in out
    assert "Google Analytics / Tag Manager" in out and "Google Fonts" in out
    assert "Meta (Facebook) Pixel" not in out
    assert "Cookies / browser storage:\n  - set by the code" in out
    assert "stripe/stripe-php (composer.json)" in out
    assert "MAIL_MAILER, STRIPE_KEY" in out
    assert "sk_live" not in out and "STRIPE_SECRET" not in out
