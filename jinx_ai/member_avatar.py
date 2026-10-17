"""Circular member photos: crop math, generic role glyphs, saved PNGs."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtWidgets import QWidget

from .config import BASE_DIR
from .icon import create_activity_icon
from .platform.roles import role_preset

CROP_SIZE = 240
CROP_INSET = 12
AVATAR_SAVE_SIZE = 256


def avatars_dir() -> Path:
    return BASE_DIR / "data" / "platform" / "avatars"


def circle_mask(pix: QPixmap, size: int | None = None) -> QPixmap:
    if pix.isNull():
        return QPixmap()
    side = int(size or min(pix.width(), pix.height()))
    side = max(16, side)
    scaled = pix.scaled(
        side,
        side,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - side) // 2)
    y = max(0, (scaled.height() - side) // 2)
    out = QPixmap(side, side)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, side, side)
    painter.setClipPath(path)
    painter.drawPixmap(-x, -y, scaled)
    painter.end()
    return out


def generic_role_pixmap(role: str, size: int) -> QPixmap:
    preset = role_preset(role)
    side = max(16, int(size))
    out = QPixmap(side, side)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#12151f"))
    painter.setPen(QPen(QColor(preset.color), max(1.0, side / 24.0)))
    pad = max(1.0, side / 24.0)
    painter.drawEllipse(QRectF(pad, pad, side - pad * 2, side - pad * 2))
    glyph = max(10, int(side * 0.42))
    icon = create_activity_icon(preset.icon, glyph, preset.color).pixmap(glyph, glyph)
    painter.drawPixmap((side - icon.width()) // 2, (side - icon.height()) // 2, icon)
    painter.end()
    return out


def member_pixmap(avatar: str, role: str, size: int) -> QPixmap:
    path = Path(avatar) if avatar else None
    if path is not None and path.is_file():
        pix = QPixmap(str(path))
        if not pix.isNull():
            return circle_mask(pix, size)
    return generic_role_pixmap(role, size)


def save_member_avatar(pix: QPixmap, bot_id: str) -> str:
    if pix.isNull() or not bot_id:
        return ""
    folder = avatars_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{bot_id}.png"
    masked = circle_mask(pix, AVATAR_SAVE_SIZE)
    masked.save(str(dest), "PNG")
    return str(dest)


class CircleCropView(QWidget):
    """Pan + zoom a photo under a circular crop. Drag to pan, wheel to zoom."""

    changed = pyqtSignal()

    def __init__(self, parent=None, *, size: int = CROP_SIZE) -> None:
        super().__init__(parent)
        self.setObjectName("circleCrop")
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._src = QPixmap()
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._drag: QPoint | None = None

    def has_image(self) -> bool:
        return not self._src.isNull()

    def set_image(self, path: str) -> None:
        pix = QPixmap(path)
        self._src = pix
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._clamp()
        self.update()
        self.changed.emit()

    def clear(self) -> None:
        self._src = QPixmap()
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()
        self.changed.emit()

    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, value: float) -> None:
        self._zoom = max(1.0, min(3.0, float(value)))
        self._clamp()
        self.update()
        self.changed.emit()

    def cropped(self, size: int = AVATAR_SAVE_SIZE) -> QPixmap:
        if self._src.isNull():
            return QPixmap()
        buf = QPixmap(self.width(), self.height())
        buf.fill(Qt.GlobalColor.transparent)
        painter = QPainter(buf)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_image(painter)
        painter.end()
        inset = CROP_INSET
        side = self.width() - inset * 2
        square = buf.copy(inset, inset, side, side)
        return circle_mask(square, size)

    def _circle(self) -> QRectF:
        inset = float(CROP_INSET)
        return QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2)

    def _cover_scale(self) -> float:
        if self._src.isNull():
            return 1.0
        circle = self._circle()
        return max(circle.width() / self._src.width(), circle.height() / self._src.height())

    def _image_rect(self) -> QRectF:
        scale = self._cover_scale() * self._zoom
        w = self._src.width() * scale
        h = self._src.height() * scale
        cx = self.width() / 2 + self._pan.x()
        cy = self.height() / 2 + self._pan.y()
        return QRectF(cx - w / 2, cy - h / 2, w, h)

    def _clamp(self) -> None:
        if self._src.isNull():
            self._pan = QPointF(0.0, 0.0)
            return
        circle = self._circle()
        radius = circle.width() / 2
        dest = self._image_rect()
        max_x = max(0.0, dest.width() / 2 - radius)
        max_y = max(0.0, dest.height() / 2 - radius)
        self._pan.setX(max(-max_x, min(max_x, self._pan.x())))
        self._pan.setY(max(-max_y, min(max_y, self._pan.y())))

    def _paint_image(self, painter: QPainter) -> None:
        if self._src.isNull():
            return
        dest = self._image_rect()
        painter.drawPixmap(dest, self._src, QRectF(self._src.rect()))

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#090b12"))
        circle = self._circle()
        if not self._src.isNull():
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(circle)
            painter.setClipPath(clip)
            self._paint_image(painter)
            painter.restore()
            overlay = QPainterPath()
            overlay.addRect(QRectF(self.rect()))
            overlay.addEllipse(circle)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(9, 11, 18, 168))
            painter.drawPath(overlay)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#22d3ee"), 2))
        painter.drawEllipse(circle)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._src.isNull():
            self._drag = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is None:
            super().mouseMoveEvent(event)
            return
        pos = event.position().toPoint()
        delta = pos - self._drag
        self._drag = pos
        self._pan += QPointF(delta.x(), delta.y())
        self._clamp()
        self.update()
        self.changed.emit()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._src.isNull():
            super().wheelEvent(event)
            return
        step = 0.08 if event.angleDelta().y() > 0 else -0.08
        self.set_zoom(self._zoom + step)
        event.accept()
