"""Shape generators produce target clouds. No rendering, no animation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..skeleton import Skeleton


@dataclass
class ShapeResult:
    x: np.ndarray
    y: np.ndarray
    region: np.ndarray


@dataclass
class ShapeContext:
    t: float
    mode: str
    level: float
    viseme: float
    muted: bool
    expr: str
    glyph: str
    seed: np.ndarray
    u: np.ndarray
    i: np.ndarray
    skeleton: Skeleton | None = None
    head_radius: float = 0.125
    blocky: bool = False
    rng: np.random.Generator | None = None


class ShapeGenerator(Protocol):
    name: str

    def generate(self, count: int, ctx: ShapeContext) -> ShapeResult: ...
