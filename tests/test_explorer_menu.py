from pathlib import Path

from jinx_ai.file_explorer import explorer_action_labels


def test_empty_tree_offers_add_folder() -> None:
    labels = explorer_action_labels(None, is_root=False)
    assert labels[0] == "Add folder to workspace"
    assert "Refresh" in labels


def test_workspace_root_can_be_removed(tmp_path: Path) -> None:
    labels = explorer_action_labels(tmp_path, is_root=True)
    assert "Remove from workspace" in labels
    assert "Add this folder to workspace" not in labels
    assert "Reveal in File Explorer" in labels


def test_nested_folder_can_be_added(tmp_path: Path) -> None:
    nested = tmp_path / "src"
    nested.mkdir()
    labels = explorer_action_labels(nested, is_root=False)
    assert "Add this folder to workspace" in labels
    assert "Remove from workspace" not in labels


def test_file_menu_has_open(tmp_path: Path) -> None:
    path = tmp_path / "main.py"
    path.write_text("print(1)\n", encoding="utf-8")
    labels = explorer_action_labels(path, is_root=False)
    assert labels[:2] == ["Open", "Open in VS Code"]
    assert "Copy path" in labels


def test_sqlite_file_offers_database(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    path.write_bytes(b"")
    labels = explorer_action_labels(path, is_root=False)
    assert "Open in Database" in labels


def test_icon_buttons_have_tooltips() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    from jinx_ai.fa import icon_button

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    btn = icon_button("fa5s.folder-plus", "Add folder to workspace", lambda: None)
    assert btn.toolTip() == "Add folder to workspace"
    assert btn.accessibleName() == "Add folder to workspace"


def test_explorer_toolbar_has_terminal_toggle() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication, QPushButton

    from jinx_ai.file_explorer import FileExplorerPanel

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    panel = FileExplorerPanel()
    tips = [btn.toolTip() for btn in panel.findChildren(QPushButton)]
    assert any("terminal" in (tip or "").lower() for tip in tips)
    hits: list[int] = []
    panel.terminal_toggle_requested.connect(lambda: hits.append(1))
    panel.terminal_toggle_requested.emit()
    assert hits == [1]
