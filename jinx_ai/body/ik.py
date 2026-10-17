"""Minimal 2-bone IK. No third-party solver."""

from __future__ import annotations

import math

import numpy as np

_EPS = 1e-4


def solve_two_bone(
    start: np.ndarray,
    target: np.ndarray,
    upper: float,
    lower: float,
    bend: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (mid, end) for a two-bone chain.

    Targets outside [abs(L1-L2), L1+L2] are clamped onto the reachable ring.
    ``bend`` selects which side the elbow/knee folds (+1 or -1).
    """
    sx = float(start[0])
    sy = float(start[1])
    tx = float(target[0])
    ty = float(target[1])
    l1 = max(_EPS, float(upper))
    l2 = max(_EPS, float(lower))
    dx = tx - sx
    dy = ty - sy
    dist = math.hypot(dx, dy)
    reach = l1 + l2
    min_r = abs(l1 - l2) + _EPS
    if dist < _EPS:
        dx, dy, dist = 1.0, 0.0, _EPS
    clamped = min(reach, max(min_r, dist))
    ux = dx / dist
    uy = dy / dist
    # Project onto the reachable segment, then offset perpendicular for the joint.
    a = (l1 * l1 - l2 * l2 + clamped * clamped) / (2.0 * clamped)
    a = min(l1, max(-l1, a))
    h2 = max(0.0, l1 * l1 - a * a)
    h = math.sqrt(h2) * (1.0 if bend >= 0 else -1.0)
    px, py = -uy, ux
    mid = np.array([sx + ux * a + px * h, sy + uy * a + py * h], dtype=np.float32)
    end = np.array([sx + ux * clamped, sy + uy * clamped], dtype=np.float32)
    return mid, end


def angle_of(origin: np.ndarray, tip: np.ndarray) -> float:
    return math.atan2(float(tip[1] - origin[1]), float(tip[0] - origin[0]))
