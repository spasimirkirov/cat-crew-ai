"""Procedural clips + blending. Skeleton poses only — no particles."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import mixamo
from .skeleton import Skeleton

_CLIP_ONE_SHOT = frozenset({"wave", "point", "surprised", "turn", "sit", "stand"})
_HAIR = ("hair_l", "hair_r", "hair_tail", "hair_bang")


def _smooth(t: float) -> float:
    x = min(1.0, max(0.0, float(t)))
    return x * x * (3.0 - 2.0 * x)


@dataclass
class AnimParams:
    duration: float = 1.8
    arm: str = "right"
    amplitude: float = 0.8
    frequency: float = 5.0
    repetitions: float = 3.0


@dataclass
class AnimationController:
    name: str = "idle"
    previous: str = "idle"
    clip_t: float = 0.0
    blend: float = 1.0
    blend_dur: float = 0.35
    params: AnimParams = field(default_factory=AnimParams)
    speed: float = 1.0
    _weight_t: float = 0.0
    _weight_side: float = 1.0

    def transition_to(self, name: str, duration: float = 0.4, **kwargs) -> None:
        key = (name or "idle").strip().lower()
        if key == "stand":
            key = "idle"
        self.previous = self.name
        self.name = key
        self.clip_t = 0.0
        self.blend = 0.0
        self.blend_dur = max(0.12, float(duration))
        p = AnimParams()
        p.duration = float(kwargs.get("clip_duration", p.duration))
        p.arm = str(kwargs.get("arm", p.arm))
        p.amplitude = float(kwargs.get("amplitude", p.amplitude))
        p.frequency = float(kwargs.get("frequency", p.frequency))
        p.repetitions = float(kwargs.get("repetitions", p.repetitions))
        self.params = p

    def update(self, dt: float) -> None:
        dt = float(dt) * float(self.speed)
        self.clip_t += dt
        if self.blend < 1.0:
            self.blend = min(1.0, self.blend + dt / self.blend_dur)
        if self.name in _CLIP_ONE_SHOT:
            dur = max(0.4, self.params.duration)
            if self.clip_t >= dur:
                self.transition_to("idle", duration=0.35)

    def finished_one_shot(self) -> bool:
        return self.name == "idle" and self.previous in _CLIP_ONE_SHOT

    def apply(
        self,
        skeleton: Skeleton,
        t: float,
        *,
        speaking: bool = False,
        listening: bool = False,
        heartbeat_phase: float = 0.0,
        heartbeat_amp: float = 0.0,
    ) -> dict[str, np.ndarray]:
        """Write bone angles/widths, run IK, solve world xforms.

        Returns debug IK targets {name: (x,y)}.
        """
        skeleton.reset_pose()
        idle = self._idle(skeleton, t, heartbeat_phase, heartbeat_amp)
        clip = self._clip(skeleton, t)
        w = _smooth(self.blend)
        angles = {k: idle.get(k, 0.0) * (1.0 - w) + clip.get(k, idle.get(k, 0.0)) * w for k in set(idle) | set(clip)}
        mix_name = self.name
        if mix_name == "talking":
            mix_name = "idle"
        if mix_name == "wave" and self.params.arm == "both":
            mix_name = "wave_both"
        mix_t = self.clip_t if self.name in _CLIP_ONE_SHOT else t
        mix = mixamo.sample(mix_name, mix_t)
        if mix:
            one_shot = self.name in _CLIP_ONE_SHOT
            for name, delta in mix.items():
                if one_shot:
                    angles[name] = idle.get(name, 0.0) * (1.0 - w) + delta * w
                else:
                    angles[name] = idle.get(name, 0.0) + delta
        for name in _HAIR:
            if name in idle:
                angles[name] = idle[name]
        widths = self._idle_widths(t, heartbeat_amp)
        if self.name in ("sleep", "sad"):
            widths = {k: v * (0.92 if self.name == "sad" else 0.88) for k, v in widths.items()}
        skeleton.apply_angles(angles)
        skeleton.apply_widths(widths)
        skeleton.solve()
        ik_debug: dict[str, np.ndarray] = {}
        ik_w = w if self.name in ("wave", "think", "walk", "point", "sleep", "sit") else 0.0
        if mix and self.name in _CLIP_ONE_SHOT:
            ik_w = 0.0
        if listening and self.name == "idle":
            ik_w = 0.0
        if speaking and self.name == "idle":
            pass
        ik_debug.update(self._apply_ik(skeleton, t, ik_w))
        if listening:
            self._nudge(skeleton, {"neck": -0.18, "spine": -0.06})
            skeleton.solve()
        if speaking and self.name in ("idle", "talking"):
            self._nudge(skeleton, {"neck": 0.05 * math.sin(t * 9.0), "head": 0.04 * math.sin(t * 6.5)})
            skeleton.solve()
        return ik_debug

    def _nudge(self, skeleton: Skeleton, extra: dict[str, float]) -> None:
        for name, delta in extra.items():
            if skeleton.has(name):
                skeleton.bone(name).angle += delta

    def _idle(self, skeleton: Skeleton, t: float, hb_phase: float, hb_amp: float) -> dict[str, float]:
        del skeleton
        breath = math.sin(t * 1.45)
        sway = 0.045 * math.sin(t * 0.72)
        head = 0.07 * math.sin(t * 0.91 + 0.4)
        self._weight_t += 0.016
        # Occasional weight shift every ~4s.
        cycle = 0.5 + 0.5 * math.sin(t * 0.28)
        self._weight_side = -1.0 if cycle < 0.5 else 1.0
        hip = 0.035 * self._weight_side * (0.4 + 0.6 * abs(math.sin(t * 0.28)))
        pulse = hb_amp * math.sin(hb_phase)
        return {
            "spine": sway * 0.35 + pulse * 0.04,
            "chest": breath * 0.03,
            "neck": head * 0.6,
            "head": head,
            "l_upper_arm": 0.06 * breath + hip * 0.4,
            "r_upper_arm": -0.06 * breath - hip * 0.4,
            "l_upper_leg": hip * 0.5,
            "r_upper_leg": -hip * 0.5,
            "pelvis": 0.0,
            "hair_l": 0.12 * math.sin(t * 1.18) + 0.04 * breath,
            "hair_r": -0.12 * math.sin(t * 1.22 + 0.35) - 0.04 * breath,
            "hair_tail": 0.16 * math.sin(t * 0.92 + 0.2),
            "hair_bang": 0.07 * math.sin(t * 1.55),
        }

    def _idle_widths(self, t: float, hb_amp: float) -> dict[str, float]:
        breath = math.sin(t * 1.45)
        pulse = 1.0 + 0.045 * breath + hb_amp * 0.08 * (0.5 + 0.5 * math.sin(t * 1.45))
        return {
            "spine": pulse,
            "chest": 1.0 + 0.07 * breath + hb_amp * 0.12,
            "pelvis": 1.0 + 0.03 * breath,
        }

    def _clip(self, skeleton: Skeleton, t: float) -> dict[str, float]:
        name = self.name
        if name == "idle" or name == "talking":
            return self._idle(skeleton, t, 0.0, 0.0)
        if name == "wave":
            return self._wave_angles(t)
        if name == "think":
            return {
                "neck": 0.22,
                "head": 0.18,
                "spine": 0.08,
                "r_upper_arm": -0.35,
                "l_upper_arm": 0.12,
            }
        if name == "sleep":
            return {
                "spine": 0.55,
                "chest": 0.18,
                "neck": 0.65,
                "head": 0.4,
                "l_upper_arm": 0.7,
                "r_upper_arm": -0.7,
                "l_upper_leg": 0.45,
                "r_upper_leg": -0.45,
                "l_lower_leg": 0.5,
                "r_lower_leg": -0.5,
            }
        if name == "sit":
            return {
                "spine": 0.08,
                "l_upper_leg": 0.9,
                "r_upper_leg": -0.9,
                "l_lower_leg": -1.1,
                "r_lower_leg": 1.1,
            }
        if name == "point":
            return {"r_upper_arm": -1.15, "r_lower_arm": -0.1, "neck": -0.12, "head": -0.08}
        if name == "surprised":
            return {
                "l_upper_arm": -1.4,
                "r_upper_arm": 1.4,
                "neck": -0.15,
                "head": -0.1,
                "spine": -0.08,
            }
        if name == "happy":
            bounce = 0.08 * abs(math.sin(t * 5.5))
            return {"l_upper_arm": -0.55, "r_upper_arm": 0.55, "spine": -bounce, "neck": -0.08}
        if name == "sad":
            return {"neck": 0.45, "head": 0.35, "spine": 0.22, "l_upper_arm": 0.25, "r_upper_arm": -0.25}
        if name == "walk":
            return self._walk_angles(t)
        if name == "turn":
            return {"spine": 0.55 * math.sin(min(1.0, self.clip_t / max(self.params.duration, 0.4)) * math.pi)}
        if name == "listening":
            return {"neck": -0.2, "head": -0.12, "spine": -0.08}
        return self._idle(skeleton, t, 0.0, 0.0)

    def _wave_angles(self, t: float) -> dict[str, float]:
        p = self.params
        u = min(1.0, self.clip_t / max(p.duration, 0.4))
        lift = _smooth(min(1.0, u / 0.18)) * (1.0 - _smooth(max(0.0, (u - 0.82) / 0.18)))
        osc = math.sin(self.clip_t * p.frequency * 2.2) * p.amplitude * 0.35
        arm = p.arm
        prefix = "r" if arm != "left" else "l"
        sign = -1.0 if prefix == "r" else 1.0
        return {
            f"{prefix}_upper_arm": sign * (-1.25 * lift + osc * 0.15),
            f"{prefix}_lower_arm": sign * (-0.15 * lift + osc),
            "neck": 0.08 * lift,
            "head": 0.06 * lift,
        }

    def _walk_angles(self, t: float) -> dict[str, float]:
        cad = t * 5.2
        step = math.sin(cad)
        return {
            "l_upper_leg": 0.45 * step,
            "r_upper_leg": -0.45 * step,
            "l_lower_leg": 0.2 * max(0.0, -step),
            "r_lower_leg": 0.2 * max(0.0, step),
            "l_upper_arm": -0.4 * step,
            "r_upper_arm": 0.4 * step,
            "spine": 0.05 * math.sin(cad * 2),
            "head": -0.03 * math.sin(cad),
        }

    def _apply_ik(self, skeleton: Skeleton, t: float, weight: float) -> dict[str, np.ndarray]:
        debug: dict[str, np.ndarray] = {}
        if weight <= 0.02:
            return debug
        name = self.name
        if name == "wave":
            p = self.params
            prefix = "r" if p.arm != "left" else "l"
            if not skeleton.has(f"{prefix}_upper_arm"):
                return debug
            hand = skeleton.bone("head") if skeleton.has("head") else skeleton.bone("chest")
            osc = math.sin(self.clip_t * p.frequency * 2.2) * 0.11 * p.amplitude
            side = 0.28 if prefix == "r" else -0.28
            target = np.array([hand.ox + side + osc, hand.oy - 0.08], dtype=np.float32)
            debug[f"{prefix}_hand"] = target
            if weight > 0.5:
                skeleton.ik_chain(f"{prefix}_upper_arm", f"{prefix}_lower_arm", target, bend=1.0 if prefix == "r" else -1.0)
        elif name == "think":
            if skeleton.has("head") and skeleton.has("r_upper_arm"):
                head = skeleton.bone("head")
                target = np.array([head.ox + 0.12, head.oy + 0.08], dtype=np.float32)
                debug["r_hand"] = target
                skeleton.ik_chain("r_upper_arm", "r_lower_arm", target, bend=1.0)
        elif name == "walk":
            cad = t * 5.2
            for side, sign, phase in (("l", -1.0, 0.0), ("r", 1.0, math.pi)):
                if not skeleton.has(f"{side}_upper_leg"):
                    continue
                hip = skeleton.bone(f"{side}_upper_leg")
                lift = max(0.0, math.sin(cad + phase)) * 0.07
                stride = math.sin(cad + phase) * 0.08
                target = np.array([hip.ox + sign * 0.01 + stride, hip.oy + 0.32 - lift], dtype=np.float32)
                debug[f"{side}_foot"] = target
                skeleton.ik_chain(f"{side}_upper_leg", f"{side}_lower_leg", target, bend=-1.0 if side == "l" else 1.0)
        elif name == "point":
            if skeleton.has("r_upper_arm"):
                chest = skeleton.bone("chest")
                target = np.array([chest.ox + 0.42, chest.oy - 0.05], dtype=np.float32)
                debug["r_hand"] = target
                skeleton.ik_chain("r_upper_arm", "r_lower_arm", target, bend=1.0)
        elif name == "sleep":
            if skeleton.has("l_upper_arm") and skeleton.has("head"):
                head = skeleton.bone("head")
                target = np.array([head.ox - 0.05, head.oy + 0.16], dtype=np.float32)
                debug["l_hand"] = target
                skeleton.ik_chain("l_upper_arm", "l_lower_arm", target, bend=-1.0)
        elif name == "sit":
            for side, sign in (("l", -1.0), ("r", 1.0)):
                if not skeleton.has(f"{side}_upper_leg"):
                    continue
                hip = skeleton.bone(f"{side}_upper_leg")
                target = np.array([hip.ox + sign * 0.04, hip.oy + 0.16], dtype=np.float32)
                debug[f"{side}_foot"] = target
                skeleton.ik_chain(f"{side}_upper_leg", f"{side}_lower_leg", target, bend=-1.0 if side == "l" else 1.0)
        return debug
