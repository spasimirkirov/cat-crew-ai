"""Toolbar icon buttons. Font Awesome via qtawesome, baked so QSS cannot hide them."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QPushButton

from .icon import create_activity_icon

ON_COLOR = "#28c840"
OFF_COLOR = "#ff5f57"

_FA_KIND = {
    "fa5s.plus": "plus",
    "fa5s.code-branch": "fork",
    "fa5s.sitemap": "tree",
    "fa5s.pen": "pen",
    "fa5s.trash-alt": "trash",
    "fa5s.brain": "brain",
    "fa5s.sync": "sync",
    "fa5s.folder-plus": "folder-plus",
    "fa5s.folder-open": "folder-open",
    "fa5s.folder": "files",
    "fa5s.file": "file",
    "fa5s.download": "download",
    "fa5s.power-off": "power",
    "fa5s.comment": "chat",
    "fa5s.code": "code",
    "fa5s.eye": "look",
    "fa5s.cogs": "settings",
    "fa5s.cog": "settings",
    "fa5s.terminal": "terminal",
    "fa5s.paper-plane": "send",
    "fa5s.stop": "stop",
    "fa5s.bolt": "bolt",
    "fa5s.microphone": "mic",
    "fa5s.microphone-slash": "mic-off",
    "fa5s.phone": "phone",
    "fa5s.phone-slash": "phone-slash",
    "fa5s.volume-up": "volume-up",
    "fa5s.volume-mute": "volume-mute",
    "fa5s.chevron-down": "chevron-down",
    "fa5s.chevron-up": "chevron-up",
    "fa5s.chevron-right": "chevron-right",
    "fa5s.star": "star",
    "fa5s.microchip": "gpu",
    "fa5s.wrench": "wrench",
    "fa5s.redo": "redo",
    "fa5s.times": "close",
    "fa5s.list": "list",
    "fa5s.desktop": "desktop",
    "fa5s.home": "home",
    "fa5s.id-card": "id-card",
    "fa5s.image": "image",
}


def fa_icon(name: str, color: str = "#e2e8f0") -> QIcon:
    return create_activity_icon(_FA_KIND.get(name, name), 18, color)


def icon_button(
    name: str, tip: str, slot, *, danger: bool = False, size: int = 32
) -> QPushButton:
    kind = _FA_KIND.get(name, name)
    glyph = 18 if size <= 28 else 20
    btn = QPushButton()
    btn.setObjectName("dangerIcon" if danger else "iconBtn")
    btn.setToolTip(tip)
    btn.setStatusTip(tip)
    btn.setAccessibleName(tip)
    btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
    btn.setIcon(create_activity_icon(kind, glyph, "#fb7185" if danger else "#e8e8ed"))
    btn.setIconSize(QSize(glyph, glyph))
    btn.setFixedSize(size, size)
    btn.setToolTipDuration(8000)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.clicked.connect(lambda _checked=False: slot())
    return btn


class IconToggle(QPushButton):
    """On/off chrome control: Font Awesome glyph + green / red, never a switch."""

    def __init__(
        self,
        on_kind: str,
        off_kind: str,
        on_tip: str,
        off_tip: str,
        *,
        size: int = 28,
        checked: bool = True,
        on_color: str = ON_COLOR,
        off_color: str = OFF_COLOR,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._on_kind = on_kind
        self._off_kind = off_kind
        self._on_tip = on_tip
        self._off_tip = off_tip
        self._on_color = on_color
        self._off_color = off_color
        self._glyph = 16 if size <= 28 else 18
        self.setObjectName("iconToggle")
        self.setCheckable(True)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(self._glyph, self._glyph))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self.setToolTipDuration(8000)
        self.toggled.connect(self._sync)
        self.setChecked(bool(checked))
        self._sync()

    def setEnabled(self, enabled: bool) -> None:  # noqa: A003
        super().setEnabled(enabled)
        self._sync()

    def _sync(self, _on: bool = False) -> None:
        del _on
        on = self.isChecked()
        kind = self._on_kind if on else self._off_kind
        if not self.isEnabled():
            color = "#636366"
        else:
            color = self._on_color if on else self._off_color
        tip = self._on_tip if on else self._off_tip
        self.setIcon(create_activity_icon(kind, self._glyph, color))
        self.setToolTip(tip)
        self.setStatusTip(tip)
        self.setAccessibleName(tip)
        self.setProperty("on", "true" if on else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
