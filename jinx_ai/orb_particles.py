"""Jinx particle field — overlay facade over the particle body engine.

Stay in NumPy + sines/value-noise. The overlay paints whatever snapshot()
returns. Modes morph the attractor; set_expression() parks particles on a
graffiti glyph for a beat, then lets them blow apart.
"""

from __future__ import annotations

from typing import Any

from .body.controller import ALL_SHAPES, FIGURE_SHAPES, BodyController
from .body.particles.data import CYAN, PINK
from .body.shapes.procedural import value_noise

_STYLES = frozenset(ALL_SHAPES)
_MODES = frozenset(
    {"idle", "listening", "thinking", "speaking", "tool", "error", "muted", "sleeping"}
)


class ParticleField:
    """Pooled particles in unit space, origin at the overlay center."""

    def __init__(self, count: int = 240) -> None:
        self.body = BodyController(count=count)
        self._bind()

    def _bind(self) -> None:
        p = self.body.particles
        self.n = p.n
        self.x = p.x
        self.y = p.y
        self.vx = p.vx
        self.vy = p.vy
        self.life = p.life
        self.max_life = p.max_life
        self.size = p.size
        self.kind = p.kind
        self.seed = p.seed
        self.i = p.i
        self.u = p.u
        self.tx = p.tx
        self.ty = p.ty

    @property
    def t(self) -> float:
        return self.body.t

    @t.setter
    def t(self, value: float) -> None:
        self.body.t = float(value)

    @property
    def mode(self) -> str:
        return self.body.mode

    @mode.setter
    def mode(self, value: str) -> None:
        self.body.mode = str(value)

    @property
    def style(self) -> str:
        return self.body.shape

    @style.setter
    def style(self, value: str) -> None:
        self.body.shape = str(value)

    @property
    def level(self) -> float:
        return self.body.level

    @level.setter
    def level(self, value: float) -> None:
        self.body.level = float(value)

    @property
    def viseme(self) -> float:
        return self.body.viseme

    @viseme.setter
    def viseme(self, value: float) -> None:
        self.body.viseme = float(value)

    @property
    def muted(self) -> bool:
        return self.body.muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self.body.muted = bool(value)

    @property
    def expr(self) -> str:
        return self.body.expr

    @expr.setter
    def expr(self, value: str) -> None:
        self.body.expr = str(value)

    @property
    def expr_left(self) -> float:
        return self.body.expr_left

    @expr_left.setter
    def expr_left(self, value: float) -> None:
        self.body.expr_left = float(value)

    def set_mode(self, name: str) -> None:
        key = name if name in _MODES else "idle"
        self.body.set_mode(key)

    def set_style(self, name: str) -> None:
        key = (name or "orb").strip().lower()
        if key not in _STYLES:
            key = "orb"
        if key in FIGURE_SHAPES:
            self.body.ensure_count(max(self.n, 560))
        self.body.set_shape(key, morph=0.0 if self.body.t < 0.05 else None)
        self._bind()

    def set_muted(self, muted: bool) -> None:
        self.body.set_muted(muted)

    def set_level(self, value: float) -> None:
        self.body.set_level(value)

    def set_viseme(self, open_n: float) -> None:
        self.body.set_viseme(open_n)

    def set_expression(self, emotion: str, duration: float = 1.7) -> None:
        self.body.set_expression(emotion, duration)

    def update(self, dt: float) -> None:
        self.body.update(dt)
        self._bind()

    def snapshot(self) -> dict[str, Any]:
        return self.body.snapshot()

    def play(self, name: str, **kwargs) -> None:
        self.body.play(name, **kwargs)

    def morph_to(self, name: str, duration: float = 1.5) -> None:
        self.body.morph_to(name, duration)
        self._bind()
