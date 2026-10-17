"""Emotion sliders. The LLM does not infer these yet — callers set them."""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class BodyEmotionController:
    happiness: float = 0.0
    sadness: float = 0.0
    excitement: float = 0.0
    fear: float = 0.0
    anger: float = 0.0
    calm: float = 0.0
    curiosity: float = 0.0
    energy: float = 0.45

    def set(self, name: str, value: float) -> None:
        key = str(name or "").strip().lower()
        if not hasattr(self, key):
            return
        setattr(self, key, min(1.0, max(0.0, float(value))))

    def as_dict(self) -> dict[str, float]:
        return {f.name: float(getattr(self, f.name)) for f in fields(self)}

    def cohesion_scale(self) -> float:
        c = 1.0
        c -= 0.25 * self.excitement
        c -= 0.45 * self.fear
        c += 0.15 * self.calm
        c -= 0.08 * self.happiness
        return min(1.2, max(0.12, c))

    def noise_scale(self) -> float:
        n = 1.0
        n += 1.4 * self.fear
        n += 0.5 * self.excitement
        n -= 0.4 * self.calm
        n += 0.2 * self.anger
        return max(0.15, n)

    def energy_scale(self) -> float:
        return min(1.8, max(0.2, 0.45 + 0.5 * self.energy + 0.4 * self.happiness + 0.5 * self.excitement - 0.35 * self.sadness))

    def glow_scale(self) -> float:
        return min(1.5, max(0.35, 1.0 + 0.25 * self.happiness + 0.3 * self.excitement - 0.4 * self.sadness - 0.2 * self.calm * 0.3))

    def pose_offsets(self) -> dict[str, float]:
        """Additive bone angle offsets."""
        return {
            "neck": 0.28 * self.sadness - 0.08 * self.happiness - 0.12 * self.curiosity + 0.15 * self.fear,
            "head": 0.22 * self.sadness - 0.06 * self.happiness + 0.1 * self.curiosity,
            "spine": 0.12 * self.sadness - 0.06 * self.happiness + 0.08 * self.fear,
            "l_upper_arm": 0.18 * self.sadness - 0.2 * self.happiness + 0.25 * self.fear,
            "r_upper_arm": -0.18 * self.sadness + 0.2 * self.happiness - 0.25 * self.fear,
        }
