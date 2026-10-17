"""Home: Projects and Teams boards."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .platform.registry import load_registry
from .widgets import PageHeader, chip_button, FlowLayout


def _card(title: str, subtitle: str, actions: list) -> QFrame:
    frame = QFrame()
    frame.setObjectName("settingsCard")
    frame.setFixedWidth(280)
    frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    col = QVBoxLayout(frame)
    col.setContentsMargins(14, 12, 14, 12)
    col.setSpacing(8)
    name = QLabel(title)
    name.setObjectName("hostTitle")
    col.addWidget(name)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        col.addWidget(sub)
    row = QHBoxLayout()
    row.setContentsMargins(0, 4, 0, 0)
    row.setSpacing(6)
    for widget in actions:
        row.addWidget(widget)
    row.addStretch(1)
    col.addLayout(row)
    return frame


class HomePage(QWidget):
    project_opened = pyqtSignal(str)
    project_new = pyqtSignal()
    project_edit = pyqtSignal(str)
    project_delete = pyqtSignal(str)
    team_opened = pyqtSignal(str)
    team_new = pyqtSignal()
    team_edit = pyqtSignal(str)
    team_delete = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 18, 16)
        root.setSpacing(12)
        root.addWidget(
            PageHeader(
                "Home",
                "Projects you run, and teams you staff. Members live in the catalog.",
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("page")
        self._col = QVBoxLayout(body)
        self._col.setContentsMargins(0, 0, 8, 8)
        self._col.setSpacing(16)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def reload(self) -> None:
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        registry = load_registry()

        proj_head = QHBoxLayout()
        ph = QLabel("Projects")
        ph.setObjectName("h1")
        proj_head.addWidget(ph)
        proj_head.addStretch(1)
        proj_head.addWidget(chip_button("New project", self.project_new.emit, primary=True))
        wrap = QWidget()
        wrap.setLayout(proj_head)
        self._col.addWidget(wrap)
        if not registry.projects:
            empty = QLabel("No projects yet. Create one to open the Project Explorer.")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self._col.addWidget(empty)
        else:
            grid = QWidget()
            flow = FlowLayout(spacing=10, grow=False)
            grid.setLayout(flow)
            for project in registry.projects.values():
                n_teams = len(project.team_ids)
                n_bots = len(registry.project_bots(project))
                sub = project.description or "No description"
                sub = f"{sub}\n{n_teams} team{'s' if n_teams != 1 else ''} · {n_bots} bot{'s' if n_bots != 1 else ''}"
                pid = project.id
                flow.addWidget(
                    _card(
                        project.name,
                        sub,
                        [
                            chip_button("Open", lambda p=pid: self.project_opened.emit(p), primary=True),
                            chip_button("Edit", lambda p=pid: self.project_edit.emit(p)),
                            chip_button("Delete", lambda p=pid: self.project_delete.emit(p), danger=True),
                        ],
                    )
                )
            self._col.addWidget(grid)

        team_head = QHBoxLayout()
        th = QLabel("Teams")
        th.setObjectName("h1")
        team_head.addWidget(th)
        team_head.addStretch(1)
        team_head.addWidget(chip_button("New team", self.team_new.emit, primary=True))
        wrap2 = QWidget()
        wrap2.setLayout(team_head)
        self._col.addWidget(wrap2)
        if not registry.teams:
            empty = QLabel("No teams yet. Add members first, then group them.")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self._col.addWidget(empty)
        else:
            grid = QWidget()
            flow = FlowLayout(spacing=10, grow=False)
            grid.setLayout(flow)
            for team in registry.teams.values():
                n = len(team.bot_ids)
                tid = team.id
                flow.addWidget(
                    _card(
                        team.name,
                        (team.description or "No description") + f"\n{n} member{'s' if n != 1 else ''}",
                        [
                            chip_button("Edit", lambda t=tid: self.team_edit.emit(t), primary=True),
                            chip_button("Delete", lambda t=tid: self.team_delete.emit(t), danger=True),
                        ],
                    )
                )
            self._col.addWidget(grid)
        self._col.addStretch(1)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload()
