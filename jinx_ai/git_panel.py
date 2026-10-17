"""Source Control: staged / changes lists with +/- like VS Code."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import git_ops
from .context_menu import add_item, popup, reveal_in_explorer
from .fa import icon_button
from .icon import create_activity_icon
from .workspace import current_workspace


class _StatusWorker(QThread):
    done = pyqtSignal(object)

    def run(self) -> None:
        self.done.emit(git_ops.parse_status())


class _DiffWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, path: str, staged: bool) -> None:
        super().__init__()
        self.path = path
        self.staged = staged

    def run(self) -> None:
        if self.staged:
            text = git_ops.diff_text(self.path, staged=True)
            self.done.emit(text if text and text != "(no output)" else "(staged — no textual diff)")
            return
        work = git_ops.diff_text(self.path, staged=False)
        if work and work != "(no output)":
            self.done.emit(work)
            return
        self.done.emit("(no diff — untracked or binary)")


class GitFileRow(QFrame):
    picked = pyqtSignal(str, bool)
    stage = pyqtSignal(str)
    unstage = pyqtSignal(str)
    open_file = pyqtSignal(str)

    def __init__(self, path: str, letter: str, *, staged: bool, parent=None) -> None:
        super().__init__(parent)
        self.path = path
        self.staged = staged
        self.setObjectName("gitFileRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)

        name = Path(path).name or path
        folder = str(Path(path).parent).replace("\\", "/")
        if folder in {".", ""}:
            folder = ""

        name_l = QLabel(name)
        name_l.setObjectName("gitFileName")
        name_l.setProperty("letter", letter)
        path_l = QLabel(folder)
        path_l.setObjectName("muted")
        path_l.setToolTip(path)
        letter_l = QLabel(letter)
        letter_l.setObjectName(f"gitLetter{letter}")
        letter_l.setFixedWidth(16)
        letter_l.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 4, 3)
        row.setSpacing(8)
        row.addWidget(name_l, 0)
        row.addWidget(path_l, 1)
        row.addWidget(letter_l, 0)
        if staged:
            btn = icon_button("fa5s.minus", f"Unstage {name}", lambda: self.unstage.emit(path), size=24)
        else:
            btn = icon_button("fa5s.plus", f"Stage {name}", lambda: self.stage.emit(path), size=24)
        btn.setObjectName("gitRowBtn")
        row.addWidget(btn, 0)
        self.setToolTip(path)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            walk = child
            while walk is not None and walk is not self:
                if isinstance(walk, QPushButton):
                    super().mousePressEvent(event)
                    return
                walk = walk.parent()
            self.picked.emit(self.path, self.staged)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_file.emit(self.path)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class GitSection(QFrame):
    bulk = pyqtSignal()

    def __init__(self, title: str, bulk_tip: str, bulk_icon: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("gitSection")
        self._expanded = True
        self._title = title

        self.head = QFrame()
        self.head.setObjectName("gitSectionHead")
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head.installEventFilter(self)
        head = QHBoxLayout(self.head)
        head.setContentsMargins(4, 2, 4, 2)
        head.setSpacing(6)
        self.chevron = QPushButton()
        self.chevron.setObjectName("gitChevron")
        self.chevron.setFixedSize(22, 22)
        self.chevron.setIconSize(QSize(12, 12))
        self.chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chevron.setToolTip("Collapse or expand this list")
        self.chevron.clicked.connect(self.toggle)
        self.label = QLabel(title)
        self.label.setObjectName("gitSectionTitle")
        bulk = icon_button(bulk_icon, bulk_tip, self.bulk.emit, size=24)
        bulk.setObjectName("gitRowBtn")
        head.addWidget(self.chevron, 0)
        head.addWidget(self.label, 1)
        head.addWidget(bulk, 0)
        self._paint_chevron()

        self.body = QWidget()
        self.body_l = QVBoxLayout(self.body)
        self.body_l.setContentsMargins(0, 0, 0, 0)
        self.body_l.setSpacing(0)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.head)
        col.addWidget(self.body)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.head and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            child = self.head.childAt(event.position().toPoint())
            walk = child
            while walk is not None and walk is not self.head:
                if isinstance(walk, QPushButton):
                    return False
                walk = walk.parent()
            self.toggle()
            return True
        return super().eventFilter(obj, event)

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        self._paint_chevron()

    def _paint_chevron(self) -> None:
        kind = "chevron-down" if self._expanded else "chevron-right"
        self.chevron.setIcon(create_activity_icon(kind, 12, "#8e8e93"))

    def set_rows(self, rows: list[GitFileRow]) -> None:
        while self.body_l.count():
            item = self.body_l.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for row in rows:
            self.body_l.addWidget(row)
        n = len(rows)
        self.label.setText(f"{self._title}  {n}" if n else self._title)


class GitPanel(QWidget):
    status = pyqtSignal(str)
    generate_commit_requested = pyqtSignal(str)
    file_open_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._selected: tuple[str, bool] | None = None
        self._loading = False

        self.branch_label = QLabel("Source Control")
        self.branch_label.setObjectName("switchTitle")
        self.root_label = QLabel()
        self.root_label.setObjectName("muted")
        self.root_label.setWordWrap(True)

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(4)
        tools.addWidget(icon_button("fa5s.sync", "Refresh", self.refresh, size=28))
        tools.addWidget(icon_button("fa5s.code-branch", "git pull", self._pull, size=28))
        tools.addStretch(1)
        tools.addWidget(icon_button("fa5s.folder-plus", "git init in this folder", self._init, size=28))

        self.staged_section = GitSection("Staged Changes", "Unstage all", "fa5s.minus")
        self.changes_section = GitSection("Changes", "Stage all", "fa5s.plus")
        self.staged_section.bulk.connect(self._unstage_all)
        self.changes_section.bulk.connect(self._stage_all)

        lists = QWidget()
        lists_l = QVBoxLayout(lists)
        lists_l.setContentsMargins(0, 0, 0, 0)
        lists_l.setSpacing(8)
        lists_l.addWidget(self.staged_section)
        lists_l.addWidget(self.changes_section)
        lists_l.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(lists)

        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setPlaceholderText("Select a file to see the diff.")
        self.diff.setObjectName("gitDiff")

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(scroll)
        split.addWidget(self.diff)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        self.commit_input = QPlainTextEdit()
        self.commit_input.setPlaceholderText("Message (Ctrl+Enter to commit)")
        self.commit_input.setMaximumHeight(72)
        commit_row = QHBoxLayout()
        commit_row.setSpacing(6)
        gen = QPushButton("AI message")
        gen.setObjectName("secondary")
        gen.setToolTip("Ask Jinx for a commit message")
        gen.clicked.connect(self._ask_ai)
        commit_btn = QPushButton("Commit")
        commit_btn.setToolTip("Create a git commit from staged files")
        commit_btn.clicked.connect(self._commit)
        push_btn = QPushButton("Push")
        push_btn.setObjectName("secondary")
        push_btn.setToolTip("git push")
        push_btn.clicked.connect(self._push)
        commit_row.addWidget(gen)
        commit_row.addStretch(1)
        commit_row.addWidget(push_btn)
        commit_row.addWidget(commit_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.branch_label)
        layout.addWidget(self.root_label)
        layout.addLayout(tools)
        layout.addWidget(self.commit_input)
        layout.addLayout(commit_row)
        layout.addWidget(split, stretch=1)

        self._status_worker: _StatusWorker | None = None
        self._diff_worker: _DiffWorker | None = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.refresh()

    def refresh(self) -> None:
        if self._status_worker is not None and self._status_worker.isRunning():
            return
        worker = _StatusWorker(self)
        self._status_worker = worker
        worker.done.connect(self._apply_status)
        worker.finished.connect(lambda: self._forget_status(worker))
        worker.start()

    def _forget_status(self, worker: _StatusWorker) -> None:
        if self._status_worker is worker:
            self._status_worker = None
        worker.deleteLater()

    def _apply_status(self, info: object) -> None:
        if not isinstance(info, dict):
            return
        if not info["ok"]:
            self.branch_label.setText("Not a git repository")
            self.root_label.setText(str(current_workspace()))
            self.staged_section.set_rows([])
            self.staged_section.setVisible(False)
            self.changes_section.set_rows([])
            self.diff.setPlainText(info["message"])
            return
        root = info["root"]
        self.branch_label.setText(info["branch"])
        self.root_label.setText(str(root))
        staged, changes = git_ops.split_status_files(info["files"])
        self.staged_section.set_rows([self._row(item, staged=True) for item in staged])
        self.staged_section.setVisible(bool(staged))
        self.changes_section.set_rows([self._row(item, staged=False) for item in changes])
        if not staged and not changes:
            self.diff.setPlainText("Working tree clean.")
        self.status.emit("Git status refreshed.")

    def _row(self, item: dict, *, staged: bool) -> GitFileRow:
        row = GitFileRow(item["path"], item.get("letter") or "M", staged=staged)
        row.picked.connect(self._pick)
        row.stage.connect(self._stage_one)
        row.unstage.connect(self._unstage_one)
        row.open_file.connect(self._open_rel)
        return row

    def _pick(self, path: str, staged: bool) -> None:
        self._selected = (path, staged)
        if self._diff_worker is not None and self._diff_worker.isRunning():
            return
        self.diff.setPlainText("Loading diff…")
        worker = _DiffWorker(path, staged)
        worker.setParent(self)
        self._diff_worker = worker
        worker.done.connect(self.diff.setPlainText)
        worker.finished.connect(lambda: self._forget_diff(worker))
        worker.start()

    def _forget_diff(self, worker: _DiffWorker) -> None:
        if self._diff_worker is worker:
            self._diff_worker = None
        worker.deleteLater()

    def _abs_path(self, rel: str) -> Path:
        root = git_ops.git_root() or current_workspace()
        return (root / rel).resolve()

    def _open_rel(self, rel: str) -> None:
        self.file_open_requested.emit(str(self._abs_path(rel)))

    def _menu(self, pos) -> None:
        child = self.childAt(pos)
        row = child
        while row is not None and not isinstance(row, GitFileRow):
            row = row.parent()
        if not isinstance(row, GitFileRow):
            return
        rel = row.path
        abs_path = self._abs_path(rel)

        def build(menu: QMenu) -> None:
            add_item(menu, "Open", lambda: self.file_open_requested.emit(str(abs_path)), icon="pen")
            add_item(menu, "Reveal in File Explorer", lambda: reveal_in_explorer(abs_path), icon="folder-open")
            add_item(menu, "Copy path", lambda: self._copy(str(abs_path)), icon="copy")
            menu.addSeparator()
            if row.staged:
                add_item(menu, "Unstage", lambda: self._unstage_one(rel), icon="minus")
            else:
                add_item(menu, "Stage", lambda: self._stage_one(rel), icon="plus")

        popup(self, self.mapToGlobal(pos), build)

    def _stage_one(self, path: str) -> None:
        self.status.emit(git_ops.stage_paths([path]))
        self.refresh()

    def _unstage_one(self, path: str) -> None:
        self.status.emit(git_ops.unstage_paths([path]))
        self.refresh()

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.status.emit("Copied.")

    def _stage_all(self) -> None:
        info = git_ops.parse_status()
        _, changes = git_ops.split_status_files(info.get("files") or [])
        paths = [item["path"] for item in changes]
        if not paths:
            self.status.emit("Nothing to stage.")
            return
        self.status.emit(git_ops.stage_paths(paths))
        self.refresh()

    def _unstage_all(self) -> None:
        info = git_ops.parse_status()
        staged, _changes = git_ops.split_status_files(info.get("files") or [])
        paths = [item["path"] for item in staged]
        if not paths:
            self.status.emit("Nothing to unstage.")
            return
        self.status.emit(git_ops.unstage_paths(paths))
        self.refresh()

    def _ask_ai(self) -> None:
        staged = git_ops.diff_text(staged=True)
        work = git_ops.diff_text(staged=False)
        blob = ""
        if staged and staged != "(no output)":
            blob += staged + "\n"
        if work and work != "(no output)":
            blob += work
        status = git_ops.status_text()
        payload = (blob or status)[:8000]
        if not payload.strip():
            self.status.emit("Nothing to summarize.")
            return
        self.status.emit("Asking Jinx for a commit message…")
        self.generate_commit_requested.emit(payload)

    def set_commit_message(self, text: str) -> None:
        self.commit_input.setPlainText((text or "").strip())

    def _commit(self) -> None:
        message = self.commit_input.toPlainText().strip()
        result = git_ops.commit(message)
        self.status.emit(result)
        if result.startswith("Error"):
            QMessageBox.warning(self, "Commit", result)
            return
        self.commit_input.clear()
        self.refresh()

    def _pull(self) -> None:
        self.status.emit(git_ops.pull())
        self.refresh()

    def _push(self) -> None:
        self.status.emit(git_ops.push())
        self.refresh()

    def _init(self) -> None:
        self.status.emit(git_ops.init_repo())
        self.refresh()
