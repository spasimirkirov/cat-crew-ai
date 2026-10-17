"""Truth checks for the Team Arcane landing experiment (scripts/arcane_site.py)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

# scripts/arcane_site.py (the Team Arcane landing experiment) no longer exists: skip instead of failing collection.
arcane_site = pytest.importorskip("arcane_site", reason="scripts/arcane_site.py was removed with the Team Arcane experiment")
computer_snapshot, site_ready = arcane_site.computer_snapshot, arcane_site.site_ready


def test_site_ready_rejects_react_bootstrap_only(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react-bootstrap": "2.0.0", "bootstrap": "5.3.0"}}',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "App.jsx").write_text("Jayce Heimerdinger Vi CatCrewAI Ekko Caitlyn Silco Mel Cassandra Draven", encoding="utf-8")
    assert site_ready(tmp_path) is False


def test_site_ready_needs_build_and_roster(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "18.0.0"}, "devDependencies": {"vite": "5.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "vite.config.js").write_text(
        'import react from "@vitejs/plugin-react"\nexport default { plugins: [react()] }',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    names = "Jayce Heimerdinger Vi CatCrewAI Ekko Caitlyn Silco Mel Cassandra Draven Blitzcrank"
    (src / "App.jsx").write_text(names + " <form>contact</form>", encoding="utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "index.php").write_text("<?php readfile('dist/index.html');", encoding="utf-8")
    (tmp_path / ".htaccess").write_text(
        "RewriteEngine On\nRewriteCond %{REQUEST_FILENAME} !-f\nRewriteRule . index.php [L]\n",
        encoding="utf-8",
    )
    assert site_ready(tmp_path) is True


def test_site_ready_needs_htaccess_with_php(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "18.0.0"}, "devDependencies": {"vite": "5.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "vite.config.js").write_text(
        'import react from "@vitejs/plugin-react"\nexport default { plugins: [react()] }',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    names = "Jayce Heimerdinger Vi CatCrewAI Ekko Caitlyn Silco Mel Cassandra Draven Blitzcrank"
    (src / "App.jsx").write_text(names + " <form>contact</form>", encoding="utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "index.php").write_text("<?php readfile('dist/index.html');", encoding="utf-8")
    assert site_ready(tmp_path) is False
    (tmp_path / ".htaccess").write_text("RewriteRule . index.php [L]\n", encoding="utf-8")
    assert site_ready(tmp_path) is True


def test_snapshot_says_not_shippable(tmp_path: Path) -> None:
    text = computer_snapshot(tmp_path)
    assert "SHIPPABLE: no" in text
    assert "Missing:" in text
