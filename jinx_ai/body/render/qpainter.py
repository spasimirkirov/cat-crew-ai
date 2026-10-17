"""QPainter consumer. Additive neon dots + optional skeleton debug."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRadialGradient

from ..controller import FIGURE_SHAPES
from ..particles.data import PINK

_CYAN_LUT = [QColor(0, 240, 255, min(255, i * 11)) for i in range(16)]
_PINK_LUT = [QColor(255, 0, 127, min(255, i * 11)) for i in range(16)]
_CYAN_DIM = [QColor(0, 140, 170, min(255, i * 6)) for i in range(16)]
_PINK_DIM = [QColor(160, 40, 90, min(255, i * 6)) for i in range(16)]


def render_particles(
    snap: dict[str, Any],
    painter: QPainter,
    viewport: tuple[int, int],
    *,
    muted: bool = False,
    hologram: float = 0.0,
) -> None:
    w, h = viewport
    cx, cy = w * 0.5, h * 0.52
    side = min(w, h)
    shape = str(snap.get("shape") or "orb")
    figure = shape in FIGURE_SHAPES
    scale = side * (0.55 if figure else 0.40)
    if figure:
        cy = h * 0.50
    muted = muted or snap.get("mode") == "sleeping"
    cyan_lut = _CYAN_DIM if muted else _CYAN_LUT
    pink_lut = _PINK_DIM if muted else _PINK_LUT

    if not figure:
        glow_r = side * 0.30
        glow = QRadialGradient(QPointF(cx, cy), glow_r)
        core_a = 22 if muted else 78
        glow.setColorAt(0.0, QColor(0, 240, 255, core_a))
        glow.setColorAt(0.45, QColor(0, 180, 220, int(core_a * 0.35)))
        glow.setColorAt(1.0, QColor(0, 240, 255, 0))
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QPointF(cx, cy), glow_r, glow_r * 1.05)

    xs = snap["x"]
    ys = snap["y"]
    life = snap["life"]
    sizes = snap["size"]
    kinds = snap["kind"]
    visible = snap.get("visible")
    n = len(xs)
    glow_mul = 0.7 if muted else (0.55 + 0.45 * float(snap.get("morph") or 1.0))
    if figure:
        glow_mul = 0.72 if muted else 0.88

    trail_x = snap.get("trail_x")
    trail_y = snap.get("trail_y")
    trail_len = int(snap.get("trail_len") or 0)
    if trail_len > 0 and trail_x is not None and trail_y is not None:
        for k in range(min(trail_len, trail_x.shape[0]) - 1, 0, -1):
            fade = 0.18 * (1.0 - k / float(trail_len))
            for i in range(n):
                if visible is not None and int(visible[i]) == 0:
                    continue
                a = float(life[i]) * fade
                if a < 0.05:
                    continue
                px = cx + float(trail_x[k, i]) * scale
                py = cy + float(trail_y[k, i]) * scale
                rad = max(0.8, float(sizes[i]) * 0.45)
                lut = pink_lut if int(kinds[i]) == PINK else cyan_lut
                painter.setBrush(lut[min(15, int(a * 15))])
                painter.drawEllipse(QRectF(px - rad, py - rad, rad * 2, rad * 2))

    for i in range(n):
        if visible is not None and int(visible[i]) == 0:
            continue
        a = float(life[i])
        if a < 0.06:
            continue
        px = cx + float(xs[i]) * scale
        py = cy + float(ys[i]) * scale
        rad = max(1.1, float(sizes[i]) * (0.55 + 0.7 * a) * glow_mul)
        lut = pink_lut if int(kinds[i]) == PINK else cyan_lut
        painter.setBrush(lut[min(15, int(a * 15))])
        painter.drawEllipse(QRectF(px - rad, py - rad, rad * 2, rad * 2))

    debug = snap.get("debug")
    if debug:
        _draw_debug(painter, debug, cx, cy, scale, n, snap)


_REGION_COLORS = [
    QColor(0, 240, 255, 160),
    QColor(255, 0, 127, 180),
    QColor(180, 220, 255, 160),
    QColor(80, 255, 180, 160),
]


def _draw_debug(painter: QPainter, debug: dict[str, Any], cx: float, cy: float, scale: float, n: int, snap: dict) -> None:
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    pen = QPen(QColor(255, 255, 255, 140), 1.2)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for bone in debug.get("bones") or []:
        x0, y0, x1, y1, _name = bone
        painter.drawLine(
            QPointF(cx + x0 * scale, cy + y0 * scale),
            QPointF(cx + x1 * scale, cy + y1 * scale),
        )
        painter.drawEllipse(QPointF(cx + x0 * scale, cy + y0 * scale), 2.4, 2.4)
    painter.setPen(QPen(QColor(255, 200, 80, 200), 1.0))
    for _name, (ix, iy) in (debug.get("ik") or {}).items():
        painter.drawEllipse(QPointF(cx + ix * scale, cy + iy * scale), 4.0, 4.0)
    bbox = debug.get("bbox") or (0, 0, 0, 0)
    painter.setPen(QPen(QColor(34, 211, 238, 90), 1.0, Qt.PenStyle.DashLine))
    x0, y0, x1, y1 = bbox
    painter.drawRect(QRectF(cx + x0 * scale, cy + y0 * scale, (x1 - x0) * scale, (y1 - y0) * scale))
    tx, ty = snap.get("tx"), snap.get("ty")
    if tx is not None and ty is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 40))
        step = max(1, n // 80)
        for i in range(0, n, step):
            painter.drawEllipse(QPointF(cx + float(tx[i]) * scale, cy + float(ty[i]) * scale), 1.6, 1.6)
    painter.setPen(QColor(245, 245, 247, 200))
    painter.setFont(QFont("Segoe UI", 8))
    lines = [
        f"fps {float(debug.get('fps') or 0):.0f}  n {debug.get('n')}",
        f"{debug.get('shape')}  {debug.get('anim')}  morph {float(debug.get('morph') or 1):.2f}",
        f"state {debug.get('state')}",
    ]
    y = 12
    for line in lines:
        painter.drawText(8, y, line)
        y += 12
