"""replace_in_file chrome-title guards."""

from __future__ import annotations

from pathlib import Path

from jinx_ai.tools import replace_in_file


def test_replace_blocks_title_edit_on_widgets(tmp_path, monkeypatch) -> None:
    widgets = tmp_path / "widgets.py"
    widgets.write_text("class X: pass\n", encoding="utf-8")
    monkeypatch.setattr(
        "jinx_ai.tools.resolve_user_path",
        lambda _p: widgets,
    )
    out = replace_in_file(
        "jinx_ai/widgets.py",
        "title='Jinx AI - The Best AI Assistant'",
        "title='Jinx AI - Fully Autonomous AI Assistant'",
    )
    assert out.startswith("Error: wrong file")
    assert "titlebar.py" in out
    assert "Exact locations" in out or "config.py" in out
    assert widgets.read_text(encoding="utf-8") == "class X: pass\n"


def test_replace_not_found_demands_search_files(tmp_path, monkeypatch) -> None:
    target = tmp_path / "titlebar.py"
    target.write_text('QLabel("Other")\n', encoding="utf-8")
    monkeypatch.setattr("jinx_ai.tools.resolve_user_path", lambda _p: target)
    out = replace_in_file(
        "jinx_ai/titlebar.py",
        'QLabel("Jinx AI - The Best AI Assistant")',
        'QLabel("Jinx AI - Fully Autonomous AI Assistant")',
    )
    assert "old_string was not found" in out
    assert "search_files" in out
    assert "Do NOT retry" in out
