"""Procedural particle body. Skeleton is hidden; particles are the material."""

from .controller import ALL_SHAPES, FIGURE_SHAPES, BodyController, ParticleStyle
from .emotion import BodyEmotionController
from .ik import solve_two_bone
from .state import BodyState

__all__ = [
    "ALL_SHAPES",
    "FIGURE_SHAPES",
    "BodyController",
    "BodyEmotionController",
    "BodyState",
    "ParticleStyle",
    "solve_two_bone",
]
