"""Team Members catalog — define bots (name, photo, role, skills)."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .member_avatar import member_pixmap
from .platform.models import Bot
from .platform.registry import load_registry
from .widgets import PageHeader, chip_button, FlowLayout


class MembersPage(QWidget):
    member_new = pyqtSignal()
    member_edit = pyqtSignal(str)
    member_delete = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 18, 16)
        root.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(
            PageHeader(
                "Team Members",
                "Name, circular photo, role (fills skills and prompt). Bio is optional.",
            ),
            1,
        )
        head.addWidget(chip_button("New member", self.member_new.emit, primary=True))
        wrap = QWidget()
        wrap.setLayout(head)
        root.addWidget(wrap)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("page")
        self._flow_host = QWidget()
        self._flow = FlowLayout(spacing=10, grow=False)
        self._flow_host.setLayout(self._flow)
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 8, 8)
        col.addWidget(self._flow_host)
        col.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def reload(self) -> None:
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        registry = load_registry()
        bots = list(registry.bots.values())
        if not bots:
            empty = QLabel("No members yet. Add a bot, then put them on a team.")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self._flow.addWidget(empty)
            return
        for bot in bots:
            self._flow.addWidget(self._card(bot))

    def _card(self, bot: Bot) -> QFrame:
        frame = QFrame()
        frame.setObjectName("settingsCard")
        frame.setFixedWidth(280)
        frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        col = QVBoxLayout(frame)
        col.setContentsMargins(14, 12, 14, 12)
        top = QHBoxLayout()
        avatar = QLabel()
        avatar.setFixedSize(40, 40)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setObjectName("botAvatar")
        avatar.setPixmap(member_pixmap(bot.avatar, bot.role, 40))
        top.addWidget(avatar)
        names = QVBoxLayout()
        title = QLabel(bot.name)
        title.setObjectName("hostTitle")
        names.addWidget(title)
        job = QLabel(bot.title or bot.role)
        job.setObjectName("muted")
        names.addWidget(job)
        top.addLayout(names, 1)
        col.addLayout(top)
        if bot.description:
            bio = QLabel(bot.description)
            bio.setObjectName("muted")
            bio.setWordWrap(True)
            col.addWidget(bio)
        if bot.skills:
            col.addWidget(QLabel(" · ".join(bot.skills[:6])))
        row = QHBoxLayout()
        bid = bot.id
        row.addWidget(chip_button("Edit", lambda b=bid: self.member_edit.emit(b), primary=True))
        row.addWidget(chip_button("Delete", lambda b=bid: self.member_delete.emit(b), danger=True))
        row.addStretch(1)
        col.addLayout(row)
        return frame

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload()
