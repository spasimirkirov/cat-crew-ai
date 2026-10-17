"""Particle glyphs for the orb: smile, heart, brain, star, neurons.

Targets only. The overlay still paints dots; these functions never draw sprites.
"""

from __future__ import annotations

import math

import numpy as np

from .base import ShapeContext

TAU = math.tau


def _fade(x: np.ndarray) -> np.ndarray:
    return x * x * (3.0 - 2.0 * x)


def _value_noise(x: np.ndarray, y: np.ndarray) -> np.ndarray:
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


def _polyline(u: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
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


def _circle(u: np.ndarray, cx: float, cy: float, r: float) -> tuple[np.ndarray, np.ndarray]:
    ang = u * TAU
    return (cx + np.cos(ang) * r).astype(np.float32), (cy + np.sin(ang) * r).astype(np.float32)


def _orb(ctx: ShapeContext, scale: float = 0.92) -> tuple[np.ndarray, np.ndarray]:
    t = ctx.t
    theta = ctx.u * TAU + t * 0.35
    r = (0.34 + 0.08 * ctx.seed) * scale
    r *= 1.0 + 0.06 * np.sin(t * 1.8 + ctx.seed * 9)
    if not (ctx.muted or ctx.mode in ("muted", "sleeping")):
        r *= 1.0 + 0.04 * ctx.level
    x = r * np.cos(theta)
    y = r * np.sin(theta) * 0.96
    return x.astype(np.float32), y.astype(np.float32)

_SMILE_ARC = np.array(
    [[-0.22, 0.10], [-0.14, 0.20], [0.00, 0.26], [0.14, 0.20], [0.22, 0.10]],
    dtype=np.float32,
)

_STAR = np.array(
    [
        [0.00, -0.42],
        [0.10, -0.12],
        [0.40, -0.12],
        [0.16, 0.08],
        [0.25, 0.38],
        [0.00, 0.20],
        [-0.25, 0.38],
        [-0.16, 0.08],
        [-0.40, -0.12],
        [-0.10, -0.12],
        [0.00, -0.42],
    ],
    dtype=np.float32,
)

_NEURON_NODES = np.array(
    [
        [-0.22, -0.18],
        [0.20, -0.22],
        [0.00, -0.02],
        [-0.28, 0.08],
        [0.26, 0.10],
        [-0.08, 0.22],
        [0.14, 0.26],
        [-0.18, -0.34],
        [0.08, -0.36],
        [0.00, 0.36],
    ],
    dtype=np.float32,
)

_NEURON_EDGES = (
    (0, 2),
    (1, 2),
    (0, 3),
    (1, 4),
    (2, 5),
    (2, 6),
    (3, 5),
    (4, 6),
    (0, 7),
    (1, 8),
    (5, 9),
    (6, 9),
    (7, 2),
    (8, 2),
)


def _halo(gx: np.ndarray, gy: np.ndarray, ctx: ShapeContext, mix: float = 0.16) -> tuple[np.ndarray, np.ndarray]:
    ox, oy = _orb(ctx, scale=0.92)
    pick = ctx.u < mix
    return np.where(pick, ox, gx).astype(np.float32), np.where(pick, oy, gy).astype(np.float32)


def smile_targets(ctx: ShapeContext) -> tuple[np.ndarray, np.ndarray]:
    u = ctx.u
    t = ctx.t
    head_u = np.clip(u / 0.48, 0, 1)
    hx, hy = _circle(head_u, 0.0, 0.02, 0.36 + 0.012 * math.sin(t * 1.6))
    eye_u = np.clip((u - 0.48) / 0.22, 0, 1)
    elx, ely = _circle(eye_u, -0.11, -0.08, 0.045)
    erx, ery = _circle(eye_u, 0.11, -0.08, 0.045)
    mouth_u = np.clip((u - 0.70) / 0.30, 0, 1)
    mx, my = _polyline(mouth_u, _SMILE_ARC)
    x = np.where(u < 0.48, hx, np.where(u < 0.59, elx, np.where(u < 0.70, erx, mx)))
    y = np.where(u < 0.48, hy, np.where(u < 0.59, ely, np.where(u < 0.70, ery, my)))
    open_n = 0.04 + 0.10 * ctx.viseme
    y = y + np.where(u >= 0.70, open_n * (0.5 + 0.5 * np.sin(t * 18.0)), 0.0)
    return _halo(x.astype(np.float32), y.astype(np.float32), ctx)


def heart_targets(ctx: ShapeContext) -> tuple[np.ndarray, np.ndarray]:
    t = ctx.t
    ang = ctx.u * TAU
    beat = 1.0 + 0.06 * ctx.level + 0.05 * np.sin(t * 2.6)
    x = (np.sin(ang) ** 3) * 0.36 * beat
    y = (
        -(
            13.0 * np.cos(ang)
            - 5.0 * np.cos(2.0 * ang)
            - 2.0 * np.cos(3.0 * ang)
            - np.cos(4.0 * ang)
        )
        / 32.0
        * 0.72
        * beat
    )
    nx = _value_noise(x * 2.2 + t * 0.2, y * 2.2)
    x = x * (0.94 + 0.10 * nx)
    y = y * (0.94 + 0.10 * nx)
    return _halo(x.astype(np.float32), y.astype(np.float32), ctx, mix=0.12)


def star_targets(ctx: ShapeContext) -> tuple[np.ndarray, np.ndarray]:
    t = ctx.t
    spin = ctx.u * TAU + t * (1.1 if ctx.mode == "error" else 0.35)
    sx, sy = _polyline(np.mod(ctx.u + t * 0.04, 1.0), _STAR)
    c, s = math.cos(spin.mean() * 0.0 + t * (2.4 if ctx.mode == "error" else 0.55)), math.sin(
        t * (2.4 if ctx.mode == "error" else 0.55)
    )
    x = sx * c - sy * s
    y = sx * s + sy * c
    if ctx.mode == "error":
        x = x + 0.08 * np.sign(np.sin(t * 26 + ctx.i))
    return _halo(x.astype(np.float32), y.astype(np.float32), ctx, mix=0.14)


def brain_targets(ctx: ShapeContext) -> tuple[np.ndarray, np.ndarray]:
    t = ctx.t
    u = ctx.u
    lobe = np.where(u < 0.5, -1.0, 1.0).astype(np.float32)
    local = np.where(u < 0.5, u * 2.0, (u - 0.5) * 2.0)
    ang = local * TAU + t * 0.4
    rx = 0.22 + 0.04 * np.sin(ang * 3.0 + t)
    ry = 0.28 + 0.05 * np.sin(ang * 2.0 - t * 0.8)
    fold = 0.045 * np.sin(ang * 6.0 + t * 1.7)
    x = lobe * (0.16 + rx * (0.55 + 0.45 * np.cos(ang))) + fold * lobe
    y = -0.02 + ry * np.sin(ang) * 0.85 + 0.03 * np.sin(ang * 5.0)
    nx = _value_noise(x * 3.0 + t * 0.15, y * 3.0)
    x = x * (0.92 + 0.12 * nx)
    y = y * (0.92 + 0.12 * nx)
    return x.astype(np.float32), y.astype(np.float32)


def neuron_targets(ctx: ShapeContext) -> tuple[np.ndarray, np.ndarray]:
    u = ctx.u
    t = ctx.t
    n_nodes = len(_NEURON_NODES)
    n_edges = len(_NEURON_EDGES)
    pulse = 1.0 + 0.04 * np.sin(t * 3.4 + ctx.seed * 6)
    node_frac = 0.28
    on_node = u < node_frac
    node_u = np.clip(u / max(node_frac, 1e-6), 0, 1)
    node_idx = np.clip((node_u * n_nodes).astype(np.int32), 0, n_nodes - 1)
    nx = _NEURON_NODES[node_idx, 0] * pulse
    ny = _NEURON_NODES[node_idx, 1] * pulse
    jitter = 0.028 + 0.01 * np.sin(t * 5.0 + ctx.i)
    nx_, ny_ = _circle(ctx.seed, 0.0, 0.0, 1.0)
    nx = nx + nx_ * jitter
    ny = ny + ny_ * jitter * 0.9
    edge_u = np.clip((u - node_frac) / max(1.0 - node_frac, 1e-6), 0, 1)
    edge_idx = np.clip((edge_u * n_edges).astype(np.int32), 0, n_edges - 1)
    a = np.array([_NEURON_EDGES[i][0] for i in edge_idx], dtype=np.int32)
    b = np.array([_NEURON_EDGES[i][1] for i in edge_idx], dtype=np.int32)
    local = np.mod(edge_u * n_edges, 1.0)
    travel = 0.15 * np.sin(t * 2.8 + ctx.seed * 8)
    local = np.clip(local + travel, 0.0, 1.0)
    ex = (_NEURON_NODES[a, 0] + (_NEURON_NODES[b, 0] - _NEURON_NODES[a, 0]) * local) * pulse
    ey = (_NEURON_NODES[a, 1] + (_NEURON_NODES[b, 1] - _NEURON_NODES[a, 1]) * local) * pulse
    x = np.where(on_node, nx, ex)
    y = np.where(on_node, ny, ey)
    return x.astype(np.float32), y.astype(np.float32)


def think_brain_targets(ctx: ShapeContext) -> tuple[np.ndarray, np.ndarray]:
    bx, by = brain_targets(ctx)
    nx, ny = neuron_targets(ctx)
    pick = ctx.u < 0.58
    x = np.where(pick, bx, nx)
    y = np.where(pick, by, ny)
    return _halo(x.astype(np.float32), y.astype(np.float32), ctx, mix=0.10)


def glyph_for(ctx: ShapeContext) -> str:
    """Which orb glyph the current overlay mode / expression should show."""
    if ctx.expr == "happy":
        return "smile"
    if ctx.expr == "chaotic":
        return "neurons"
    mode = ctx.mode
    if mode == "thinking":
        return "brain"
    if mode == "listening":
        return "heart"
    if mode == "speaking":
        return "smile"
    if mode == "tool":
        return "star"
    if mode == "error":
        return "star"
    return "orb"


def effect_targets(ctx: ShapeContext, scale: float, calm: bool) -> tuple[np.ndarray, np.ndarray]:
    kind = glyph_for(ctx)
    if kind == "smile":
        return smile_targets(ctx)
    if kind == "heart":
        return heart_targets(ctx)
    if kind == "star":
        return star_targets(ctx)
    if kind == "brain":
        return think_brain_targets(ctx)
    if kind == "neurons":
        return _halo(*neuron_targets(ctx), ctx, mix=0.12)
    return _orb(ctx, scale)
