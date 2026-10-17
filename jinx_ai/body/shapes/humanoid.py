"""Humanoid target cloud: female silhouette skinned to the 2D rig, plus hair."""

from __future__ import annotations

import numpy as np

from ..particles.targets import region_id
from .base import ShapeContext, ShapeResult
from .pictograms import figure_mask
from .silhouette import map_mask_to_box
from .skin import SkinBind, apply_skin, bind_points


class HumanoidGenerator:
    name = "humanoid"

    def __init__(self, kind: str = "humanoid") -> None:
        self.name = kind
        self.kind = "humanoid"
        self.blocky = False
        self._bind: SkinBind | None = None
        self._n = 0

    def invalidate(self) -> None:
        self._bind = None

    def generate(self, count: int, ctx: ShapeContext) -> ShapeResult:
        skel = ctx.skeleton
        rng = ctx.rng or np.random.default_rng(11)
        n = max(1, int(count))
        if skel is None:
            z = np.zeros(n, np.float32)
            return ShapeResult(x=z, y=z.copy(), region=np.zeros(n, np.int16))
        if self._bind is None or self._n != n:
            saved = [(b.name, b.angle, b.width) for b in skel.bones]
            skel.reset_pose()
            skel.solve()
            x0, y0, x1, y1 = skel.bbox()
            half = max(abs(x0), abs(x1), 0.28)
            box = (-half - 0.10, y0 - 0.22, half + 0.12, y1 + 0.08)
            mask = figure_mask(self.kind, size=128)
            px, py = map_mask_to_box(mask, n, rng, box)
            region = np.zeros(n, np.int16)
            for bone in skel.bones:
                if bone.name == "head":
                    d = np.sqrt((px - bone.ox) ** 2 + (py - bone.oy) ** 2)
                    region[d < ctx.head_radius * 1.35] = region_id("head")
                elif bone.region == "hair":
                    d = np.sqrt((px - bone.ox) ** 2 + (py - bone.oy) ** 2)
                    hair = d < max(0.08, bone.width * 1.4)
                    region[hair] = region_id("hair")
            self._bind = bind_points(px, py, region, skel)
            self._n = n
            for name, angle, width in saved:
                if skel.has(name):
                    skel.bone(name).angle = angle
                    skel.bone(name).width = width
            skel.solve()
        x, y = apply_skin(self._bind, skel)
        region = self._bind.region.copy()
        hair_id = np.int16(region_id("hair"))
        for i, name in enumerate(self._bind.names):
            if str(name).startswith("hair"):
                region[i] = hair_id
        return ShapeResult(x=x, y=y, region=region)
