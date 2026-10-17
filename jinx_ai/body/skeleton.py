"""Lightweight 2D skeleton. Independent of particles."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .ik import angle_of, solve_two_bone


@dataclass
class Bone:
    name: str
    parent: str | None
    local_x: float = 0.0
    local_y: float = 0.0
    length: float = 0.0
    angle: float = 0.0
    rest_angle: float = 0.0
    width: float = 0.08
    rest_width: float = 0.08
    region: str = ""
    enabled: bool = True
    weight: float = 1.0
    ox: float = 0.0
    oy: float = 0.0
    tip_x: float = 0.0
    tip_y: float = 0.0
    wangle: float = 0.0


@dataclass
class Skeleton:
    bones: list[Bone] = field(default_factory=list)
    origin_x: float = 0.0
    origin_y: float = 0.0
    scale: float = 1.0
    root_angle: float = 0.0
    _index: dict[str, Bone] = field(default_factory=dict, repr=False)

    def rebuild_index(self) -> None:
        self._index = {b.name: b for b in self.bones}

    def bone(self, name: str) -> Bone:
        if not self._index:
            self.rebuild_index()
        return self._index[name]

    def has(self, name: str) -> bool:
        if not self._index:
            self.rebuild_index()
        return name in self._index

    def reset_pose(self) -> None:
        for bone in self.bones:
            bone.angle = bone.rest_angle
            bone.width = bone.rest_width

    def apply_angles(self, offsets: dict[str, float]) -> None:
        for name, delta in offsets.items():
            if self.has(name):
                bone = self.bone(name)
                bone.angle = bone.rest_angle + float(delta)

    def apply_widths(self, scales: dict[str, float]) -> None:
        for name, scale in scales.items():
            if self.has(name):
                bone = self.bone(name)
                bone.width = bone.rest_width * float(scale)

    def solve(self) -> None:
        """World transforms, parent-first. Bones must be defined in topo order."""
        if not self._index or len(self._index) != len(self.bones):
            self.rebuild_index()
        s = float(self.scale)
        for bone in self.bones:
            if bone.parent is None:
                px, py, pa = self.origin_x, self.origin_y, self.root_angle
            else:
                parent = self._index[bone.parent]
                px, py, pa = parent.tip_x, parent.tip_y, parent.wangle
            c, sng = math.cos(pa), math.sin(pa)
            lx, ly = bone.local_x * s, bone.local_y * s
            bone.ox = px + c * lx - sng * ly
            bone.oy = py + c * ly + sng * lx
            bone.wangle = pa + bone.angle
            length = bone.length * s if bone.enabled else 0.0
            bone.tip_x = bone.ox + math.cos(bone.wangle) * length
            bone.tip_y = bone.oy + math.sin(bone.wangle) * length

    def ik_chain(
        self,
        upper_name: str,
        lower_name: str,
        target: np.ndarray,
        bend: float = 1.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        upper = self.bone(upper_name)
        lower = self.bone(lower_name)
        start = np.array([upper.ox, upper.oy], dtype=np.float32)
        l1 = max(1e-4, upper.length * self.scale)
        l2 = max(1e-4, lower.length * self.scale)
        mid, end = solve_two_bone(start, target, l1, l2, bend)
        parent_angle = 0.0
        if upper.parent and self.has(upper.parent):
            parent_angle = self.bone(upper.parent).wangle
        upper.angle = angle_of(start, mid) - parent_angle
        lower.angle = angle_of(mid, end) - (parent_angle + upper.angle)
        self.solve()
        return mid, end

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [b.ox for b in self.bones] + [b.tip_x for b in self.bones]
        ys = [b.oy for b in self.bones] + [b.tip_y for b in self.bones]
        return min(xs), min(ys), max(xs), max(ys)

    def copy_pose(self) -> dict[str, float]:
        return {b.name: b.angle for b in self.bones}
