"""Desktop context menus: QMenu at the cursor, actions with icons."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QMenu, QWidget

from .icon import create_activity_icon


def popup(widget: QWidget, global_pos: QPoint, build: Callable[[QMenu], None]) -> None:
    """Build and show a context menu at the click. Skip if it has no actions."""
    menu = QMenu(widget)
    menu.setObjectName("contextMenu")
    build(menu)
    if menu.isEmpty():
        return
    menu.exec(global_pos)


def add_item(
    menu: QMenu,
    text: str,
    slot: Callable[[], None],
    *,
    icon: str = "",
    shortcut: str = "",
    enabled: bool = True,
) -> None:
    action = menu.addAction(text)
    if icon:
        action.setIcon(create_activity_icon(icon, 16, "#e8e8ed"))
    if shortcut:
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutVisibleInContextMenu(True)
    action.setEnabled(enabled)
    action.triggered.connect(lambda _checked=False: slot())


def reveal_in_explorer(path: Path) -> None:
    """Open File Explorer with this file selected, or open the folder."""
    target = path.expanduser().resolve()
    try:
        if target.is_file():
            subprocess.Popen(["explorer", f"/select,{target}"])
            return
        folder = target if target.is_dir() else target.parent
        os.startfile(str(folder))  # type: ignore[attr-defined]
    except OSError:
        folder = target if target.is_dir() else target.parent
        subprocess.Popen(["explorer", str(folder)])
