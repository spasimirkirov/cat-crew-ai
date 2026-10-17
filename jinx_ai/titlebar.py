"""Frameless window chrome: orb, drag, traffic lights on the right."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .icon import create_activity_icon, create_jinx_icon
from .widgets import TrafficLights


class TitleBar(QWidget):
    close_requested = pyqtSignal()
    minimize_requested = pyqtSignal()
    maximize_requested = pyqtSignal()
    downloads_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(48)

        self.lights = TrafficLights()
        self.lights.close_clicked.connect(self.close_requested.emit)
        self.lights.minimize_clicked.connect(self.minimize_requested.emit)
        self.lights.maximize_clicked.connect(self.maximize_requested.emit)

        brand = QLabel()
        brand.setPixmap(create_jinx_icon(18).pixmap(18, 18))
        brand.setFixedSize(18, 18)

        self.title = QLabel("Jinx AI - The Best AI Assistant")
        self.title.setObjectName("titleBrand")

        self.mode_label = QLabel("Assistant")
        self.mode_label.setObjectName("titleMode")

        self.dl_btn = QPushButton()
        self.dl_btn.setObjectName("iconBtn")
        self.dl_btn.setFixedSize(28, 28)
        self.dl_btn.setIconSize(QSize(14, 14))
        self.dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dl_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.dl_btn.setToolTip("Downloads")
        self.dl_btn.setIcon(create_activity_icon("download", 14, "#e8e8ed"))
        self.dl_btn.setVisible(False)
        self.dl_btn.clicked.connect(self.downloads_clicked.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 12, 0)
        layout.setSpacing(10)
        layout.addWidget(brand)
        layout.addWidget(self.title)
        layout.addWidget(self.mode_label)
        layout.addStretch(1)
        layout.addWidget(self.dl_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.lights, alignment=Qt.AlignmentFlag.AlignVCenter)

    def set_context(self, text: str) -> None:
        self.mode_label.setText(text or "Home")

    def set_downloads(self, *, visible: bool, active: bool = False) -> None:
        self.dl_btn.setVisible(bool(visible) or bool(active))
        self.dl_btn.setProperty("active", "true" if active else "false")
        self.dl_btn.setIcon(
            create_activity_icon("download", 14, "#a5f3fc" if active else "#e8e8ed")
        )
        self.dl_btn.setToolTip("Downloads — in progress" if active else "Downloads")
        style = self.dl_btn.style()
        style.unpolish(self.dl_btn)
        style.polish(self.dl_btn)

    def downloads_anchor(self) -> QPoint:
        return self.dl_btn.mapToGlobal(QPoint(0, self.dl_btn.height() + 4))

    def set_maximized(self, maximized: bool) -> None:
        self.lights.set_maximized(maximized)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is not None and (
                child is self.lights
                or self.lights.isAncestorOf(child)
                or child is self.dl_btn
                or self.dl_btn.isAncestorOf(child)
            ):
                return
            handle = self.window().windowHandle() if self.window() else None
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)
