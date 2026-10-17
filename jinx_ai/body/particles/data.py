"""Structure-of-arrays particle pool. Persistent — no per-frame alloc."""

from __future__ import annotations

from typing import Any

import numpy as np

CYAN = 0
PINK = 1


class ParticleData:
    def __init__(self, count: int, seed: int = 11) -> None:
        n = max(16, int(count))
        rng = np.random.default_rng(seed)
        self.seed_int = int(seed)
        self.n = n
        self.x = rng.normal(0.0, 0.18, n).astype(np.float32)
        self.y = rng.normal(0.0, 0.18, n).astype(np.float32)
        self.vx = np.zeros(n, np.float32)
        self.vy = np.zeros(n, np.float32)
        self.tx = self.x.copy()
        self.ty = self.y.copy()
        self.bx = self.x.copy()  # primary body target (pre-offset)
        self.by = self.y.copy()
        self.region = np.zeros(n, np.int16)
        self.size = rng.uniform(0.9, 2.2, n).astype(np.float32)
        self.alpha = np.ones(n, np.float32)
        self.energy = np.ones(n, np.float32)
        self.noise_phase = rng.random(n).astype(np.float32)
        self.life = rng.random(n).astype(np.float32)
        self.max_life = rng.uniform(0.55, 1.45, n).astype(np.float32)
        self.kind = np.zeros(n, np.int8)
        self.kind[::5] = PINK
        self.seed = rng.random(n).astype(np.float32)
        self.i = np.arange(n, dtype=np.float32)
        self.u = self.i / float(max(n, 1))
        self.visible = np.ones(n, np.uint8)
        self.trail_x: np.ndarray | None = None
        self.trail_y: np.ndarray | None = None
        self.trail_len = 0

    def resize(self, count: int) -> None:
        n = max(16, int(count))
        if n == self.n:
            return
        old = self.n
        rng = np.random.default_rng(self.seed_int + n)

        def grow(arr: np.ndarray, fill) -> np.ndarray:
            out = np.empty(n, dtype=arr.dtype)
            take = min(old, n)
            out[:take] = arr[:take]
            if n > old:
                if callable(fill):
                    out[old:] = fill(n - old)
                else:
                    out[old:] = fill
            return out

        self.x = grow(self.x, lambda k: rng.normal(0.0, 0.18, k).astype(np.float32))
        self.y = grow(self.y, lambda k: rng.normal(0.0, 0.18, k).astype(np.float32))
        self.vx = grow(self.vx, np.float32(0))
        self.vy = grow(self.vy, np.float32(0))
        self.tx = grow(self.tx, np.float32(0))
        self.ty = grow(self.ty, np.float32(0))
        self.bx = grow(self.bx, np.float32(0))
        self.by = grow(self.by, np.float32(0))
        self.region = grow(self.region, np.int16(0))
        self.size = grow(self.size, lambda k: rng.uniform(0.9, 2.2, k).astype(np.float32))
        self.alpha = grow(self.alpha, np.float32(1))
        self.energy = grow(self.energy, np.float32(1))
        self.noise_phase = grow(self.noise_phase, lambda k: rng.random(k).astype(np.float32))
        self.life = grow(self.life, lambda k: rng.random(k).astype(np.float32))
        self.max_life = grow(self.max_life, lambda k: rng.uniform(0.55, 1.45, k).astype(np.float32))
        self.kind = grow(self.kind, np.int8(0))
        if n > old:
            self.kind[old::5] = PINK
        self.seed = grow(self.seed, lambda k: rng.random(k).astype(np.float32))
        self.i = np.arange(n, dtype=np.float32)
        self.u = self.i / float(n)
        self.visible = grow(self.visible, np.uint8(1))
        self.n = n
        self.set_trail_length(self.trail_len)

    def set_trail_length(self, length: int) -> None:
        k = max(0, min(12, int(length)))
        self.trail_len = k
        if k <= 0:
            self.trail_x = None
            self.trail_y = None
            return
        self.trail_x = np.repeat(self.x[None, :], k, axis=0)
        self.trail_y = np.repeat(self.y[None, :], k, axis=0)

    def snapshot(self) -> dict[str, Any]:
        life = np.clip(self.life / np.maximum(self.max_life, 1e-4), 0.0, 1.0)
        return {
            "x": self.x,
            "y": self.y,
            "life": life,
            "size": self.size,
            "kind": self.kind,
            "alpha": self.alpha,
            "energy": self.energy,
            "region": self.region,
            "visible": self.visible,
            "tx": self.tx,
            "ty": self.ty,
            "trail_x": self.trail_x,
            "trail_y": self.trail_y,
            "trail_len": self.trail_len,
        }
