"""Settings → Connectors: enable catalog presets and paste tokens."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .connectors import (
    CatalogEntry,
    ConnectorConfig,
    ensure_catalog_rows,
    get_connector,
    load_catalog,
    set_enabled,
    set_token,
)
from .fa import IconToggle
from .icon import create_activity_icon
from .widgets import PageHeader, chip_button


class ConnectorsPage(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._rows: dict[str, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 28, 16)
        root.setSpacing(10)
        root.addWidget(
            PageHeader(
                "Connectors",
                "Optional external systems for the agent (GitHub, Linear, Notion, …). "
                "Credentials stay on this PC. Built-in tools (files, shell, search, Comfy) "
                "do not need a connector. MCP HTTP calling lands next — enable + token now.",
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
        self._rows.clear()
        ensure_catalog_rows()
        for preset in load_catalog():
            cfg = get_connector(preset.id)
            self._col.addWidget(self._card(preset, cfg))
        self._col.addStretch(1)

    def _card(self, preset: CatalogEntry, cfg: ConnectorConfig | None) -> QWidget:
        card = QFrame()
        card.setObjectName("settingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(create_activity_icon(preset.icon, 18, "#c7c7cc").pixmap(18, 18))
        head.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        name = QLabel(preset.name)
        name.setObjectName("switchTitle")
        titles.addWidget(name)
        desc = QLabel(preset.description or preset.kind)
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        titles.addWidget(desc)
        head.addLayout(titles, 1)

        toggle = IconToggle(
            "power",
            "power",
            f"Disable {preset.name}",
            f"Enable {preset.name}",
            size=28,
            checked=bool(cfg and cfg.enabled),
        )
        toggle.toggled.connect(lambda on, pid=preset.id: self._on_toggle(pid, on))
        head.addWidget(toggle, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        meta = QLabel(
            f"Kind: {preset.kind} · Auth: {preset.auth}"
            + (f" · Driver: {preset.driver}" if preset.driver else "")
        )
        meta.setObjectName("muted")
        layout.addWidget(meta)

        if preset.auth != "none":
            row = QHBoxLayout()
            field = QLineEdit()
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText("Token / API key (optional until you enable)")
            if cfg and cfg.token:
                field.setText(cfg.token)
            field.editingFinished.connect(
                lambda pid=preset.id, edit=field: self._on_token(pid, edit.text())
            )
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.addWidget(field, 1)
            layout.addLayout(row)
            self._rows[preset.id] = {"toggle": toggle, "token": field}
        else:
            self._rows[preset.id] = {"toggle": toggle}

        actions = QHBoxLayout()
        if preset.docs_url:
            actions.addWidget(
                chip_button(
                    "Docs",
                    lambda u=preset.docs_url: QDesktopServices.openUrl(QUrl(u)),
                    tip=preset.docs_url,
                )
            )
        actions.addStretch(1)
        layout.addLayout(actions)
        return card

    def _on_toggle(self, connector_id: str, enabled: bool) -> None:
        set_enabled(connector_id, enabled)
        self.changed.emit()

    def _on_token(self, connector_id: str, token: str) -> None:
        set_token(connector_id, token)
        self.changed.emit()
