"""Target resampling, capsule sampling, morph lerp. Shape-agnostic."""

from __future__ import annotations

import numpy as np

from ..skeleton import Skeleton


def smooth01(t: float) -> float:
    x = min(1.0, max(0.0, float(t)))
    return x * x * (3.0 - 2.0 * x)


def resample_xy(
    src_x: np.ndarray,
    src_y: np.ndarray,
    n: int,
    rng: np.random.Generator | None = None,
    src_region: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Map any sample count onto ``n`` slots. Prefer even spacing over random."""
    n = max(1, int(n))
    m = int(src_x.shape[0])
    if m <= 0:
        z = np.zeros(n, np.float32)
        return z, z.copy(), np.zeros(n, np.int16)
    if m == n:
        reg = src_region.astype(np.int16) if src_region is not None else np.zeros(n, np.int16)
        return src_x.astype(np.float32), src_y.astype(np.float32), reg
    idx = (np.linspace(0, m - 1, n)).astype(np.int32)
    x = src_x[idx].astype(np.float32)
    y = src_y[idx].astype(np.float32)
    if m < n and rng is not None:
        # Extra copies get a tiny radial offset so they add volume, not a stack.
        jitter = rng.normal(0.0, 0.012, n).astype(np.float32)
        ang = rng.random(n).astype(np.float32) * np.float32(np.pi * 2)
        x = x + np.cos(ang) * jitter
        y = y + np.sin(ang) * jitter
    if src_region is not None:
        reg = src_region[idx].astype(np.int16)
    else:
        reg = np.zeros(n, np.int16)
    return x, y, reg


def lerp_targets(
    ax: np.ndarray,
    ay: np.ndarray,
    bx: np.ndarray,
    by: np.ndarray,
    t: float,
) -> tuple[np.ndarray, np.ndarray]:
    u = np.float32(smooth01(t))
    return (ax + (bx - ax) * u).astype(np.float32), (ay + (by - ay) * u).astype(np.float32)


def sample_capsule(
    ox: float,
    oy: float,
    tip_x: float,
    tip_y: float,
    width: float,
    count: int,
    rng: np.random.Generator,
    along: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    n = max(1, int(count))
    if along is None:
        along = rng.random(n).astype(np.float32)
        along = along * along * (3.0 - 2.0 * along)
        # Slight bunching toward joints, not a ladder.
        along = np.clip(along * 0.92 + rng.random(n).astype(np.float32) * 0.08, 0.0, 1.0)
    dx = tip_x - ox
    dy = tip_y - oy
    length = float(np.hypot(dx, dy)) + 1e-6
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    # Round caps: radial disk near the ends.
    rad = rng.standard_normal(n).astype(np.float32) * 0.42
    rad = np.clip(rad, -1.15, 1.15) * float(width) * 0.5
    taper = 0.75 + 0.25 * np.sin(along * np.pi)
    x = ox + dx * along + px * rad * taper
    y = oy + dy * along + py * rad * taper
    # Disks at joints so the silhouette has volume, not a stick.
    cap = rng.random(n) < 0.12
    if np.any(cap):
        ang = rng.random(int(cap.sum())).astype(np.float32) * np.float32(np.pi * 2)
        rr = rng.random(int(cap.sum())).astype(np.float32) ** 0.5 * float(width) * 0.55
        which = rng.random(int(cap.sum())) < 0.5
        cx = np.where(which, ox, tip_x)
        cy = np.where(which, oy, tip_y)
        x[cap] = cx + np.cos(ang) * rr
        y[cap] = cy + np.sin(ang) * rr
    return x.astype(np.float32), y.astype(np.float32)


def sample_circle(
    cx: float,
    cy: float,
    radius: float,
    count: int,
    rng: np.random.Generator,
    fill: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    n = max(1, int(count))
    ang = rng.random(n).astype(np.float32) * np.float32(np.pi * 2)
    if fill:
        r = np.sqrt(rng.random(n).astype(np.float32)) * float(radius)
        # Slightly denser rim so the head reads as a sphere, not a blob.
        r = np.where(rng.random(n) < 0.28, float(radius) * (0.82 + 0.18 * rng.random(n)), r)
    else:
        r = np.full(n, float(radius), np.float32)
    return (cx + np.cos(ang) * r).astype(np.float32), (cy + np.sin(ang) * r).astype(np.float32)


def sample_rect(
    cx: float,
    cy: float,
    w: float,
    h: float,
    count: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    n = max(1, int(count))
    x = (rng.random(n).astype(np.float32) - 0.5) * float(w)
    y = (rng.random(n).astype(np.float32) - 0.5) * float(h)
    return (cx + x).astype(np.float32), (cy + y).astype(np.float32)


REGION_IDS = {
    "head": 1,
    "neck": 2,
    "torso": 3,
    "pelvis": 4,
    "left_arm": 5,
    "right_arm": 6,
    "left_hand": 7,
    "right_hand": 8,
    "left_leg": 9,
    "right_leg": 10,
    "left_foot": 11,
    "right_foot": 12,
    "body": 13,
    "hair": 14,
    "cloud": 0,
}


def region_id(name: str) -> int:
    return REGION_IDS.get(str(name), 0)


def sample_skeleton(
    skeleton: Skeleton,
    count: int,
    rng: np.random.Generator,
    head_radius: float = 0.125,
    blocky: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    enabled = [b for b in skeleton.bones if b.enabled and (b.length > 1e-4 or b.name in ("head", "pelvis") or b.region == "hair")]
    if not enabled:
        z = np.zeros(count, np.float32)
        return z, z.copy(), np.zeros(count, np.int16)
    weights = np.array([max(0.05, b.weight) for b in enabled], dtype=np.float64)
    if skeleton.has("head"):
        # Extra budget for the cranial disk so it is not a bone nub.
        for i, b in enumerate(enabled):
            if b.name == "head":
                weights[i] *= 1.8
    weights = weights / weights.sum()
    raw = weights * count
    shares = np.maximum(1, np.round(raw).astype(np.int32))
    # Fix rounding so we land on exactly ``count``.
    while int(shares.sum()) > count:
        shares[int(np.argmax(shares))] -= 1
    while int(shares.sum()) < count:
        shares[int(np.argmax(weights))] += 1
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    rs: list[np.ndarray] = []
    for bone, n in zip(enabled, shares, strict=True):
        n = int(max(0, n))
        if n <= 0:
            continue
        rid = np.int16(region_id(bone.region))
        if bone.name == "head":
            hx = 0.5 * (bone.ox + bone.tip_x)
            hy = 0.5 * (bone.oy + bone.tip_y)
            if blocky:
                px, py = sample_rect(hx, hy - head_radius * 0.15, head_radius * 1.7, head_radius * 1.7, n, rng)
            else:
                px, py = sample_circle(hx, hy - head_radius * 0.15, head_radius, n, rng)
        elif bone.region == "hair" and bone.length < 0.02:
            px, py = sample_circle(bone.ox, bone.oy - bone.width * 0.15, bone.width * 0.55, n, rng)
        elif bone.name == "pelvis" and bone.length < 0.02:
            px, py = sample_circle(bone.ox, bone.oy, bone.width * 0.55, n, rng)
        else:
            px, py = sample_capsule(bone.ox, bone.oy, bone.tip_x, bone.tip_y, bone.width, n, rng)
        xs.append(px)
        ys.append(py)
        rs.append(np.full(n, rid, np.int16))
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    region = np.concatenate(rs)
    return resample_xy(x, y, count, rng, region)
