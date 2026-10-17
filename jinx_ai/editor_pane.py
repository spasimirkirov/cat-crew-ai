"""Center editor: open files in tabs, Ctrl+S saves."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .context_menu import add_item, popup, reveal_in_explorer
from .icon import create_activity_icon
from .tools import open_in_vscode

_TEXT_EXTS = {
    ".py", ".pyw", ".txt", ".md", ".json", ".toml", ".yml", ".yaml", ".ini", ".cfg",
    ".css", ".html", ".htm", ".js", ".ts", ".tsx", ".jsx", ".cs", ".cpp", ".h", ".hpp",
    ".c", ".rs", ".go", ".java", ".kt", ".xml", ".sql", ".env", ".gitignore", ".csv",
    ".ps1", ".bat", ".cmd", ".sh", ".qml", ".qss", ".log",
}
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".exe", ".dll", ".pyd",
    ".so", ".zip", ".7z", ".rar", ".pdf", ".vrm", ".vrma", ".woff", ".woff2", ".ttf",
}


class _FileTab(QPlainTextEdit):
    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.path = path
        self._saved = ""
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self.setFont(font)
        self.setTabStopDistance(32)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def is_dirty(self) -> bool:
        return self.toPlainText() != self._saved

    def load(self) -> None:
        text = self.path.read_text(encoding="utf-8", errors="replace")
        self._saved = text
        self.setPlainText(text)

    def save(self) -> None:
        text = self.toPlainText()
        self.path.write_text(text, encoding="utf-8")
        self._saved = text


class EditorPane(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(14, 14))
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        bar = self.tabs.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._tab_menu)

        empty = QWidget()
        empty.setObjectName("page")
        empty_l = QVBoxLayout(empty)
        hint = QLabel("Select a file in the explorer to open it here.\nCtrl+S saves the active tab.")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        empty_l.addStretch(1)
        empty_l.addWidget(hint)
        empty_l.addStretch(1)
        self.empty = empty

        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.tabs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

    def _looks_binary(self, file_path: Path) -> bool:
        try:
            chunk = file_path.read_bytes()[:4096]
        except OSError:
            return False
        return b"\x00" in chunk

    def open_file(self, path: str | Path) -> None:
        file_path = Path(path).resolve()
        if not file_path.is_file():
            self.status.emit(f"Not a file: {file_path}")
            return
        suffix = file_path.suffix.lower()
        if suffix in _BINARY_EXTS or (
            suffix and suffix not in _TEXT_EXTS and self._looks_binary(file_path)
        ):
            self.status.emit(f"Cannot edit this file type here: {file_path.name}")
            return
        try:
            if file_path.stat().st_size > 2_000_000:
                self.status.emit(f"File is too large to edit here: {file_path.name}")
                return
        except OSError as exc:
            QMessageBox.warning(self, "Open file", str(exc))
            return
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if isinstance(widget, _FileTab) and widget.path == file_path:
                self.tabs.setCurrentIndex(index)
                self.stack.setCurrentWidget(self.tabs)
                return
        try:
            tab = _FileTab(file_path)
            tab.load()
        except OSError as exc:
            QMessageBox.warning(self, "Open file", str(exc))
            return
        tab.textChanged.connect(lambda t=tab: self._refresh_tab_title(t))
        index = self.tabs.addTab(tab, file_path.name)
        self.tabs.setTabIcon(index, create_activity_icon("file", 14, "#e8e8ed"))
        self.tabs.setTabToolTip(index, str(file_path))
        self.tabs.setCurrentWidget(tab)
        self.stack.setCurrentWidget(self.tabs)
        self.status.emit(str(file_path))

    def save_tab(self, tab: _FileTab) -> bool:
        try:
            tab.save()
        except OSError as exc:
            QMessageBox.warning(self, "Save", str(exc))
            return False
        self._refresh_tab_title(tab)
        self.status.emit(f"Saved {tab.path.name}")
        return True

    def save_current(self) -> bool:
        tab = self.tabs.currentWidget()
        if not isinstance(tab, _FileTab):
            return False
        return self.save_tab(tab)

    def _refresh_tab_title(self, tab: _FileTab) -> None:
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        mark = "● " if tab.is_dirty() else ""
        self.tabs.setTabText(index, f"{mark}{tab.path.name}")

    def _close_tab(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if isinstance(tab, _FileTab) and tab.is_dirty():
            choice = QMessageBox.question(
                self,
                "Unsaved changes",
                f"Save {tab.path.name} before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Save and not self.save_tab(tab):
                return
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.stack.setCurrentWidget(self.empty)

    def _on_tab_changed(self, _index: int) -> None:
        tab = self.tabs.currentWidget()
        if isinstance(tab, _FileTab):
            self.status.emit(str(tab.path))

    def _tab_menu(self, pos) -> None:
        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        tab = self.tabs.widget(index)
        if not isinstance(tab, _FileTab):
            return

        def build(menu: QMenu) -> None:
            add_item(menu, "Close", lambda: self._close_tab(index), icon="close")
            add_item(menu, "Close others", lambda: self._close_others(index))
            add_item(menu, "Close all", self._close_all)
            menu.addSeparator()
            add_item(
                menu,
                "Reveal in File Explorer",
                lambda: reveal_in_explorer(tab.path),
                icon="folder-open",
            )
            add_item(menu, "Copy path", lambda: self._copy(str(tab.path)), icon="copy")
            add_item(
                menu,
                "Open in VS Code",
                lambda: self.status.emit(open_in_vscode(str(tab.path))),
                icon="code",
            )

        popup(self, bar.mapToGlobal(pos), build)

    def _close_others(self, keep: int) -> None:
        for index in range(self.tabs.count() - 1, -1, -1):
            if index != keep:
                self._close_tab(index)

    def _close_all(self) -> None:
        for index in range(self.tabs.count() - 1, -1, -1):
            self._close_tab(index)

    def _copy(self, text: str) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        self.status.emit("Copied.")
