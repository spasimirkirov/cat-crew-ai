"""replace_in_file window-title guards (the title lives in tauri.conf.json + index.html)."""

from __future__ import annotations

from backend.tools import replace_in_file


def test_replace_blocks_title_edit_on_shell(tmp_path, monkeypatch) -> None:
    shell = tmp_path / "Shell.tsx"
    shell.write_text("export const Shell = () => null;\n", encoding="utf-8")
    monkeypatch.setattr("backend.tools.BASE_DIR", tmp_path)  # the file is CatCrewAI's own source
    monkeypatch.setattr("backend.tools.resolve_user_path", lambda _p: shell)
    out = replace_in_file(
        "frontend/src/components/Shell.tsx",
        "<title>CatCrewAI - The Best AI Assistant</title>",
        "<title>CatCrewAI - Fully Autonomous AI Assistant</title>",
    )
    assert out.startswith("Error: wrong file")
    assert "tauri.conf.json" in out and "index.html" in out
    assert shell.read_text(encoding="utf-8") == "export const Shell = () => null;\n"


def test_replace_not_found_demands_search_files(tmp_path, monkeypatch) -> None:
    target = tmp_path / "index.html"
    target.write_text("<title>Other</title>\n", encoding="utf-8")
    monkeypatch.setattr("backend.tools.resolve_user_path", lambda _p: target)
    out = replace_in_file(
        "frontend/index.html",
        "<title>CatCrewAI - The Best AI Assistant</title>",
        "<title>CatCrewAI - Fully Autonomous AI Assistant</title>",
    )
    assert "old_string was not found" in out
    assert "search_files" in out
    assert "Do NOT retry" in out


def test_replace_title_in_the_right_file_works(tmp_path, monkeypatch) -> None:
    target = tmp_path / "index.html"
    target.write_text("<title>CatCrewAI</title>\n", encoding="utf-8")
    monkeypatch.setattr("backend.tools.resolve_user_path", lambda _p: target)
    monkeypatch.setattr("backend.services.pending_restart.note_catcrew_source_edit", lambda _p: None)
    out = replace_in_file("frontend/index.html", "<title>CatCrewAI</title>", "<title>CatCrew</title>")
    assert out.startswith("Replaced 1 snippet")
    assert "<title>CatCrew</title>" in target.read_text(encoding="utf-8")


def test_a_users_own_app_can_change_its_title(tmp_path, monkeypatch) -> None:
    """The guard is for CatCrewAI's own window: a project's App.tsx with a <title> is just a file."""
    app = tmp_path / "App.tsx"
    app.write_text("<Helmet><title>Shop</title></Helmet>\n", encoding="utf-8")
    monkeypatch.setattr("backend.tools.resolve_user_path", lambda _p: app)
    out = replace_in_file("src/App.tsx", "<title>Shop</title>", "<title>My Shop</title>")
    assert out.startswith("Replaced 1 snippet")
    assert "<title>My Shop</title>" in app.read_text(encoding="utf-8")
