"""Raster SDF (chamfer) and mask sampling. No scipy."""

from __future__ import annotations

import numpy as np

from ..particles.targets import resample_xy
from .base import ShapeContext, ShapeResult


def chamfer_edt(mask: np.ndarray) -> np.ndarray:
    """Approximate distance from each True (inside) pixel to nearest False."""
    inside = np.asarray(mask, dtype=bool)
    h, w = inside.shape
    inf = float(h + w)
    dist = np.where(inside, inf, 0.0).astype(np.float32)
    # Forward
    for y in range(h):
        for x in range(w):
            d = dist[y, x]
            if y > 0:
                d = min(d, dist[y - 1, x] + 1.0)
                if x > 0:
                    d = min(d, dist[y - 1, x - 1] + 1.414)
                if x + 1 < w:
                    d = min(d, dist[y - 1, x + 1] + 1.414)
            if x > 0:
                d = min(d, dist[y, x - 1] + 1.0)
            dist[y, x] = d
    # Backward
    for y in range(h - 1, -1, -1):
        for x in range(w - 1, -1, -1):
            d = dist[y, x]
            if y + 1 < h:
                d = min(d, dist[y + 1, x] + 1.0)
                if x > 0:
                    d = min(d, dist[y + 1, x - 1] + 1.414)
                if x + 1 < w:
                    d = min(d, dist[y + 1, x + 1] + 1.414)
            if x + 1 < w:
                d = min(d, dist[y, x + 1] + 1.0)
            dist[y, x] = d
    dist[~inside] = 0.0
    return dist


def _safe_choice(
    rng: np.random.Generator,
    n_src: int,
    count: int,
    weights: np.ndarray | None,
) -> np.ndarray:
    """numpy Generator.choice requires p to sum to 1 within tight atol."""
    n_src = max(1, int(n_src))
    count = max(1, int(count))
    if weights is None or n_src <= 1:
        return rng.integers(0, n_src, size=count)
    p = np.asarray(weights, dtype=np.float64)
    if p.shape[0] != n_src:
        return rng.integers(0, n_src, size=count)
    np.nan_to_num(p, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    p = np.clip(p, 0.0, None)
    total = float(p.sum())
    if not np.isfinite(total) or total <= 1e-12:
        return rng.integers(0, n_src, size=count)
    p /= total
    p[-1] = 1.0 - float(p[:-1].sum())
    if p[-1] < 0.0 or not np.isfinite(p[-1]):
        return rng.integers(0, n_src, size=count)
    try:
        return rng.choice(n_src, size=count, replace=True, p=p)
    except ValueError:
        return rng.integers(0, n_src, size=count)


def sample_mask(
    mask: np.ndarray,
    count: int,
    rng: np.random.Generator,
    *,
    use_sdf: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Return unit-space points (origin center, y+ down) from an opaque mask."""
    m = np.asarray(mask)
    if m.ndim == 3:
        m = m[..., -1] if m.shape[-1] >= 4 else m.mean(axis=2)
    inside = m if m.dtype == bool else m > 0.5
    if not np.any(inside):
        z = np.zeros(count, np.float32)
        return z, z.copy()
    h, w = inside.shape
    ys, xs = np.nonzero(inside)
    if use_sdf and inside.size <= 192 * 96:
        sdf = chamfer_edt(inside)
        edge = np.exp(-((sdf[ys, xs] - 1.3) ** 2) / 2.2)
        interior = np.clip(sdf[ys, xs] / (float(sdf.max()) + 1e-6), 0.0, 1.0)
        weights = 0.82 * edge + 0.18 * interior
        pick = _safe_choice(rng, xs.shape[0], count, weights)
    else:
        pick = rng.integers(0, xs.shape[0], size=count)
    px = xs[pick].astype(np.float32)
    py = ys[pick].astype(np.float32)
    x = (px / max(w - 1, 1) - 0.5) * 1.35
    y = (py / max(h - 1, 1) - 0.45) * 1.55
    return x.astype(np.float32), y.astype(np.float32)


def map_mask_to_box(
    mask: np.ndarray,
    count: int,
    rng: np.random.Generator,
    box: tuple[float, float, float, float],
) -> tuple[np.ndarray, np.ndarray]:
    """Contour-biased samples mapped into a world-space box (x0,y0,x1,y1)."""
    m = np.asarray(mask)
    inside = m if m.dtype == bool else m > 0.5
    if not np.any(inside):
        z = np.zeros(count, np.float32)
        return z, z.copy()
    h, w = inside.shape
    ys, xs = np.nonzero(inside)
    if inside.size <= 192 * 192:
        sdf = chamfer_edt(inside)
        edge = np.exp(-((sdf[ys, xs] - 1.3) ** 2) / 2.2)
        interior = np.clip(sdf[ys, xs] / (float(sdf.max()) + 1e-6), 0.0, 1.0)
        weights = 0.85 * edge + 0.15 * interior
        pick = _safe_choice(rng, xs.shape[0], count, weights)
    else:
        pick = rng.integers(0, xs.shape[0], size=count)
    px = xs[pick].astype(np.float32)
    py = ys[pick].astype(np.float32)
    x0, y0, x1, y1 = box
    nx = px / max(w - 1, 1)
    ny = py / max(h - 1, 1)
    return (x0 + nx * (x1 - x0)).astype(np.float32), (y0 + ny * (y1 - y0)).astype(np.float32)


def raster_disk(mask: np.ndarray, cx: float, cy: float, r: float) -> None:
    h, w = mask.shape
    yy, xx = np.ogrid[:h, :w]
    mask |= (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r


def fox_mask(size: int = 64) -> np.ndarray:
    from .pictograms import cat_mask

    return cat_mask(max(48, int(size)))


class SilhouetteGenerator:
    name = "silhouette"

    def __init__(self, mask: np.ndarray | None = None, name: str = "silhouette") -> None:
        self.name = name
        self.mask = fox_mask() if mask is None else mask
        self._px: np.ndarray
        self._py: np.ndarray
        self._w: np.ndarray | None
        self._cache_points()

    def _cache_points(self) -> None:
        m = np.asarray(self.mask)
        if m.ndim == 3:
            m = m[..., -1] if m.shape[-1] >= 4 else m.mean(axis=2)
        inside = m if m.dtype == bool else m > 0.5
        if not np.any(inside):
            self._px = np.zeros(1, np.float32)
            self._py = np.zeros(1, np.float32)
            self._w = None
            self._h = 1
            self._wdt = 1
            return
        h, w = inside.shape
        ys, xs = np.nonzero(inside)
        self._px = xs.astype(np.float32)
        self._py = ys.astype(np.float32)
        self._h = h
        self._wdt = w
        if inside.size <= 192 * 192:
            sdf = chamfer_edt(inside)
            edge = np.exp(-((sdf[ys, xs] - 1.3) ** 2) / 2.2)
            interior = np.clip(sdf[ys, xs] / (float(sdf.max()) + 1e-6), 0.0, 1.0)
            weights = 0.85 * edge + 0.15 * interior
            total = float(np.nansum(weights))
            if np.isfinite(total) and total > 1e-12:
                self._w = (weights / total).astype(np.float64)
            else:
                self._w = None
        else:
            self._w = None

    def generate(self, count: int, ctx: ShapeContext) -> ShapeResult:
        rng = ctx.rng or np.random.default_rng(11)
        n = max(1, int(count))
        pick = _safe_choice(rng, int(self._px.shape[0]), n, self._w)
        px = self._px[pick]
        py = self._py[pick]
        x = (px / max(self._wdt - 1, 1) - 0.5) * 1.35
        y = (py / max(self._h - 1, 1) - 0.45) * 1.55
        x = x + 0.02 * np.sin(ctx.t * 1.1 + px * 0.07)
        y = y + 0.015 * np.sin(ctx.t * 0.8)
        region = np.full(n, 13, np.int16)
        return ShapeResult(x=x, y=y, region=region)


def generate_from_points(points: np.ndarray, count: int, rng: np.random.Generator) -> ShapeResult:
    pts = np.asarray(points, dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] < 2:
        z = np.zeros(count, np.float32)
        return ShapeResult(x=z, y=z.copy(), region=np.zeros(count, np.int16))
    x, y, region = resample_xy(pts[:, 0], pts[:, 1], count, rng)
    return ShapeResult(x=x, y=y, region=region)


def generate_from_mask(mask: np.ndarray, count: int, rng: np.random.Generator) -> ShapeResult:
    x, y = sample_mask(mask, count, rng)
    return ShapeResult(x=x, y=y, region=np.full(count, 13, np.int16))


def generate_from_primitives(primitives: list[dict], count: int, rng: np.random.Generator) -> ShapeResult:
    """primitives: {kind, ...} circle/ellipse/capsule/rectangle."""
    from ..particles.targets import sample_capsule, sample_circle, sample_rect

    if not primitives:
        z = np.zeros(count, np.float32)
        return ShapeResult(x=z, y=z.copy(), region=np.zeros(count, np.int16))
    weights = np.array([float(p.get("weight") or 1.0) for p in primitives], dtype=np.float64)
    weights = weights / weights.sum()
    shares = np.maximum(1, np.round(weights * count).astype(np.int32))
    while int(shares.sum()) > count:
        shares[int(np.argmax(shares))] -= 1
    while int(shares.sum()) < count:
        shares[int(np.argmax(weights))] += 1
    xs, ys = [], []
    for prim, n in zip(primitives, shares, strict=True):
        kind = str(prim.get("kind") or "circle")
        cx, cy = float(prim.get("x", 0.0)), float(prim.get("y", 0.0))
        if kind == "capsule":
            px, py = sample_capsule(
                cx,
                cy,
                float(prim.get("tx", cx)),
                float(prim.get("ty", cy + 0.2)),
                float(prim.get("width", 0.08)),
                int(n),
                rng,
            )
        elif kind in {"rectangle", "rect"}:
            px, py = sample_rect(cx, cy, float(prim.get("w", 0.2)), float(prim.get("h", 0.2)), int(n), rng)
        elif kind == "ellipse":
            px, py = sample_circle(cx, cy, float(prim.get("r", 0.1)), int(n), rng)
            px = cx + (px - cx) * float(prim.get("sx", 1.0))
            py = cy + (py - cy) * float(prim.get("sy", 1.2))
        else:
            px, py = sample_circle(cx, cy, float(prim.get("r", 0.1)), int(n), rng)
        xs.append(px)
        ys.append(py)
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    x, y, region = resample_xy(x, y, count, rng)
    return ShapeResult(x=x, y=y, region=region)
