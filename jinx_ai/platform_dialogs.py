"""Create / edit dialogs for bots, teams, and projects."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .fa import icon_button
from .member_avatar import CircleCropView, member_pixmap, save_member_avatar
from .platform.models import Bot, Project, Team, new_id
from .platform.registry import load_registry
from .platform.roles import apply_role_fields, role_ids, role_preset


class BotDialog(QDialog):
    def __init__(self, bot: Bot | None = None, parent=None) -> None:
        super().__init__(parent)
        self._bot_id = bot.id if bot else new_id("bot")
        self._existing_avatar = (bot.avatar if bot else "") or ""
        self._cleared = False
        self._applied_role = (bot.role if bot else "general") or "general"
        self.setWindowTitle("Edit member" if bot else "New team member")
        self.setMinimumWidth(780)
        self.resize(840, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        split = QHBoxLayout()
        split.setSpacing(20)

        left = QWidget()
        left.setFixedWidth(248)
        photo = QVBoxLayout(left)
        photo.setContentsMargins(0, 0, 0, 0)
        photo.setSpacing(8)
        self.preview = QLabel()
        self.preview.setObjectName("memberPreview")
        self.preview.setFixedSize(96, 96)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        photo.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignHCenter)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        btns.addStretch(1)
        btns.addWidget(
            icon_button("fa5s.image", "Upload a circular photo", self._pick_avatar, size=28)
        )
        btns.addWidget(
            icon_button(
                "fa5s.times",
                "Remove photo — use a generic role glyph",
                self._clear_avatar,
                danger=True,
                size=28,
            )
        )
        btns.addStretch(1)
        photo.addLayout(btns)
        hint = QLabel("Drag to pan. Wheel or slider to zoom. Photo is optional.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        photo.addWidget(hint)
        self.crop = CircleCropView(size=220)
        self.crop.hide()
        self.crop.changed.connect(self._refresh_preview)
        photo.addWidget(self.crop, alignment=Qt.AlignmentFlag.AlignHCenter)
        zoom_row = QWidget()
        zoom_l = QHBoxLayout(zoom_row)
        zoom_l.setContentsMargins(0, 0, 0, 0)
        zoom_label = QLabel("Zoom")
        zoom_label.setObjectName("muted")
        zoom_l.addWidget(zoom_label)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(100, 300)
        self.zoom.setValue(100)
        self.zoom.valueChanged.connect(self._on_zoom)
        zoom_l.addWidget(self.zoom, 1)
        self._zoom_row = zoom_row
        zoom_row.hide()
        photo.addWidget(zoom_row)
        photo.addStretch(1)
        split.addWidget(left, 0)

        form_wrap = QWidget()
        form = QFormLayout(form_wrap)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.name = QLineEdit(bot.name if bot else "")
        self.name.setPlaceholderText("Required")
        self.title = QLineEdit((bot.title if bot else "") or "")
        self.role = QComboBox()
        for role_id in role_ids():
            self.role.addItem(role_preset(role_id).label, role_id)
        idx = self.role.findData(self._applied_role)
        if idx >= 0:
            self.role.setCurrentIndex(idx)
        self.details = QTextEdit()
        self.details.setPlaceholderText("Optional. A sentence or two about this member.")
        self.details.setPlainText((bot.description if bot else "") or "")
        self.details.setFixedHeight(56)
        self.skills = QLineEdit(", ".join(bot.skills) if bot else "")
        self.prompt = QTextEdit()
        self.prompt.setPlainText((bot.system_prompt if bot else "") or "")
        self.prompt.setFixedHeight(140)
        form.addRow("Name", self.name)
        form.addRow("Title", self.title)
        form.addRow("Role", self.role)
        form.addRow("Bio (optional)", self.details)
        form.addRow("Skills", self.skills)
        form.addRow("Prompt", self.prompt)
        split.addWidget(form_wrap, 1)
        root.addLayout(split, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.name.textChanged.connect(self._sync_ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if bot is None:
            self._fill_role(previous="")
        self.role.currentIndexChanged.connect(self._on_role)
        self._sync_ok()
        self._refresh_preview()

    def _role_id(self) -> str:
        return str(self.role.currentData() or "general")

    def _fill_role(self, previous: str) -> None:
        title, skills, prompt = apply_role_fields(
            self._role_id(),
            previous,
            title=self.title.text(),
            skills=self.skills.text(),
            prompt=self.prompt.toPlainText(),
        )
        self.title.setText(title)
        self.skills.setText(skills)
        self.prompt.setPlainText(prompt)
        self._applied_role = self._role_id()
        if not self.crop.has_image():
            self._refresh_preview()

    def _on_role(self, _index: int = 0) -> None:
        previous = self._applied_role
        self._fill_role(previous)

    def _on_zoom(self, value: int) -> None:
        self.crop.set_zoom(value / 100.0)

    def _sync_ok(self, _text: str = "") -> None:
        if self._ok is not None:
            self._ok.setEnabled(bool(self.name.text().strip()))

    def _pick_avatar(self) -> None:
        path, _ok = QFileDialog.getOpenFileName(
            self, "Profile photo", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return
        self._cleared = False
        self.crop.set_image(path)
        self.crop.show()
        self._zoom_row.show()
        self.zoom.blockSignals(True)
        self.zoom.setValue(100)
        self.zoom.blockSignals(False)
        self._refresh_preview()

    def _clear_avatar(self) -> None:
        self._cleared = True
        self._existing_avatar = ""
        self.crop.clear()
        self.crop.hide()
        self._zoom_row.hide()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self.crop.has_image():
            self.preview.setPixmap(self.crop.cropped(96))
            want = int(round(self.crop.zoom() * 100))
            if self.zoom.value() != want:
                self.zoom.blockSignals(True)
                self.zoom.setValue(want)
                self.zoom.blockSignals(False)
            return
        if not self._cleared and self._existing_avatar:
            self.preview.setPixmap(member_pixmap(self._existing_avatar, self._role_id(), 96))
            return
        self.preview.setPixmap(member_pixmap("", self._role_id(), 96))

    def result_bot(self) -> Bot | None:
        name = self.name.text().strip()
        if not name:
            return None
        skills = [s.strip() for s in self.skills.text().split(",") if s.strip()]
        existing = load_registry().bots.get(self._bot_id)
        if self.crop.has_image():
            avatar = save_member_avatar(self.crop.cropped(), self._bot_id)
        elif self._cleared:
            avatar = ""
        else:
            avatar = self._existing_avatar
        return Bot(
            id=self._bot_id,
            name=name,
            role=self._role_id(),
            system_prompt=self.prompt.toPlainText().strip(),
            mcp_tools=list(existing.mcp_tools) if existing else [],
            team_id=existing.team_id if existing else None,
            tools=list(existing.tools) if existing else [],
            inherit_global_mcp=existing.inherit_global_mcp if existing else True,
            lead=existing.lead if existing else False,
            max_rounds=existing.max_rounds if existing else 8,
            description=self.details.toPlainText().strip(),
            title=self.title.text().strip(),
            avatar=avatar,
            skills=skills,
        )


class TeamDialog(QDialog):
    def __init__(self, team: Team | None = None, parent=None) -> None:
        super().__init__(parent)
        self._team_id = team.id if team else new_id("team")
        self.setWindowTitle("Edit team" if team else "New team")
        self.setMinimumWidth(440)
        form = QFormLayout()
        self.name = QLineEdit(team.name if team else "")
        self.details = QTextEdit()
        self.details.setPlainText((team.description if team else "") or "")
        self.details.setFixedHeight(72)
        self.members = QListWidget()
        self.members.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        chosen = set(team.bot_ids if team else [])
        for bot in load_registry().bots.values():
            item = QListWidgetItem(f"{bot.name}  ({bot.role})")
            item.setData(256, bot.id)
            item.setCheckState(
                Qt.CheckState.Checked if bot.id in chosen else Qt.CheckState.Unchecked
            )
            self.members.addItem(item)
        form.addRow("Name", self.name)
        form.addRow("Description", self.details)
        form.addRow("Members", self.members)
        root = QVBoxLayout(self)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def result_team(self) -> Team | None:
        name = self.name.text().strip()
        if not name:
            return None
        ids: list[str] = []
        for i in range(self.members.count()):
            item = self.members.item(i)
            if item is None:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(str(item.data(256) or ""))
        ids = [x for x in ids if x]
        existing = load_registry().teams.get(self._team_id)
        lead = existing.lead_bot_id if existing else (ids[0] if ids else None)
        if lead not in ids:
            lead = ids[0] if ids else None
        return Team(
            id=self._team_id,
            name=name,
            description=self.details.toPlainText().strip(),
            bot_ids=ids,
            lead_bot_id=lead,
        )


class ProjectDialog(QDialog):
    def __init__(self, project: Project | None = None, parent=None) -> None:
        super().__init__(parent)
        self._project_id = project.id if project else new_id("proj")
        self._is_new = project is None
        self._kickoff = ""
        self.setWindowTitle("Edit project" if project else "New project")
        self.setMinimumWidth(520)
        form = QFormLayout()
        self.name = QLineEdit(project.name if project else "")
        self.name.setPlaceholderText("Required")
        self.details = QTextEdit()
        self.details.setPlaceholderText("What is this project? Optional.")
        self.details.setPlainText((project.description if project else "") or "")
        self.details.setFixedHeight(72)
        self.folder = QLineEdit(
            (project.folders[0] if project and project.folders else "") or ""
        )
        self.folder.setPlaceholderText("Shared computer — a folder on this PC")
        folder_row = QWidget()
        folder_l = QHBoxLayout(folder_row)
        folder_l.setContentsMargins(0, 0, 0, 0)
        folder_l.setSpacing(6)
        folder_l.addWidget(self.folder, 1)
        folder_l.addWidget(
            icon_button(
                "fa5s.folder-open",
                "Choose the project computer folder",
                self._pick_folder,
                size=28,
            )
        )
        form.addRow("Title", self.name)
        form.addRow("Description", self.details)
        form.addRow("Computer", folder_row)
        hint = QLabel(
            "Bots share this folder (Files / Terminal). Leave empty to attach later. "
            "Never pick the Jinx install tree."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        self.teams = QListWidget()
        self.teams.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.teams.setFixedHeight(120)
        chosen = set(project.team_ids if project else [])
        for team in load_registry().teams.values():
            item = QListWidgetItem(team.name)
            item.setData(256, team.id)
            item.setCheckState(
                Qt.CheckState.Checked if team.id in chosen else Qt.CheckState.Unchecked
            )
            self.teams.addItem(item)
        form.addRow("Teams", self.teams)
        self.kickoff = QTextEdit()
        self.kickoff.setPlaceholderText(
            "Optional. Message Chief now — what to build, stack, constraints. "
            "Same as typing in Everyone after open. Leave empty to type there."
        )
        self.kickoff.setFixedHeight(88)
        if self._is_new:
            form.addRow("Kickoff", self.kickoff)
        root = QVBoxLayout(self)
        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.name.textChanged.connect(self._sync_ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._sync_ok()

    def _sync_ok(self, _text: str = "") -> None:
        if self._ok is not None:
            self._ok.setEnabled(bool(self.name.text().strip()))

    def _pick_folder(self) -> None:
        from .workspace import folder_picker_start

        start = self.folder.text().strip() or str(folder_picker_start())
        path = QFileDialog.getExistingDirectory(self, "Project computer", start)
        if path:
            self.folder.setText(path)

    def accept(self) -> None:  # noqa: N802
        if not self.name.text().strip():
            return
        raw = self.folder.text().strip()
        if raw:
            from .workspace import prepare_computer_folder

            try:
                prepare_computer_folder(raw)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Computer", str(exc))
                return
        super().accept()

    def kickoff_text(self) -> str:
        if not self._is_new:
            return ""
        return self.kickoff.toPlainText().strip()

    def result_project(self) -> Project | None:
        name = self.name.text().strip()
        if not name:
            return None
        from .workspace import is_app_install, prepare_computer_folder

        folders: list[str] = []
        raw = self.folder.text().strip()
        if raw:
            try:
                folders = [str(prepare_computer_folder(raw))]
            except (OSError, ValueError):
                return None
            if folders and is_app_install(folders[0]):
                return None
        ids: list[str] = []
        for i in range(self.teams.count()):
            item = self.teams.item(i)
            if item is None:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(str(item.data(256) or ""))
        ids = [x for x in ids if x]
        existing = load_registry().projects.get(self._project_id)
        if not folders and existing:
            folders = list(existing.folders)
        return Project(
            id=self._project_id,
            name=name,
            description=self.details.toPlainText().strip(),
            team_ids=ids,
            bot_ids=list(existing.bot_ids) if existing else [],
            folders=folders,
            state=dict(existing.state) if existing else {},
            events=list(existing.events) if existing else [],
        )


class PickListDialog(QDialog):
    """Pick teams or bots to add to a project."""

    def __init__(self, title: str, labels: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        self.list = QListWidget()
        for item_id, label in labels:
            row = QListWidgetItem(label)
            row.setData(256, item_id)
            self.list.addItem(row)
        root = QVBoxLayout(self)
        root.addWidget(self.list)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_id(self) -> str:
        item = self.list.currentItem()
        if item is None:
            return ""
        return str(item.data(256) or "")
