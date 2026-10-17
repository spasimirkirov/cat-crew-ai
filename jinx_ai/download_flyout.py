"""Title-bar download popup: current pull, queue, recent, history."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .downloads import RETRY_STATUSES, display_name, format_when
from .fa import icon_button
from .widgets import chip_button


class DownloadFlyout(QFrame):
    history_requested = pyqtSignal()
    folder_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    retry_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("downloadFlyout")
        self.setFixedWidth(320)
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 8, 10, 8)
        col.setSpacing(5)
        title = QLabel("Downloads")
        title.setObjectName("hostTitle")
        col.addWidget(title)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._current = QLabel("Nothing downloading.")
        self._current.setObjectName("modelName")
        self._current.setWordWrap(True)
        row.addWidget(self._current, stretch=1)
        self._cancel = icon_button(
            "close",
            "Cancel download",
            self.cancel_requested.emit,
            danger=True,
            size=24,
        )
        self._cancel.setVisible(False)
        row.addWidget(self._cancel, alignment=Qt.AlignmentFlag.AlignTop)
        self._folder = icon_button(
            "fa5s.folder-open",
            "Open containing folder",
            self.folder_requested.emit,
            size=24,
        )
        row.addWidget(self._folder, alignment=Qt.AlignmentFlag.AlignTop)
        col.addLayout(row)

        progress = QHBoxLayout()
        progress.setContentsMargins(0, 0, 0, 0)
        progress.setSpacing(6)
        self._bar = QProgressBar()
        self._bar.setObjectName("pullBar")
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 100)
        self._bar.setFixedHeight(6)
        progress.addWidget(self._bar, stretch=1)
        self._pct = QLabel("")
        self._pct.setObjectName("muted")
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pct.setFixedWidth(34)
        progress.addWidget(self._pct)
        self._progress = QWidget()
        self._progress.setLayout(progress)
        col.addWidget(self._progress)

        self._meta = QLabel()
        self._meta.setObjectName("muted")
        self._meta.setWordWrap(True)
        self._meta.setVisible(False)
        col.addWidget(self._meta)

        self._queue = QLabel()
        self._queue.setObjectName("muted")
        self._queue.setWordWrap(True)
        col.addWidget(self._queue)

        recent_h = QLabel("Recent")
        recent_h.setObjectName("sectionLabel")
        col.addWidget(recent_h)
        self._recent_host = QWidget()
        self._recent_l = QVBoxLayout(self._recent_host)
        self._recent_l.setContentsMargins(0, 0, 0, 0)
        self._recent_l.setSpacing(3)
        col.addWidget(self._recent_host)
        col.addWidget(
            chip_button(
                "See download history",
                self.history_requested.emit,
                primary=True,
                tip="Open AI Models → Downloads",
            )
        )
        self._recent_key: tuple = ()
        self.setMinimumHeight(0)

    def set_snapshot(self, snap: dict[str, Any]) -> None:
        current = str(snap.get("current") or "").strip()
        queued = [str(item) for item in (snap.get("queued") or []) if item]
        active = bool(snap.get("active") or current or queued)
        self._progress.setVisible(active)
        self._cancel.setVisible(bool(current))
        if current:
            self._current.setText(display_name(current))
            text = str(snap.get("text") or "").strip()
            if snap.get("indeterminate"):
                self._bar.setRange(0, 0)
                self._pct.setText("…")
            else:
                pct = int(snap.get("percent") or 0)
                self._bar.setRange(0, 100)
                self._bar.setValue(pct)
                self._pct.setText(f"{pct}%")
            extra = text if text and "%" not in text else ""
            self._meta.setText(extra)
            self._meta.setVisible(bool(extra))
        else:
            self._current.setText("Nothing downloading.")
            self._bar.setRange(0, 100)
            self._bar.setValue(0)
            self._pct.setText("")
            self._meta.setText("")
            self._meta.setVisible(False)
        if queued:
            self._queue.setText(
                f"{len(queued)} waiting: " + ", ".join(display_name(item) for item in queued[:6])
            )
            self._queue.setVisible(True)
        else:
            self._queue.setVisible(False)
        recent = list(snap.get("recent") or [])
        key = tuple(
            (str(row.get("name") or ""), str(row.get("status") or ""), str(row.get("finished_at") or ""))
            for row in recent[:5]
        )
        if key == self._recent_key and self._recent_l.count():
            return
        self._recent_key = key
        while self._recent_l.count():
            item = self._recent_l.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        if not recent:
            empty = QLabel("No finished downloads yet.")
            empty.setObjectName("muted")
            self._recent_l.addWidget(empty)
            return
        for row in recent[:5]:
            name = display_name(str(row.get("name") or ""))
            when = format_when(str(row.get("finished_at") or ""))
            status = str(row.get("status") or "done")
            err = str(row.get("error") or "").strip()
            bits = [part for part in (when, status, err) if part]
            line = QLabel(name)
            line.setObjectName("modelName")
            line.setWordWrap(True)
            meta = QLabel(" · ".join(bits))
            meta.setObjectName("muted")
            meta.setWordWrap(True)
            wrap = QWidget()
            box = QHBoxLayout(wrap)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(4)
            text = QVBoxLayout()
            text.setContentsMargins(0, 0, 0, 0)
            text.setSpacing(0)
            text.addWidget(line)
            text.addWidget(meta)
            box.addLayout(text, stretch=1)
            if status in RETRY_STATUSES:
                spec = str(row.get("name") or "")
                box.addWidget(
                    icon_button(
                        "redo",
                        "Retry download",
                        lambda n=spec: self.retry_requested.emit(n),
                        size=24,
                    )
                )
            self._recent_l.addWidget(wrap)

    def place_below(self, anchor: QWidget) -> None:
        """Hang under the title-bar icon, flush right, on the window's screen."""
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        layout = self.layout()
        if layout is not None:
            layout.activate()
        hint = self.sizeHint()
        height = max(hint.height(), 1)
        br = anchor.mapToGlobal(anchor.rect().bottomRight())
        x = br.x() - 320
        y = br.y() + 4
        screen = None
        win = anchor.window()
        if win is not None:
            screen = win.screen()
        if screen is None:
            screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center()))
        geo = screen.availableGeometry() if screen is not None else None
        if geo is not None:
            margin = 8
            x = min(max(x, geo.left() + margin), geo.right() - 320 - margin)
            if y + height > geo.bottom() - margin:
                top = anchor.mapToGlobal(QPoint(0, 0))
                y = top.y() - height - 4
            y = min(max(y, geo.top() + margin), max(geo.top() + margin, geo.bottom() - height - margin))
        self.resize(320, height)
        self.move(x, y)
