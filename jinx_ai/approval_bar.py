"""In-chat tool approval bar (Allow once / Always allow / Deny)."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class ToolApprovalBar(QFrame):
    """Pause strip above the composer while a destructive tool awaits a decision."""

    decided = pyqtSignal(str)  # allow | allow_always | deny

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("approvalBar")
        self.setVisible(False)
        self._tool = ""

        self.title = QLabel("Approve tool?")
        self.title.setObjectName("activityTitle")
        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)

        allow = QPushButton("Allow once")
        allow.setObjectName("primaryButton")
        allow.clicked.connect(lambda: self.decided.emit("allow"))
        always = QPushButton("Always allow")
        always.clicked.connect(lambda: self.decided.emit("allow_always"))
        deny = QPushButton("Deny")
        deny.clicked.connect(lambda: self.decided.emit("deny"))

        col = QVBoxLayout(self)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)
        col.addWidget(self.title)
        col.addWidget(self.detail)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(allow)
        row.addWidget(always)
        row.addWidget(deny)
        row.addStretch(1)
        col.addLayout(row)

    def ask(self, tool_name: str, detail: str = "") -> None:
        self._tool = tool_name
        self.title.setText(f"Approve `{tool_name}`?")
        self.detail.setText(detail or "This tool is marked as destructive.")
        self.setVisible(True)

    def clear(self) -> None:
        self._tool = ""
        self.setVisible(False)
