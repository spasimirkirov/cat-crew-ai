"""Renderer protocol — simulation never imports QPainter."""

from __future__ import annotations

from typing import Any, Protocol


class ParticleRenderer(Protocol):
    def render(self, particles: dict[str, Any], painter, viewport) -> None: ...
