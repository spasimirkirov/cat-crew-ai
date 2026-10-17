"""Workspace file tree (one or more folders). Double-click opens in the editor."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .context_menu import add_item, popup, reveal_in_explorer
from .db_backend import SQLITE_EXTS
from .fa import icon_button
from .icon import create_activity_icon
from .tools import open_in_vscode
from .widgets import PageHeader
from .workspace import current_folders

_SKIP = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".idea", ".vs", ".jinx", ".jinx-ai",
}


def explorer_action_labels(path: Path | None, *, is_root: bool) -> list[str]:
    """Titles shown in the explorer context menu (for tests and docs)."""
    if path is None:
        return ["Add folder to workspace", "Refresh"]
    labels: list[str] = []
    if path.is_file():
        labels.extend(["Open", "Open in VS Code"])
        if path.suffix.lower() in SQLITE_EXTS:
            labels.append("Open in Database")
    else:
        labels.append("Open in VS Code")
        if not is_root:
            labels.append("Add this folder to workspace")
        if is_root:
            labels.append("Remove from workspace")
    labels.extend(["Reveal in File Explorer", "Copy path", "Copy name"])
    return labels


class FileExplorerPanel(QWidget):
    workspace_open_requested = pyqtSignal()
    add_folder_requested = pyqtSignal()
    add_this_folder_requested = pyqtSignal(str)
    sqlite_requested = pyqtSignal(str)
    file_open_requested = pyqtSignal(str)
    remove_folder_requested = pyqtSignal(str)
    terminal_toggle_requested = pyqtSignal()
    status = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)

        heading = PageHeader("Workspace", compact=True)

        self.root_label = QLabel()
        self.root_label.setObjectName("muted")
        self.root_label.setWordWrap(True)

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(4)
        tools.addWidget(icon_button("fa5s.sync", "Refresh file tree", self.refresh))
        tools.addWidget(
            icon_button(
                "fa5s.folder-plus",
                "Add folder to workspace",
                self.add_folder_requested.emit,
            )
        )
        tools.addWidget(
            icon_button(
                "fa5s.folder-open",
                "Open selected folder in File Explorer",
                self._open_selected_or_workspace,
            )
        )
        tools.addWidget(
            icon_button(
                "fa5s.trash-alt",
                "Remove selected folder from workspace",
                self._remove_selected_root,
                danger=True,
            )
        )
        tools.addWidget(
            icon_button(
                "fa5s.terminal",
                "Toggle terminal (Ctrl+`)",
                self.terminal_toggle_requested.emit,
            )
        )
        tools.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(heading)
        layout.addLayout(tools)
        layout.addWidget(self.root_label)
        layout.addWidget(self.tree, stretch=1)
        self.set_folders(current_folders())

    def set_root(self, path: Path | str) -> None:
        self.set_folders([Path(path)])

    def set_folders(self, folders: list[Path] | None = None) -> None:
        if folders is None:
            folders = current_folders()
        if folders:
            self.root_label.setText("\n".join(str(f) for f in folders))
        else:
            self.root_label.setText("No folders yet. Add a folder to this session.")
        self.tree.clear()
        for folder in folders:
            item = QTreeWidgetItem([folder.name or str(folder)])
            item.setData(0, Qt.ItemDataRole.UserRole, str(folder))
            item.setToolTip(0, str(folder))
            item.setIcon(0, create_activity_icon("files", 16, "#e8e8ed"))
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
            )
            self.tree.addTopLevelItem(item)
        if self.tree.topLevelItemCount() == 1:
            first = self.tree.topLevelItem(0)
            first.setExpanded(True)
            self._populate(first)

    def refresh(self) -> None:
        self.set_folders(current_folders())
        self.status.emit("File tree refreshed.")

    def _path_of(self, item: QTreeWidgetItem | None) -> Path | None:
        if item is None:
            return None
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        return Path(raw) if raw else None

    def _is_root(self, item: QTreeWidgetItem | None) -> bool:
        return item is not None and item.parent() is None

    def _open_selected_or_workspace(self) -> None:
        path = self._path_of(self.tree.currentItem())
        if path is None:
            self.workspace_open_requested.emit()
            return
        reveal_in_explorer(path if path.is_dir() else path.parent)

    def _remove_selected_root(self) -> None:
        item = self.tree.currentItem()
        while item is not None and item.parent() is not None:
            item = item.parent()
        path = self._path_of(item)
        if path is None or not self._is_root(item):
            self.status.emit("Select a workspace root, then remove it.")
            return
        self.remove_folder_requested.emit(str(path))

    def _populate(self, item: QTreeWidgetItem) -> None:
        path = self._path_of(item)
        if path is None or not path.is_dir():
            return
        item.takeChildren()
        try:
            children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            self.status.emit(str(exc))
            return
        for child in children:
            if child.name in _SKIP:
                continue
            node = QTreeWidgetItem([child.name])
            node.setData(0, Qt.ItemDataRole.UserRole, str(child))
            node.setToolTip(0, str(child))
            if child.is_dir():
                node.setIcon(0, create_activity_icon("files", 16, "#e8e8ed"))
                node.setChildIndicatorPolicy(
                    QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
                )
            else:
                node.setIcon(0, create_activity_icon("file", 16, "#c7c7cc"))
            item.addChild(node)

    def _on_expand(self, item: QTreeWidgetItem) -> None:
        if item.childCount() == 0:
            self._populate(item)

    def _on_double(self, item: QTreeWidgetItem, _column: int) -> None:
        path = self._path_of(item)
        if path is None:
            return
        if path.is_dir():
            item.setExpanded(not item.isExpanded())
            return
        if path.suffix.lower() in SQLITE_EXTS:
            self.sqlite_requested.emit(str(path))
            return
        self.file_open_requested.emit(str(path))

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is not None:
            self.tree.setCurrentItem(item)
        path = self._path_of(item)
        is_root = self._is_root(item)
        global_pos = self.tree.viewport().mapToGlobal(pos)

        def build(menu: QMenu) -> None:
            if path is None:
                add_item(
                    menu,
                    "Add folder to workspace",
                    self.add_folder_requested.emit,
                    icon="folder-plus",
                )
                add_item(menu, "Refresh", self.refresh, icon="sync")
                return
            if path.is_file():
                add_item(
                    menu,
                    "Open",
                    lambda: self.file_open_requested.emit(str(path)),
                    icon="pen",
                )
                add_item(menu, "Open in VS Code", lambda: self._vscode(path), icon="code")
                if path.suffix.lower() in SQLITE_EXTS:
                    add_item(
                        menu,
                        "Open in Database",
                        lambda: self.sqlite_requested.emit(str(path)),
                        icon="db",
                    )
            else:
                add_item(menu, "Open in VS Code", lambda: self._vscode(path), icon="code")
                if not is_root:
                    add_item(
                        menu,
                        "Add this folder to workspace",
                        lambda: self.add_this_folder_requested.emit(str(path)),
                        icon="folder-plus",
                    )
                if is_root:
                    add_item(
                        menu,
                        "Remove from workspace",
                        lambda: self.remove_folder_requested.emit(str(path)),
                        icon="trash",
                    )
            menu.addSeparator()
            add_item(
                menu,
                "Reveal in File Explorer",
                lambda: reveal_in_explorer(path),
                icon="folder-open",
            )
            add_item(menu, "Copy path", lambda: self._copy(str(path)), icon="copy")
            add_item(menu, "Copy name", lambda: self._copy(path.name), icon="copy")

        popup(self, global_pos, build)

    def _vscode(self, path: Path) -> None:
        self.status.emit(open_in_vscode(str(path)))

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.status.emit("Copied.")
