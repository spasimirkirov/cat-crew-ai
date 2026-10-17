"""Bind rest-pose samples to bones so a silhouette can still wave."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..skeleton import Skeleton


@dataclass
class SkinBind:
    names: np.ndarray
    lx: np.ndarray
    ly: np.ndarray
    region: np.ndarray
    n: int


def _capsule_dist(px: np.ndarray, py: np.ndarray, ox: float, oy: float, tx: float, ty: float) -> np.ndarray:
    dx = tx - ox
    dy = ty - oy
    length2 = dx * dx + dy * dy + 1e-8
    t = np.clip(((px - ox) * dx + (py - oy) * dy) / length2, 0.0, 1.0)
    cx = ox + t * dx
    cy = oy + t * dy
    return np.sqrt((px - cx) ** 2 + (py - cy) ** 2)


def bind_points(x: np.ndarray, y: np.ndarray, region: np.ndarray, skeleton: Skeleton) -> SkinBind:
    bones = [b for b in skeleton.bones if b.enabled]
    if not bones:
        n = int(x.shape[0])
        return SkinBind(
            names=np.array(["root"] * n, dtype=object),
            lx=x.astype(np.float32),
            ly=y.astype(np.float32),
            region=region.astype(np.int16),
            n=n,
        )
    n = int(x.shape[0])
    best = np.full(n, 1e9, np.float32)
    idx = np.zeros(n, np.int32)
    for i, bone in enumerate(bones):
        if bone.name == "head" or bone.length < 0.03:
            d = np.sqrt((x - bone.ox) ** 2 + (y - bone.oy) ** 2)
        else:
            d = _capsule_dist(x, y, bone.ox, bone.oy, bone.tip_x, bone.tip_y)
        closer = d < best
        best[closer] = d[closer]
        idx[closer] = i
    names = np.empty(n, dtype=object)
    lx = np.empty(n, np.float32)
    ly = np.empty(n, np.float32)
    for i, bone in enumerate(bones):
        sel = idx == i
        if not np.any(sel):
            continue
        names[sel] = bone.name
        c, s = float(np.cos(bone.wangle)), float(np.sin(bone.wangle))
        dx = x[sel] - bone.ox
        dy = y[sel] - bone.oy
        lx[sel] = (c * dx + s * dy).astype(np.float32)
        ly[sel] = (-s * dx + c * dy).astype(np.float32)
    return SkinBind(names=names, lx=lx, ly=ly, region=region.astype(np.int16), n=n)


def apply_skin(bind: SkinBind, skeleton: Skeleton) -> tuple[np.ndarray, np.ndarray]:
    x = np.empty(bind.n, np.float32)
    y = np.empty(bind.n, np.float32)
    for name in np.unique(bind.names):
        sel = bind.names == name
        if not np.any(sel):
            continue
        if skeleton.has(str(name)):
            bone = skeleton.bone(str(name))
        else:
            bone = skeleton.bones[0]
        c, s = float(np.cos(bone.wangle)), float(np.sin(bone.wangle))
        x[sel] = (bone.ox + c * bind.lx[sel] - s * bind.ly[sel]).astype(np.float32)
        y[sel] = (bone.oy + s * bind.lx[sel] + c * bind.ly[sel]).astype(np.float32)
    return x, y
