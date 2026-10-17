"""Grok-style Bot roster: named teammates, presence, click to message."""

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
from .widgets import PageHeader, chip_button


class BotRow(QFrame):
    clicked = pyqtSignal(str)

    def __init__(
        self,
        bot_id: str,
        name: str,
        title: str,
        *,
        role: str,
        avatar: str = "",
        selected: bool,
    ) -> None:
        super().__init__()
        self.bot_id = bot_id
        self.setObjectName("botRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("selected", "true" if selected else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(64)
        sty = self.style()
        sty.unpolish(self)
        sty.polish(self)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)

        face = QLabel()
        face.setFixedSize(36, 36)
        face.setAlignment(Qt.AlignmentFlag.AlignCenter)
        face.setObjectName("botAvatar")
        face.setPixmap(member_pixmap(avatar, role, 36))
        row.addWidget(face)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        who = QLabel(name)
        who.setObjectName("botName")
        col.addWidget(who)
        job = QLabel(title)
        job.setObjectName("muted")
        job.setWordWrap(True)
        col.addWidget(job)
        row.addLayout(col, 1)

        self._dot = QLabel()
        self._dot.setObjectName("botPresence")
        self._dot.setFixedSize(8, 8)
        self._dot.setProperty("state", "idle")
        row.addWidget(self._dot, alignment=Qt.AlignmentFlag.AlignTop)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.bot_id)
        super().mousePressEvent(event)

    def set_presence(self, state: str) -> None:
        self._dot.setProperty("state", state or "idle")
        style = self._dot.style()
        style.unpolish(self._dot)
        style.polish(self._dot)


class PlatformPage(QWidget):
    """Sidebar roster inside a project: Everyone + assigned bots."""

    bot_opened = pyqtSignal(str)
    computer_requested = pyqtSignal()
    add_team_requested = pyqtSignal()
    add_bot_requested = pyqtSignal()
    leave_project_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._active_bot_id = "chief"
        self._active_project_id = ""
        self._rows: dict[str, BotRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 16, 10, 12)
        root.setSpacing(8)
        root.addWidget(
            PageHeader(
                "Project",
                "Everyone is the group chat. Click a bot for 1:1.",
                compact=True,
            )
        )
        self._project_label = QLabel("")
        self._project_label.setObjectName("muted")
        self._project_label.setWordWrap(True)
        root.addWidget(self._project_label)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        actions.addWidget(chip_button("Add team", self.add_team_requested.emit))
        actions.addWidget(chip_button("Add bot", self.add_bot_requested.emit))
        actions.addWidget(chip_button("Home", self.leave_project_requested.emit))
        actions.addStretch(1)
        act_wrap = QWidget()
        act_wrap.setLayout(actions)
        root.addWidget(act_wrap)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("page")
        self._col = QVBoxLayout(body)
        self._col.setContentsMargins(0, 0, 4, 8)
        self._col.setSpacing(4)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def set_active_project(self, project_id: str) -> None:
        self._active_project_id = str(project_id or "").strip()
        self.reload()

    def set_active_bot(self, bot_id: str) -> None:
        self._active_bot_id = str(bot_id or "chief").strip() or "chief"
        self.reload()

    def set_presence(self, bot_id: str, state: str) -> None:
        row = self._rows.get(bot_id)
        if row is not None:
            row.set_presence(state)

    def reload(self) -> None:
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()
        registry = load_registry()
        project = registry.projects.get(self._active_project_id)
        if project is None and registry.projects:
            project = next(iter(registry.projects.values()))
        if project is not None:
            n = len(registry.project_bots(project))
            self._project_label.setText(
                f"{project.name}  ·  {n} specialist{'s' if n != 1 else ''}"
            )
        else:
            self._project_label.setText("Open a project from Home to staff it.")

        chief = registry.chief.as_bot()
        self._add_row(
            chief,
            title="Group chat — Chief of Staff coordinates",
            selected=self._active_bot_id in {"", "chief"},
            label="Everyone",
        )
        bots = registry.project_bots(project) if project else []
        for bot in bots:
            team = ""
            if bot.team_id and bot.team_id in registry.teams:
                team = registry.teams[bot.team_id].name
            title = bot.description or bot.role
            if team:
                title = f"{title}  ·  {team}"
            self._add_row(bot, title=title, selected=bot.id == self._active_bot_id)
        self._col.addStretch(1)

    def _add_row(self, bot: Bot, *, title: str, selected: bool, label: str = "") -> None:
        row = BotRow(
            bot.id,
            label or bot.name,
            title,
            role=bot.role,
            avatar=bot.avatar,
            selected=selected,
        )
        row.clicked.connect(self.bot_opened.emit)
        self._rows[bot.id] = row
        self._col.addWidget(row)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload()
