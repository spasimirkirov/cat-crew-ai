"""Shape generators."""

from .base import ShapeContext, ShapeGenerator, ShapeResult
from .humanoid import HumanoidGenerator
from .procedural import ProceduralGenerator, value_noise
from .silhouette import (
    SilhouetteGenerator,
    fox_mask,
    generate_from_mask,
    generate_from_points,
    generate_from_primitives,
)

__all__ = [
    "ShapeContext",
    "ShapeGenerator",
    "ShapeResult",
    "HumanoidGenerator",
    "ProceduralGenerator",
    "SilhouetteGenerator",
    "fox_mask",
    "generate_from_mask",
    "generate_from_points",
    "generate_from_primitives",
    "value_noise",
]
