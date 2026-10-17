"""Orb particle cloud plus mode-driven glyphs (smile, heart, brain, star)."""

from __future__ import annotations

import math

import numpy as np

from .base import ShapeContext, ShapeResult
from .glyphs import effect_targets

TAU = math.tau


def _fade(x: np.ndarray) -> np.ndarray:
    return x * x * (3.0 - 2.0 * x)


def value_noise(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = _fade(x - x0)
    fy = _fade(y - y0)

    def cell(ix: np.ndarray, iy: np.ndarray) -> np.ndarray:
        n = (ix * 374761393.0 + iy * 668265263.0).astype(np.float64)
        n = np.mod(n, 2147483647.0)
        n = np.mod(n * 1274126177.0 + 311, 2147483647.0)
        return (n / 2147483647.0).astype(np.float32)

    ix0 = x0.astype(np.float32)
    iy0 = y0.astype(np.float32)
    n00 = cell(ix0, iy0)
    n10 = cell(ix0 + 1, iy0)
    n01 = cell(ix0, iy0 + 1)
    n11 = cell(ix0 + 1, iy0 + 1)
    nx0 = n00 + (n10 - n00) * fx
    nx1 = n01 + (n11 - n01) * fx
    return nx0 + (nx1 - nx0) * fy


def polyline(u: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    segs = pts[1:] - pts[:-1]
    lengths = np.sqrt((segs * segs).sum(axis=1)) + 1e-6
    total = float(lengths.sum())
    dist = u * total
    acc = np.concatenate([[0.0], np.cumsum(lengths)])
    idx = np.searchsorted(acc, dist, side="right") - 1
    idx = np.clip(idx, 0, len(segs) - 1)
    local = (dist - acc[idx]) / lengths[idx]
    x = pts[idx, 0] + segs[idx, 0] * local
    y = pts[idx, 1] + segs[idx, 1] * local
    return x.astype(np.float32), y.astype(np.float32)


def circle(u: np.ndarray, cx: float, cy: float, r: float) -> tuple[np.ndarray, np.ndarray]:
    ang = u * TAU
    return (cx + np.cos(ang) * r).astype(np.float32), (cy + np.sin(ang) * r).astype(np.float32)


def orb_targets(ctx: ShapeContext, scale: float = 1.0, calm: bool = False) -> tuple[np.ndarray, np.ndarray]:
    t = ctx.t
    theta = ctx.u * TAU + t * 0.35
    r = (0.34 + 0.08 * ctx.seed) * scale
    r *= 1.0 + 0.06 * np.sin(t * 1.8 + ctx.seed * 9)
    if not calm:
        r *= 1.0 + 0.04 * ctx.level
    x = r * np.cos(theta)
    y = r * np.sin(theta) * 0.96
    return x.astype(np.float32), y.astype(np.float32)


def happy_lift(x: np.ndarray, y: np.ndarray, ctx: ShapeContext) -> tuple[np.ndarray, np.ndarray]:
    t = ctx.t
    u = ctx.u
    climb = (u - 0.5) * 0.15
    y = (y - 0.35 * ((t * 1.7 + u) % 1.0) - climb).astype(np.float32)
    return x, y


class ProceduralGenerator:
    def __init__(self, name: str) -> None:
        self.name = name

    def generate(self, count: int, ctx: ShapeContext) -> ShapeResult:
        del count
        scale = 1.0
        calm = ctx.muted or ctx.mode in ("muted", "sleeping")
        if ctx.mode == "sleeping":
            scale = 0.58
        elif ctx.mode == "muted":
            scale = 0.72
        x, y = effect_targets(ctx, scale, calm)
        if ctx.mode == "speaking" and ctx.expr != "chaotic":
            open_n = ctx.viseme
            vib = (0.03 + 0.14 * open_n) * np.sin(ctx.t * 38.0 + ctx.i * 0.37)
            y = (y + vib).astype(np.float32)
        if ctx.expr == "happy":
            x, y = happy_lift(x, y, ctx)
        region = np.zeros(x.shape[0], np.int16)
        return ShapeResult(x=x, y=y, region=region)
