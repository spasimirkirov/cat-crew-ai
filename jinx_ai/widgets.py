"""Modern controls — large hit targets, explicit on/off state."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QEnterEvent, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .icon import create_activity_icon


class FlowLayout(QLayout):
    """Left-to-right row that wraps, then grows Expanding items into leftover space."""

    def __init__(
        self, parent=None, *, spacing: int = 6, columns: int = 0, grow: bool = True
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._columns = max(0, int(columns))
        self._grow = bool(grow)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def __del__(self) -> None:
        while self.takeAt(0) is not None:
            pass

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        top = rect.y() + margins.top()
        right = rect.x() + rect.width() - margins.right()
        space_x = self.spacing()
        space_y = self.spacing()
        x = left
        y = top
        row_height = 0
        row: list[tuple[QLayoutItem, int, int]] = []
        columns = self._columns
        avail = max(1, right - left)
        cell = 0
        if columns > 1:
            cell = max(1, (avail - space_x * (columns - 1)) // columns)

        def flush() -> None:
            nonlocal x, y, row_height, row
            if not row:
                return
            used = sum(width for _, width, _ in row) + space_x * (len(row) - 1)
            extra = max(0, (right - left) - used)
            grow = []
            if self._grow and columns <= 1:
                grow = [
                    item
                    for item, _, _ in row
                    if item.expandingDirections() & Qt.Orientation.Horizontal
                ]
            extra_each = extra // len(grow) if grow else 0
            leftover = extra - extra_each * len(grow) if grow else 0
            cursor = left
            for item, width, height in row:
                if grow and item in grow:
                    width += extra_each
                    if leftover:
                        width += leftover
                        leftover = 0
                if not test_only:
                    if columns > 1:
                        height = row_height
                        top = y
                    else:
                        top = y + (row_height - height) // 2
                    item.setGeometry(
                        QRect(
                            QPoint(cursor, top),
                            QSize(width, height),
                        )
                    )
                cursor += width + space_x
            y += row_height + space_y
            x = left
            row_height = 0
            row = []

        for item in self._items:
            if columns > 1:
                width = cell
                height = (
                    item.heightForWidth(width)
                    if item.hasHeightForWidth()
                    else item.sizeHint().height()
                )
                if row and len(row) >= columns:
                    flush()
            else:
                hint = item.sizeHint()
                width, height = hint.width(), hint.height()
                if row and x + width > right:
                    flush()
            row.append((item, width, height))
            row_height = max(row_height, height)
            x += width + space_x
        flush()
        if y == top:
            return margins.top() + margins.bottom()
        return y - space_y - rect.y() + margins.bottom()


class ToggleSwitch(QWidget):
    """Phone-style switch, not a native checkbox."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._enabled = True
        self._compact = bool(compact)
        if self._compact:
            self.setFixedSize(36, 20)
        else:
            self.setFixedSize(52, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("On" if self._checked else "Off")

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool) -> None:
        on = bool(on)
        if on == self._checked:
            return
        self._checked = on
        self.setToolTip("On" if on else "Off")
        self.update()
        self.toggled.emit(on)

    def setEnabled(self, enabled: bool) -> None:  # noqa: A003
        self._enabled = bool(enabled)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self._enabled
            else Qt.CursorShape.ForbiddenCursor
        )
        self.update()
        super().setEnabled(enabled)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._enabled and event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        if not self._enabled:
            bg = QColor("#1e293b")
            border = QColor("#334155")
            knob = QColor("#64748b")
        elif self._checked:
            bg = QColor("#22d3ee")
            border = QColor("#a5f3fc")
            knob = QColor("#ffffff")
        else:
            bg = QColor("#1c2030")
            border = QColor("#636366")
            knob = QColor("#e8e8ed")
        painter.setBrush(bg)
        painter.setPen(border)
        radius = rect.height() / 2.0
        painter.drawRoundedRect(QRectF(rect), radius, radius)
        diameter = 14 if self._compact else 22
        inset = 3 if self._compact else 4
        y = (self.height() - diameter) / 2
        x = self.width() - diameter - inset if self._checked else inset
        painter.setBrush(knob)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(x, y, diameter, diameter))


class SwitchRow(QFrame):
    """Clickable row: title + subtitle + switch + ON/OFF."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, subtitle: str = "", checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("switchRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._switch = ToggleSwitch(checked)
        self._state = QLabel("ON" if checked else "OFF")
        self._state.setObjectName("switchState")
        self._state.setFixedWidth(40)
        self._state.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        title_l = QLabel(title)
        title_l.setObjectName("switchTitle")
        text_col.addWidget(title_l)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("muted")
            sub.setWordWrap(True)
            text_col.addWidget(sub)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)
        row.addLayout(text_col, stretch=1)
        row.addWidget(self._state, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._switch, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._switch.toggled.connect(self._on_switch)

    def _on_switch(self, on: bool) -> None:
        self._state.setText("ON" if on else "OFF")
        self._state.setProperty("on", "true" if on else "false")
        self._state.style().unpolish(self._state)
        self._state.style().polish(self._state)
        self.toggled.emit(on)

    def isChecked(self) -> bool:
        return self._switch.isChecked()

    def setChecked(self, on: bool) -> None:
        self._switch.setChecked(on)
        self._state.setText("ON" if self._switch.isChecked() else "OFF")

    def setEnabled(self, enabled: bool) -> None:  # noqa: A003
        self._switch.setEnabled(enabled)
        super().setEnabled(enabled)

    def blockSignals(self, block: bool) -> bool:  # noqa: N802
        prev = super().blockSignals(block)
        self._switch.blockSignals(block)
        return prev

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is self._switch:
                return
            self._switch.setChecked(not self._switch.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)


def chip_button(
    text: str, slot, *, primary: bool = False, danger: bool = False, tip: str = ""
) -> QPushButton:
    btn = QPushButton(text)
    if danger:
        btn.setObjectName("chipDanger")
    else:
        btn.setObjectName("chipPrimary" if primary else "chipBtn")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    if tip:
        btn.setToolTip(tip)
        btn.setStatusTip(tip)
        btn.setAccessibleName(tip)
    btn.clicked.connect(lambda _checked=False: slot())
    return btn


class PageHeader(QWidget):
    """Title + optional subtitle — one pattern for Settings pages."""

    def __init__(self, title: str, subtitle: str = "", parent=None, *, compact: bool = False) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("hostTitle" if compact else "h1")
        col.addWidget(heading)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("muted")
            sub.setWordWrap(True)
            col.addWidget(sub)


class Pill(QLabel):
    """Tiny status chip (Loaded / Chat / Image / cap)."""

    def __init__(self, text: str, kind: str = "cap", parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("pill")
        self.setProperty("kind", kind)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


class TrafficLight(QWidget):
    """One macOS-style window button."""

    clicked = pyqtSignal()

    _FILL = {
        "close": QColor("#ff5f57"),
        "min": QColor("#febc2e"),
        "max": QColor("#28c840"),
    }

    def __init__(self, role: str, tip: str, parent=None) -> None:
        super().__init__(parent)
        self._role = role
        self._hover = False
        self.setFixedSize(14, 14)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor(self._FILL.get(self._role, QColor("#636366")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(QRectF(0.5, 0.5, 13, 13))
        if not self._hover:
            return
        painter.setPen(
            QPen(QColor(40, 20, 18, 200), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        if self._role == "close":
            painter.drawLine(4, 4, 10, 10)
            painter.drawLine(10, 4, 4, 10)
        elif self._role == "min":
            painter.drawLine(4, 7, 10, 7)
        else:
            painter.drawLine(7, 4, 7, 10)
            painter.drawLine(4, 7, 10, 7)


class TrafficLights(QWidget):
    close_clicked = pyqtSignal()
    minimize_clicked = pyqtSignal()
    maximize_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.close_btn = TrafficLight("close", "Close")
        self.min_btn = TrafficLight("min", "Minimize")
        self.max_btn = TrafficLight("max", "Maximize")
        self.close_btn.clicked.connect(self.close_clicked.emit)
        self.min_btn.clicked.connect(self.minimize_clicked.emit)
        self.max_btn.clicked.connect(self.maximize_clicked.emit)
        row.addWidget(self.min_btn)
        row.addWidget(self.max_btn)
        row.addWidget(self.close_btn)

    def set_maximized(self, maximized: bool) -> None:
        self.max_btn.setToolTip("Restore" if maximized else "Maximize")


class HudButton(QPushButton):
    """Circular overlay control. Mute swaps icon + color; close is a proper mark."""

    def __init__(self, kind: str, tip: str, *, role: str = "neutral", parent=None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._role = role
        self.setObjectName("hudBtn")
        self.setProperty("role", role)
        self.setProperty("state", "live")
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(44, 44)
        self.setIconSize(QSize(20, 20))
        self._paint_icon(kind)

    def _paint_icon(self, kind: str, color: str = "#f5f5f7") -> None:
        self.setIcon(create_activity_icon(kind, 20, color))

    def set_live(self, live: bool) -> None:
        if self._role == "call":
            self.set_in_call(bool(live))
            return
        if self._role != "mute":
            return
        on = bool(live)
        self.setProperty("state", "live" if on else "muted")
        if on:
            self.setToolTip("Mute microphone")
            self._paint_icon("mic", "#28c840")
        else:
            self.setToolTip("Unmute microphone")
            self._paint_icon("mic-off", "#ff5f57")
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def set_in_call(self, on: bool) -> None:
        if self._role != "call":
            return
        live = bool(on)
        self.setProperty("state", "live" if live else "idle")
        if live:
            self.setToolTip("Leave the call")
            self._paint_icon("phone", "#28c840")
        else:
            self.setToolTip("Start a call")
            self._paint_icon("phone-slash", "#ff5f57")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
