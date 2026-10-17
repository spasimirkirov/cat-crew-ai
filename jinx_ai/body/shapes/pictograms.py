"""Recognizable 2D pictograms (ISO-style) plus Font Awesome glyphs.

The overlay cannot load a VRM. It CAN sample a professional silhouette:
Font Awesome via qtawesome (already a Jinx dependency), with a bathroom-sign
fallback so tests work without a GUI.
"""

from __future__ import annotations

import numpy as np

_FA_NAMES = {
    "humanoid": ("fa5s.female", "mdi.human-female"),
    "robot": ("fa5s.robot", "mdi.robot"),
    "ghost": ("fa5s.ghost", "mdi.ghost"),
    "fox": ("fa5s.cat", "mdi.cat"),
}


def fill_capsule(
    mask: np.ndarray, x0: float, y0: float, x1: float, y1: float, radius: float
) -> None:
    h, w = mask.shape
    yy, xx = np.ogrid[:h, :w]
    dx = float(x1 - x0)
    dy = float(y1 - y0)
    length = (dx * dx + dy * dy) ** 0.5 + 1e-6
    t = ((xx - x0) * dx + (yy - y0) * dy) / (length * length)
    t = np.clip(t, 0.0, 1.0)
    px = x0 + t * dx
    py = y0 + t * dy
    mask |= (xx - px) ** 2 + (yy - py) ** 2 <= radius * radius


def fill_disk(mask: np.ndarray, cx: float, cy: float, r: float) -> None:
    h, w = mask.shape
    yy, xx = np.ogrid[:h, :w]
    mask |= (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r


def punch_disk(mask: np.ndarray, cx: float, cy: float, r: float) -> None:
    h, w = mask.shape
    yy, xx = np.ogrid[:h, :w]
    mask &= (xx - cx) ** 2 + (yy - cy) ** 2 > r * r


def person_mask(size: int = 160) -> np.ndarray:
    """Bathroom-sign humanoid: head, torso, separated arms and legs."""
    return female_mask(size)


def female_mask(size: int = 160) -> np.ndarray:
    """Female figure with side locks and a ponytail. Face sits in front of hair."""
    s = int(size)
    m = np.zeros((s, s), dtype=bool)
    cx = s * 0.50
    # Hair volume first (behind the face).
    fill_disk(m, cx, s * 0.155, s * 0.125)
    fill_capsule(m, cx - s * 0.09, s * 0.18, cx - s * 0.16, s * 0.40, s * 0.042)
    fill_capsule(m, cx + s * 0.09, s * 0.18, cx + s * 0.16, s * 0.40, s * 0.042)
    fill_capsule(m, cx + s * 0.06, s * 0.22, cx + s * 0.10, s * 0.48, s * 0.048)
    # Face
    fill_disk(m, cx, s * 0.175, s * 0.088)
    fill_capsule(m, cx, s * 0.26, cx, s * 0.31, s * 0.022)
    # Hourglass torso, wider hips
    fill_capsule(m, cx, s * 0.34, cx, s * 0.48, s * 0.078)
    fill_capsule(m, cx, s * 0.48, cx, s * 0.58, s * 0.092)
    fill_capsule(m, cx - s * 0.11, s * 0.35, cx - s * 0.18, s * 0.54, s * 0.030)
    fill_capsule(m, cx + s * 0.11, s * 0.35, cx + s * 0.18, s * 0.54, s * 0.030)
    fill_capsule(m, cx - s * 0.06, s * 0.60, cx - s * 0.10, s * 0.90, s * 0.044)
    fill_capsule(m, cx + s * 0.06, s * 0.60, cx + s * 0.10, s * 0.90, s * 0.044)
    return m


def robot_mask(size: int = 160) -> np.ndarray:
    s = int(size)
    m = np.zeros((s, s), dtype=bool)
    cx = s * 0.50
    fill_disk(m, cx, s * 0.07, s * 0.022)
    fill_capsule(m, cx, s * 0.09, cx, s * 0.15, s * 0.012)
    # Square head
    fill_capsule(m, cx, s * 0.18, cx, s * 0.30, s * 0.11)
    fill_capsule(m, cx - s * 0.08, s * 0.24, cx + s * 0.08, s * 0.24, s * 0.11)
    punch_disk(m, cx - s * 0.045, s * 0.23, s * 0.022)
    punch_disk(m, cx + s * 0.045, s * 0.23, s * 0.022)
    fill_capsule(m, cx, s * 0.36, cx, s * 0.56, s * 0.12)
    fill_capsule(m, cx - s * 0.16, s * 0.38, cx - s * 0.24, s * 0.58, s * 0.032)
    fill_capsule(m, cx + s * 0.16, s * 0.38, cx + s * 0.24, s * 0.58, s * 0.032)
    fill_capsule(m, cx - s * 0.06, s * 0.60, cx - s * 0.10, s * 0.88, s * 0.042)
    fill_capsule(m, cx + s * 0.06, s * 0.60, cx + s * 0.10, s * 0.88, s * 0.042)
    return m


def ghost_mask(size: int = 160) -> np.ndarray:
    s = int(size)
    m = np.zeros((s, s), dtype=bool)
    cx = s * 0.50
    fill_disk(m, cx, s * 0.28, s * 0.20)
    fill_capsule(m, cx, s * 0.32, cx, s * 0.62, s * 0.20)
    for i, ox in enumerate((-0.16, 0.0, 0.16)):
        fill_disk(m, cx + s * ox, s * 0.70, s * 0.09)
    punch_disk(m, cx - s * 0.07, s * 0.26, s * 0.035)
    punch_disk(m, cx + s * 0.07, s * 0.26, s * 0.035)
    return m


def cat_mask(size: int = 160) -> np.ndarray:
    s = int(size)
    m = np.zeros((s, s), dtype=bool)
    cx = s * 0.48
    fill_disk(m, cx, s * 0.42, s * 0.16)
    fill_disk(m, cx - s * 0.12, s * 0.26, s * 0.07)
    fill_disk(m, cx + s * 0.12, s * 0.26, s * 0.07)
    fill_disk(m, cx + s * 0.04, s * 0.48, s * 0.09)
    fill_capsule(m, cx, s * 0.52, cx + s * 0.04, s * 0.72, s * 0.14)
    fill_capsule(m, cx + s * 0.16, s * 0.58, cx + s * 0.34, s * 0.42, s * 0.045)
    fill_disk(m, cx - s * 0.07, s * 0.82, s * 0.055)
    fill_disk(m, cx + s * 0.10, s * 0.82, s * 0.055)
    fill_disk(m, cx + s * 0.02, s * 0.44, s * 0.05)
    return m


_PICTO = {
    "humanoid": female_mask,
    "female": female_mask,
    "robot": robot_mask,
    "ghost": ghost_mask,
    "fox": cat_mask,
}


def raster_fa_mask(kind: str, size: int = 192) -> np.ndarray | None:
    """Bake a Font Awesome / MDI glyph. Needs a QApplication (the overlay has one)."""
    names = _FA_NAMES.get(kind, ())
    try:
        from PyQt6.QtWidgets import QApplication

        from jinx_ai.icon import _awesome_pixmap
    except Exception:  # noqa: BLE001
        return None
    if QApplication.instance() is None:
        return None
    for name in names:
        pix = _awesome_pixmap(name, int(size), "#ffffff")
        if pix is None:
            continue
        img = pix.toImage()
        h, w = img.height(), img.width()
        if h < 8 or w < 8:
            continue
        mask = np.zeros((h, w), dtype=bool)
        for y in range(h):
            for x in range(w):
                mask[y, x] = img.pixelColor(x, y).alpha() > 48
        if int(mask.sum()) < 40:
            continue
        return _crop(mask)
    return None


def _crop(mask: np.ndarray, pad: int = 4) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return mask
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0 = max(0, y0 - pad)
    x0 = max(0, x0 - pad)
    y1 = min(mask.shape[0], y1 + pad)
    x1 = min(mask.shape[1], x1 + pad)
    return mask[y0:y1, x0:x1]


def figure_mask(kind: str, size: int = 160) -> np.ndarray:
    """Female pictogram for the figure; other kinds still try Font Awesome."""
    key = str(kind or "humanoid")
    if key in ("humanoid", "female"):
        return female_mask(size)
    baked = raster_fa_mask(key, size=max(128, int(size)))
    if baked is not None and baked.any():
        return baked
    fn = _PICTO.get(key, female_mask)
    return fn(size)
