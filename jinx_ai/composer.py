"""Chat composer: growing text area with call / stop / send inside the field."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QTextOption
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .fa import IconToggle, icon_button
from .icon import create_activity_icon
from .widgets import FlowLayout


class ComposerEdit(QPlainTextEdit):
    submitted = pyqtSignal()
    send_now = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("composerEdit")
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setTabChangesFocus(True)
        self.textChanged.connect(self._fit)
        self._min_lines = 3
        self._max_lines = 8
        self._fit()

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:  # noqa: N802
        self.setPlainText(text)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            if mods & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            if mods & Qt.KeyboardModifier.ControlModifier:
                self.send_now.emit()
                return
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def _line_h(self) -> int:
        return max(18, self.fontMetrics().lineSpacing())

    def _fit(self) -> None:
        line = self._line_h()
        extra = 14
        min_h = line * self._min_lines + extra
        max_h = line * self._max_lines + extra
        doc_h = int(self.document().size().height()) + extra
        self.setFixedHeight(max(min_h, min(max_h, doc_h)))


class Composer(QWidget):
    submitted = pyqtSignal(str)
    send_now = pyqtSignal(str)
    stop_requested = pyqtSignal()
    call_toggled = pyqtSignal(bool)
    queue_removed = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        self._in_call = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.queue_host = QWidget()
        self.queue_host.setVisible(False)
        self.queue_layout = FlowLayout(self.queue_host, spacing=6)
        root.addWidget(self.queue_host)

        frame = QFrame()
        frame.setObjectName("composer")
        inner = QHBoxLayout(frame)
        inner.setContentsMargins(10, 6, 6, 6)
        inner.setSpacing(4)

        self.editor = ComposerEdit()
        self.editor.submitted.connect(self._on_submit)
        self.editor.send_now.connect(self._on_send_now)
        inner.addWidget(self.editor, stretch=1)

        btns = QVBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(4)
        btns.addStretch(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.call_toggle = IconToggle(
            "phone",
            "phone-slash",
            "Leave the call",
            "Start a call",
            size=28,
            checked=False,
        )
        self.call_toggle.toggled.connect(self.call_toggled.emit)
        self.now_button = icon_button(
            "bolt", "Send now — steer immediately", self._on_send_now, size=28
        )
        self.now_button.setVisible(False)
        self.stop_button = QPushButton()
        self.stop_button.setObjectName("composerStop")
        self.stop_button.setToolTip("Stop this turn")
        self.stop_button.setFixedSize(28, 28)
        self.stop_button.setIcon(create_activity_icon("stop", 14, "#fecaca"))
        self.stop_button.setIconSize(QSize(14, 14))
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.stop_button.setVisible(False)
        self.send_button = QPushButton()
        self.send_button.setObjectName("composerSend")
        self.send_button.setToolTip("Send (Enter)")
        self.send_button.setFixedSize(28, 28)
        self.send_button.setIcon(create_activity_icon("send", 14, "#ecfeff"))
        self.send_button.setIconSize(QSize(14, 14))
        self.send_button.clicked.connect(self._on_submit)
        row.addWidget(self.call_toggle)
        row.addWidget(self.now_button)
        row.addWidget(self.stop_button)
        row.addWidget(self.send_button)
        btns.addLayout(row)
        inner.addLayout(btns)
        root.addWidget(frame)

        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)
        root.addWidget(self.hint)

        self.harness = QLabel("")
        self.harness.setObjectName("muted")
        self.harness.setVisible(False)
        root.addWidget(self.harness)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def text(self) -> str:
        return self.editor.toPlainText().strip()

    def clear(self) -> None:
        self.editor.setPlainText("")

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self.editor.setPlaceholderText(text)

    def setFocus(self) -> None:  # noqa: N802
        self.editor.setFocus()

    def set_harness_status(self, text: str) -> None:
        line = (text or "").strip()
        self.harness.setText(line)
        self.harness.setVisible(bool(line) and not self._busy)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.stop_button.setVisible(busy)
        self.now_button.setVisible(busy)
        self.send_button.setToolTip(
            "Queue a follow-up (Enter)" if busy else "Send (Enter)"
        )
        if busy:
            self.setPlaceholderText(
                "Queue a follow-up (Enter). Ctrl+Enter or the bolt sends it now."
            )
            self.hint.setText("Queued messages run after this turn. Bolt / Ctrl+Enter steers now.")
            self.hint.setVisible(True)
            self.harness.setVisible(False)
        else:
            self.hint.setVisible(False)
            self.harness.setVisible(bool(self.harness.text().strip()))

    def set_in_call(self, on: bool) -> None:
        self._in_call = bool(on)
        self.call_toggle.blockSignals(True)
        self.call_toggle.setChecked(self._in_call)
        self.call_toggle.blockSignals(False)

    def set_queue(self, items: list[str]) -> None:
        while self.queue_layout.count():
            item = self.queue_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self.queue_host.setVisible(bool(items))
        if not items:
            return
        for index, text in enumerate(items):
            chip = QPushButton(f"Queued: {text[:42]}")
            chip.setObjectName("chipBtn")
            chip.setToolTip("Remove from queue")
            chip.clicked.connect(lambda _=False, i=index: self.queue_removed.emit(i))
            self.queue_layout.addWidget(chip)

    def _take(self) -> str:
        body = self.editor.toPlainText().strip()
        if body:
            self.editor.setPlainText("")
        return body

    def _on_submit(self) -> None:
        body = self._take()
        if body:
            self.submitted.emit(body)

    def _on_send_now(self) -> None:
        body = self._take()
        if body:
            self.send_now.emit(body)
