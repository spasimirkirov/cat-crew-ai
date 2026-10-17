"""Settings → Skills: progressive skill catalog (name + description)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .agent_context import list_skills
from .icon import create_activity_icon
from .widgets import PageHeader


class SkillsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("page")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 28, 16)
        root.setSpacing(10)
        root.addWidget(
            PageHeader(
                "Skills",
                "Playbooks the agent can load with read_skill. The chat prompt only "
                "sees name + short description; full markdown stays on disk until needed.",
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("page")
        self._col = QVBoxLayout(body)
        self._col.setContentsMargins(0, 0, 8, 12)
        self._col.setSpacing(10)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.reload()

    def reload(self) -> None:
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        skills = list_skills()
        if not skills:
            empty = QLabel("No skills found under jinx_ai/skills or C:\\Jinx-AI\\skills.")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self._col.addWidget(empty)
        for skill in skills:
            self._col.addWidget(self._card(skill))
        self._col.addStretch(1)

    def _card(self, skill) -> QWidget:
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(create_activity_icon("book", 18, "#22d3ee").pixmap(18, 18))
        head.addWidget(icon)
        name = QLabel(skill.name)
        name.setObjectName("hostTitle")
        head.addWidget(name)
        head.addStretch(1)
        layout.addLayout(head)

        desc = QLabel(skill.description or "(no description)")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        path = QLabel(str(skill.path))
        path.setObjectName("muted")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setWordWrap(True)
        layout.addWidget(path)
        return card

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload()
