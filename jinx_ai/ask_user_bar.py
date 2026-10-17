"""In-chat ask-user card (choices or free text)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .widgets import FlowLayout


class AskUserBar(QFrame):
    answered = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("askUserBar")
        self.setVisible(False)

        self.prompt = QLabel("Question")
        self.prompt.setObjectName("activityTitle")
        self.prompt.setWordWrap(True)

        self.choices_wrap = QWidget()
        self.choices_layout = FlowLayout(self.choices_wrap, spacing=6)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a short answer…")
        self.input.returnPressed.connect(self._submit_text)
        send = QPushButton("Send")
        send.setObjectName("primaryButton")
        send.clicked.connect(self._submit_text)
        skip = QPushButton("Skip")
        skip.clicked.connect(lambda: self.answered.emit(""))

        col = QVBoxLayout(self)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(8)
        col.addWidget(self.prompt)
        col.addWidget(self.choices_wrap)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(send)
        row.addWidget(skip)
        col.addLayout(row)

    def ask(self, prompt: str, choices: list[str] | None = None) -> None:
        self.prompt.setText(prompt or "Jinx needs an answer")
        while self.choices_layout.count():
            item = self.choices_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for choice in choices or []:
            btn = QPushButton(choice)
            btn.clicked.connect(lambda _=False, c=choice: self.answered.emit(c))
            self.choices_layout.addWidget(btn)
        self.choices_wrap.setVisible(bool(choices))
        self.input.clear()
        self.setVisible(True)
        self.input.setFocus()

    def clear(self) -> None:
        self.setVisible(False)
        self.input.clear()

    def _submit_text(self) -> None:
        self.answered.emit(self.input.text().strip())
