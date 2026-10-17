"""Jinx orb favicon plus toolbar icons (Font Awesome via qtawesome)."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication


CYAN = QColor("#22d3ee")
PINK = QColor("#f472b6")
VIOLET = QColor("#a78bfa")


def create_jinx_icon(size: int = 64) -> QIcon:
    """Cyan/pink orb — same palette as the desktop overlay."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    cx, cy, r = s / 2.0, s / 2.0, s / 2.0 - 1.0

    glow = QRadialGradient(QPointF(cx, cy), r)
    glow.setColorAt(0.0, QColor(34, 211, 238, 230))
    glow.setColorAt(0.45, QColor(139, 92, 246, 210))
    glow.setColorAt(0.82, QColor(244, 114, 182, 190))
    glow.setColorAt(1.0, QColor(244, 114, 182, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(cx, cy), r, r)

    core = QRadialGradient(QPointF(cx - r * 0.15, cy - r * 0.2), r * 0.7)
    core.setColorAt(0.0, QColor(255, 255, 255, 90))
    core.setColorAt(0.35, QColor(34, 211, 238, 160))
    core.setColorAt(1.0, QColor(244, 114, 182, 0))
    painter.setBrush(core)
    painter.drawEllipse(QPointF(cx, cy), r * 0.72, r * 0.72)

    painter.setBrush(PINK)
    painter.drawEllipse(QPointF(cx + r * 0.28, cy - r * 0.22), r * 0.08, r * 0.08)
    painter.setBrush(CYAN)
    painter.drawEllipse(QPointF(cx - r * 0.3, cy + r * 0.18), r * 0.06, r * 0.06)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(QPointF(cx - r * 0.08, cy - r * 0.12), r * 0.05, r * 0.05)
    painter.end()
    return QIcon(pixmap)


# Kind names used around the app → Font Awesome 5 solid (qtawesome).
_KIND_FA = {
    "files": "fa5s.folder",
    "git": "fa5s.code-branch",
    "fork": "fa5s.code-branch",
    "db": "fa5s.database",
    "settings": "fa5s.cogs",
    "character": "fa5s.user",
    "look": "fa5s.eye",
    "chat": "fa5s.comment",
    "code": "fa5s.code",
    "plus": "fa5s.plus",
    "tree": "fa5s.sitemap",
    "pen": "fa5s.pen",
    "trash": "fa5s.trash-alt",
    "brain": "fa5s.brain",
    "robot": "fa5s.robot",
    "key": "fa5s.key",
    "power": "fa5s.power-off",
    "sync": "fa5s.sync",
    "folder-plus": "fa5s.folder-plus",
    "folder-open": "fa5s.folder-open",
    "download": "fa5s.download",
    "minus": "fa5s.minus",
    "max": "fa5s.expand",
    "restore": "fa5s.compress",
    "close": "fa5s.times",
    "copy": "fa5s.copy",
    "mic": "fa5s.microphone",
    "mic-off": "fa5s.microphone-slash",
    "phone": "fa5s.phone",
    "phone-slash": "fa5s.phone-slash",
    "terminal": "fa5s.terminal",
    "send": "fa5s.paper-plane",
    "stop": "fa5s.stop",
    "bolt": "fa5s.bolt",
    "cloud": "fa5s.cloud",
    "image": "fa5s.image",
    "sliders": "fa5s.sliders-h",
    "file": "fa5s.file",
    "volume-up": "fa5s.volume-up",
    "volume-mute": "fa5s.volume-mute",
    "chevron-down": "fa5s.chevron-down",
    "chevron-up": "fa5s.chevron-up",
    "chevron-right": "fa5s.chevron-right",
    "star": "fa5s.star",
    "gpu": "fa5s.microchip",
    "wrench": "fa5s.wrench",
    "redo": "fa5s.redo",
    "plug": "fa5s.plug",
    "book": "fa5s.book",
    "list": "fa5s.list",
    "users": "fa5s.users",
    "user-tie": "fa5s.user-tie",
    "crown": "fa5s.crown",
    "briefcase": "fa5s.briefcase",
    "paint-brush": "fa5s.paint-brush",
    "check": "fa5s.check",
    "calculator": "fa5s.calculator",
    "desktop": "fa5s.desktop",
    "home": "fa5s.home",
    "id-card": "fa5s.id-card",
    "gavel": "fa5s.gavel",
    "bullhorn": "fa5s.bullhorn",
    "chart-line": "fa5s.chart-line",
    "calendar": "fa5s.calendar-alt",
    "server": "fa5s.server",
    "smile": "fa5s.smile",
}


def _fa_name(kind: str) -> str:
    raw = (kind or "").strip()
    if raw.startswith(("fa5s.", "fa5.", "mdi.", "ei.", "mdi6.")):
        return raw
    return _KIND_FA.get(raw, "fa5s.circle")


def _device_pixel_ratio() -> float:
    app = QApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    try:
        return max(1.0, float(screen.devicePixelRatio()) if screen else 1.0)
    except Exception:  # noqa: BLE001
        return 1.0


def _awesome_pixmap(name: str, size: int, color: str) -> QPixmap | None:
    """Bake a Font Awesome glyph so QSS `color` cannot swallow the icon."""
    try:
        import qtawesome as qta
    except ImportError:
        return None
    try:
        dpr = _device_pixel_ratio()
        px = max(12, int(round(size * dpr)))
        icon = qta.icon(name, color=color, color_disabled=color)
        pix = icon.pixmap(QSize(px, px))
        if pix.isNull():
            return None
        image = pix.toImage()
        ink = sum(
            1
            for x in range(0, image.width(), 2)
            for y in range(0, image.height(), 2)
            if image.pixelColor(x, y).alpha() > 40
        )
        if ink < 4:
            return None
        pix.setDevicePixelRatio(dpr)
        return pix
    except Exception:  # noqa: BLE001
        return None


def create_activity_icon(kind: str, size: int = 22, color: str = "#cbd5e1") -> QIcon:
    """Toolbar / activity-bar icon. Font Awesome, baked to a pixmap."""
    pix = _awesome_pixmap(_fa_name(kind), max(12, int(size)), color)
    if pix is not None:
        return QIcon(pix)
    return _paint_fallback(kind, size, color)


def _paint_fallback(kind: str, size: int, color: str) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24.0, size / 24.0)
    ink = QColor(color)
    pen = QPen(ink, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _paint_glyph(painter, kind, ink, pen)
    painter.end()
    return QIcon(pixmap)


def _paint_glyph(painter: QPainter, kind: str, ink: QColor, pen: QPen) -> None:
    if kind == "files":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawRoundedRect(3, 7, 18, 13, 2, 2)
        painter.drawRoundedRect(3, 5, 8, 4, 1, 1)
    elif kind == "git" or kind == "fork":
        painter.setBrush(ink)
        painter.drawEllipse(4, 4, 5, 5)
        painter.drawEllipse(4, 15, 5, 5)
        painter.drawEllipse(15, 9, 5, 5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(6, 9, 6, 15)
        painter.drawLine(9, 7, 15, 11)
    elif kind == "db":
        painter.drawEllipse(4, 4, 16, 6)
        painter.drawLine(4, 7, 4, 17)
        painter.drawLine(20, 7, 20, 17)
        painter.drawArc(4, 14, 16, 6, 0, -180 * 16)
        painter.drawArc(4, 9, 16, 6, 0, -180 * 16)
    elif kind == "settings":
        painter.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawEllipse(QPointF(9.5, 10.5), 3.4, 3.4)
        for i in range(8):
            painter.save()
            painter.translate(9.5, 10.5)
            painter.rotate(i * 45)
            painter.drawLine(QPointF(0, -5.2), QPointF(0, -8.4))
            painter.restore()
        painter.drawEllipse(QPointF(16.2, 15.2), 2.4, 2.4)
        for i in range(8):
            painter.save()
            painter.translate(16.2, 15.2)
            painter.rotate(i * 45 + 22)
            painter.drawLine(QPointF(0, -3.6), QPointF(0, -6.2))
            painter.restore()
    elif kind == "character":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawEllipse(8, 3, 8, 8)
        painter.drawEllipse(4, 13, 16, 10)
    elif kind == "look":
        eye = QPainterPath()
        eye.moveTo(3.2, 12)
        eye.quadTo(12, 4.2, 20.8, 12)
        eye.quadTo(12, 19.8, 3.2, 12)
        painter.drawPath(eye)
        painter.setPen(QPen(ink, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(QRectF(9.2, 8.6, 5.6, 6.8))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawEllipse(QRectF(10.6, 10.2, 2.8, 3.4))
    elif kind == "chat":
        path = QPainterPath()
        path.addRoundedRect(QRectF(3, 4, 18, 13), 4, 4)
        path.moveTo(8, 17)
        path.lineTo(6, 21)
        path.lineTo(12, 17)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
    elif kind == "code":
        painter.drawLine(8, 7, 4, 12)
        painter.drawLine(4, 12, 8, 17)
        painter.drawLine(16, 7, 20, 12)
        painter.drawLine(20, 12, 16, 17)
        painter.drawLine(13, 6, 11, 18)
    elif kind == "plus":
        painter.drawLine(12, 5, 12, 19)
        painter.drawLine(5, 12, 19, 12)
    elif kind == "tree":
        painter.drawLine(6, 5, 6, 19)
        painter.drawLine(6, 12, 16, 12)
        painter.drawLine(6, 19, 16, 19)
        painter.setBrush(ink)
        painter.drawEllipse(15, 9, 5, 5)
        painter.drawEllipse(15, 16, 5, 5)
    elif kind == "pen":
        painter.drawLine(6, 18, 18, 6)
        painter.drawLine(16, 4, 20, 8)
        painter.drawLine(6, 18, 4, 20)
    elif kind == "trash":
        painter.drawRoundedRect(QRectF(6, 8, 12, 12), 1.5, 1.5)
        painter.drawLine(9, 11, 9, 17)
        painter.drawLine(12, 11, 12, 17)
        painter.drawLine(15, 11, 15, 17)
        painter.drawLine(5, 8, 19, 8)
        painter.drawLine(9, 5, 15, 5)
        painter.drawLine(9, 5, 9, 8)
        painter.drawLine(15, 5, 15, 8)
    elif kind == "brain":
        painter.drawEllipse(5, 6, 14, 13)
        painter.drawLine(12, 6, 12, 19)
        painter.drawArc(5, 9, 7, 6, 0, 180 * 16)
        painter.drawArc(12, 9, 7, 6, 0, -180 * 16)
    elif kind == "robot":
        painter.drawLine(QPointF(12, 3.4), QPointF(12, 7.2))
        painter.setBrush(ink)
        painter.drawEllipse(QRectF(10.7, 2.0, 2.6, 2.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(5.2, 7.0, 13.6, 12.4), 2.4, 2.4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawEllipse(QRectF(7.8, 10.6, 3.4, 3.4))
        painter.drawEllipse(QRectF(12.8, 10.6, 3.4, 3.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawLine(QPointF(9.2, 16.6), QPointF(14.8, 16.6))
    elif kind == "power":
        # Qt arcs: 0° is 3 o'clock, positive is counter-clockwise. Gap stays at 12 o'clock.
        painter.setPen(QPen(ink, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(5.5, 6.5, 13.0, 13.0), 128 * 16, 284 * 16)
        painter.drawLine(QPointF(12, 4.5), QPointF(12, 12.2))
    elif kind == "sync":
        painter.drawArc(5, 5, 14, 14, 40 * 16, 200 * 16)
        painter.drawLine(19, 5, 19, 11)
        painter.drawLine(19, 5, 14, 5)
        painter.drawArc(5, 5, 14, 14, 220 * 16, 200 * 16)
        painter.drawLine(5, 19, 5, 13)
        painter.drawLine(5, 19, 10, 19)
    elif kind in ("folder-plus", "folder-open"):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawRoundedRect(3, 8, 18, 12, 2, 2)
        painter.drawRoundedRect(3, 6, 8, 4, 1, 1)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        if kind == "folder-plus":
            painter.drawLine(12, 11, 12, 17)
            painter.drawLine(9, 14, 15, 14)
    elif kind == "minus":
        painter.setPen(QPen(ink, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(6, 12, 18, 12)
    elif kind == "max":
        painter.drawRect(6, 6, 12, 12)
    elif kind == "restore":
        painter.drawRect(8, 5, 10, 10)
        painter.drawLine(6, 9, 6, 19)
        painter.drawLine(6, 19, 16, 19)
    elif kind == "close":
        painter.setPen(QPen(ink, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(7, 7, 17, 17)
        painter.drawLine(17, 7, 7, 17)
    elif kind == "copy":
        painter.drawRoundedRect(QRectF(8.5, 4.5, 10, 12), 1.6, 1.6)
        painter.drawRoundedRect(QRectF(5, 8, 10, 12), 1.6, 1.6)
    elif kind == "mic":
        painter.drawRoundedRect(QRectF(9.2, 3.6, 5.6, 10.4), 2.8, 2.8)
        painter.drawArc(QRectF(6.4, 9.2, 11.2, 9.2), 0, -180 * 16)
        painter.drawLine(QPointF(12, 18.4), QPointF(12, 20.4))
        painter.drawLine(QPointF(8.6, 20.6), QPointF(15.4, 20.6))
    elif kind == "mic-off":
        painter.drawRoundedRect(QRectF(9.2, 3.6, 5.6, 10.4), 2.8, 2.8)
        painter.drawArc(QRectF(6.4, 9.2, 11.2, 9.2), 0, -180 * 16)
        painter.drawLine(QPointF(12, 18.4), QPointF(12, 20.4))
        painter.drawLine(QPointF(8.6, 20.6), QPointF(15.4, 20.6))
        painter.setPen(QPen(ink, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(6.2, 6.2), QPointF(17.8, 17.8))
    elif kind in {"phone", "phone-slash"}:
        painter.save()
        painter.translate(12, 12)
        painter.rotate(-40)
        painter.drawRoundedRect(QRectF(-3.2, -9.2, 6.4, 5.6), 2.2, 2.2)
        painter.drawLine(QPointF(0, -3.4), QPointF(0, 3.4))
        painter.drawRoundedRect(QRectF(-3.2, 3.6, 6.4, 5.6), 2.2, 2.2)
        painter.restore()
        if kind == "phone-slash":
            painter.setPen(QPen(ink, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(6.2, 6.2), QPointF(17.8, 17.8))
    elif kind == "terminal":
        painter.drawRoundedRect(QRectF(4, 6, 16, 12), 2, 2)
        painter.drawLine(7, 10, 10, 12)
        painter.drawLine(10, 12, 7, 14)
        painter.drawLine(12, 14, 16, 14)
    elif kind == "send":
        painter.drawLine(5, 12, 19, 5)
        painter.drawLine(19, 5, 14, 19)
        painter.drawLine(19, 5, 11, 13)
    elif kind == "stop":
        painter.setBrush(ink)
        painter.drawRoundedRect(QRectF(7, 6, 10, 12), 1.5, 1.5)
    elif kind == "bolt":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(13, 3),
                    QPointF(8, 13),
                    QPointF(12, 13),
                    QPointF(11, 21),
                    QPointF(19, 10),
                    QPointF(13, 10),
                ]
            )
        )
    elif kind == "cloud":
        painter.drawRoundedRect(QRectF(5, 11, 14, 7), 3, 3)
        painter.drawEllipse(QRectF(7, 8, 7, 7))
        painter.drawEllipse(QRectF(12, 7, 6, 6))
    elif kind == "image":
        painter.drawRoundedRect(QRectF(4, 6, 16, 13), 2, 2)
        painter.setBrush(ink)
        painter.drawEllipse(QRectF(7, 8.5, 3.2, 3.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(6, 17, 11, 12)
        painter.drawLine(11, 12, 14, 15)
        painter.drawLine(14, 15, 18, 11)
    elif kind == "file":
        painter.drawLine(8, 4, 14, 4)
        painter.drawLine(14, 4, 18, 8)
        painter.drawLine(18, 8, 18, 20)
        painter.drawLine(18, 20, 6, 20)
        painter.drawLine(6, 20, 6, 4)
        painter.drawLine(14, 4, 14, 8)
        painter.drawLine(14, 8, 18, 8)
    elif kind == "volume-up":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawPolygon(
            QPolygonF([QPointF(4, 9), QPointF(9, 9), QPointF(14, 5), QPointF(14, 19), QPointF(9, 15), QPointF(4, 15)])
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawArc(QRectF(13, 8, 6, 8), -50 * 16, 100 * 16)
        painter.drawArc(QRectF(15, 6, 7, 12), -50 * 16, 100 * 16)
    elif kind == "volume-mute":
        painter.drawPolygon(
            QPolygonF([QPointF(4, 9), QPointF(9, 9), QPointF(14, 5), QPointF(14, 19), QPointF(9, 15), QPointF(4, 15)])
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ink, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(6.2, 6.2), QPointF(18.5, 18.5))
    elif kind == "chevron-down":
        painter.setPen(QPen(ink, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(6, 9), QPointF(12, 16))
        painter.drawLine(QPointF(12, 16), QPointF(18, 9))
    elif kind == "chevron-up":
        painter.setPen(QPen(ink, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(6, 15), QPointF(12, 8))
        painter.drawLine(QPointF(12, 8), QPointF(18, 15))
    elif kind == "chevron-right":
        painter.setPen(QPen(ink, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(9, 6), QPointF(16, 12))
        painter.drawLine(QPointF(16, 12), QPointF(9, 18))
    elif kind == "star":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(12, 3),
                    QPointF(14.4, 9.2),
                    QPointF(21, 9.4),
                    QPointF(15.6, 13.4),
                    QPointF(17.6, 20),
                    QPointF(12, 16.2),
                    QPointF(6.4, 20),
                    QPointF(8.4, 13.4),
                    QPointF(3, 9.4),
                    QPointF(9.6, 9.2),
                ]
            )
        )
    elif kind in ("gpu", "wrench"):
        painter.drawRoundedRect(QRectF(5, 8, 14, 10), 2, 2)
        painter.drawLine(8, 8, 8, 5)
        painter.drawLine(16, 8, 16, 5)
        painter.drawLine(8, 18, 8, 21)
        painter.drawLine(16, 18, 16, 21)
    elif kind == "redo":
        painter.drawArc(6, 5, 12, 14, 40 * 16, 250 * 16)
        painter.drawLine(QPointF(16, 5), QPointF(19, 8))
        painter.drawLine(QPointF(16, 5), QPointF(13, 8))
    else:
        painter.drawEllipse(6, 6, 12, 12)
