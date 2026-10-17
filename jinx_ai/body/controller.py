"""BodyController — public embodiment API. No QPainter."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .animation import AnimationController
from .definition import BodyDefinition, humanoid_definition
from .emotion import BodyEmotionController
from .particles.data import ParticleData
from .particles.simulation import ParticleSimulation, SimParams
from .particles.targets import lerp_targets, smooth01
from .shapes.base import ShapeContext
from .shapes.humanoid import HumanoidGenerator
from .shapes.procedural import ProceduralGenerator
from .shapes.silhouette import SilhouetteGenerator, generate_from_mask, generate_from_points, generate_from_primitives
from .skeleton import Skeleton
from .state import BodyState, clip_for_state, overlay_to_state

FIGURE_SHAPES = frozenset({"humanoid", "silhouette"})
PROCEDURAL_SHAPES = frozenset({"orb"})
ALL_SHAPES = ("humanoid", "orb")
FIGURE_COUNT = {"humanoid": 560, "silhouette": 420}


@dataclass
class ParticleStyle:
    primary: tuple[int, int, int] = (0, 240, 255)
    secondary: tuple[int, int, int] = (255, 0, 127)
    glow: float = 1.0
    hologram: float = 0.0
    trails: int = 0
    body_scale: float = 1.0
    animation_speed: float = 1.0


class BodyController:
    """Skeleton + animation + shape + particles. Renderer consumes snapshot()."""

    def __init__(self, count: int = 240, seed: int = 11) -> None:
        self.particles = ParticleData(count, seed)
        self.sim = ParticleSimulation()
        self.anim = AnimationController()
        self.emotion = BodyEmotionController()
        self.style = ParticleStyle()
        self.shape = "orb"
        self.mode = "idle"
        self.state = BodyState.IDLE
        self.muted = False
        self.level = 0.0
        self.viseme = 0.08
        self.expr = ""
        self.expr_left = 0.0
        self.expr_hold = 0.0
        self._glyph = "eyes"
        self.t = 0.0
        self.debug = False
        self.origin_x = 0.0
        self.origin_y = 0.0
        self._walk_tx: float | None = None
        self._walk_ty: float | None = None
        self._heartbeat_bpm = 72.0
        self._heartbeat_amp = 0.35
        self.speaking = False
        self.listening = False
        self.cohesion = 1.0
        self.dissolve = 0.0
        self._dissolve_speed = 0.0
        self.ghost = False
        self._morph_t = 1.0
        self._morph_dur = 1.2
        self._morph_from: tuple[np.ndarray, np.ndarray] | None = None
        self._morph_from_shape = "orb"
        self.definition: BodyDefinition | None = None
        self.skeleton: Skeleton | None = None
        self._ik_debug: dict[str, np.ndarray] = {}
        self._fps = 0.0
        self._rng_seed = seed
        self._gens: dict[str, Any] = {
            "orb": ProceduralGenerator("orb"),
            "humanoid": HumanoidGenerator("humanoid"),
        }
        self._ensure_figure_def(self.shape)

    @property
    def n(self) -> int:
        return self.particles.n

    def ensure_count(self, count: int) -> None:
        if count > self.particles.n:
            self.particles.resize(count)

    def set_shape(self, name: str, *, morph: float | None = None) -> None:
        key = (name or "orb").strip().lower()
        if key not in self._gens:
            key = "orb"
        if key == self.shape and self._morph_t >= 1.0:
            return
        dur = 1.2 if morph is None else float(morph)
        self.morph_to(key, duration=dur)

    def morph_to(self, name: str, duration: float = 1.5) -> None:
        key = (name or "orb").strip().lower()
        if key not in self._gens:
            key = "orb"
        ctx = self._context()
        current = self._generate(self.shape, ctx)
        self._morph_from = (current.x.copy(), current.y.copy())
        self._morph_from_shape = self.shape
        self.shape = key
        self.ghost = False
        dur = max(0.0, float(duration))
        self._morph_dur = max(0.2, dur) if dur > 0.02 else 0.01
        self._morph_t = 1.0 if dur <= 0.02 else 0.0
        if self._morph_t >= 1.0:
            self._morph_from = None
        self._ensure_figure_def(key)
        gen = self._gens.get(key)
        invalidate = getattr(gen, "invalidate", None)
        if callable(invalidate):
            invalidate()
        want = FIGURE_COUNT.get(key)
        if want:
            self.ensure_count(want)
        trails = int(self.style.trails)
        self.particles.set_trail_length(trails)

    def play(self, name: str, duration: float = 1.8, **kwargs) -> None:
        key = (name or "idle").strip().lower()
        extra = {k: v for k, v in kwargs.items() if k != "duration"}
        extra["clip_duration"] = float(kwargs.get("duration", duration))
        if key == "wave":
            extra.setdefault("arm", "right")
            extra.setdefault("amplitude", 0.8)
            extra.setdefault("frequency", 5.0)
            extra.setdefault("repetitions", 3.0)
            extra["clip_duration"] = float(duration)
            self.anim.transition_to("wave", duration=0.28, **extra)
            self.state = BodyState.WAVE
            return
        self.anim.transition_to(key, duration=0.35, **extra)
        if key == "walk":
            self.state = BodyState.WALK
        elif key == "sleep":
            self.state = BodyState.SLEEPING
        elif key == "think":
            self.state = BodyState.THINKING
        elif key == "happy":
            self.state = BodyState.HAPPY
        elif key == "surprised":
            self.state = BodyState.SURPRISED
        else:
            self.state = BodyState.CUSTOM if key not in ("idle",) else BodyState.IDLE

    def set_state(self, name: str) -> None:
        try:
            state = BodyState(str(name or "idle").lower())
        except ValueError:
            state = overlay_to_state(name)
        self.state = state
        if self.shape in FIGURE_SHAPES:
            clip = clip_for_state(state)
            if self.anim.name != clip:
                self.anim.transition_to(clip, duration=0.4)
        self.speaking = state is BodyState.TALKING
        self.listening = state is BodyState.LISTENING

    def set_mode(self, name: str) -> None:
        key = name if name else "idle"
        if self.muted and key not in ("error", "muted", "speaking", "thinking", "tool", "sleeping"):
            key = "muted"
        self.mode = key
        self.speaking = key == "speaking"
        self.listening = key == "listening"
        if self.shape in FIGURE_SHAPES:
            state = overlay_to_state(key)
            self.state = state
            clip = clip_for_state(state)
            if key == "tool":
                clip = "idle"
            if self.anim.name != clip and self.anim.name not in ("wave", "walk"):
                self.anim.transition_to(clip, duration=0.4)
        else:
            self.state = overlay_to_state(key)

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        if self.muted:
            self.set_mode("muted")
        elif self.mode == "muted":
            self.set_mode("idle")

    def set_level(self, value: float) -> None:
        self.level = min(1.0, max(0.0, float(value)))

    def set_viseme(self, open_n: float) -> None:
        self.viseme = min(1.0, max(0.0, float(open_n)))

    def set_expression(self, emotion: str, duration: float = 1.7) -> None:
        name = (emotion or "").strip().lower()
        if name not in ("chaotic", "happy"):
            return
        self.expr = name
        self.expr_hold = max(0.4, float(duration))
        self.expr_left = self.expr_hold
        self._glyph = "eyes" if (int(self.t * 7) % 3) else "monkey"
        if self.shape in FIGURE_SHAPES:
            if name == "happy":
                self.play("happy", duration=min(1.4, self.expr_hold))
            elif name == "chaotic":
                self.emotion.set("fear", 0.85)

    def set_emotion(self, name: str, value: float) -> None:
        self.emotion.set(name, value)

    def set_speaking(self, on: bool) -> None:
        self.speaking = bool(on)
        if on:
            self.set_mode("speaking")
        elif self.mode == "speaking":
            self.set_mode("idle")

    def set_listening(self, on: bool) -> None:
        self.listening = bool(on)
        if on:
            self.set_mode("listening")
        elif self.mode == "listening":
            self.set_mode("idle")

    def set_heartbeat(self, bpm: float, intensity: float = 0.35) -> None:
        self._heartbeat_bpm = max(30.0, min(180.0, float(bpm)))
        self._heartbeat_amp = min(1.0, max(0.0, float(intensity)))

    def move_to(self, x: float, y: float) -> None:
        self.origin_x = float(x)
        self.origin_y = float(y)

    def move_by(self, dx: float, dy: float) -> None:
        self.origin_x += float(dx)
        self.origin_y += float(dy)

    def walk_to(self, x: float, y: float) -> None:
        self._walk_tx = float(x)
        self._walk_ty = float(y)
        self.play("walk")

    def dissolve_body(self, duration: float = 1.2) -> None:
        self._dissolve_speed = 1.0 / max(0.15, float(duration))

    def reform(self, duration: float = 1.2) -> None:
        self._dissolve_speed = -1.0 / max(0.15, float(duration))

    def set_debug(self, on: bool) -> None:
        self.debug = bool(on)

    def apply_style(self, look: dict[str, Any] | None) -> None:
        cfg = look or {}
        self.style.hologram = 1.0 if cfg.get("hologram") else float(cfg.get("hologram_intensity") or 0.0)
        self.style.trails = int(cfg.get("trails") or 0)
        self.debug = bool(cfg.get("body_debug", False))
        if self.ghost:
            self.particles.set_trail_length(max(4, self.style.trails))
        else:
            self.particles.set_trail_length(self.style.trails)
        if "cohesion" in cfg:
            self.cohesion = min(1.2, max(0.08, float(cfg["cohesion"])))
        self.anim.speed = float(cfg.get("animation_speed") or self.style.animation_speed)
        self.style.body_scale = float(cfg.get("body_scale") or 1.0)
        if self.skeleton is not None:
            self.skeleton.scale = self.style.body_scale

    def generate_from_points(self, points: np.ndarray) -> None:
        rng = np.random.default_rng(self._rng_seed)
        result = generate_from_points(points, self.n, rng)
        self._gens["silhouette"] = _StaticGen("silhouette", result)
        self.morph_to("silhouette", duration=1.0)

    def generate_from_mask(self, mask: np.ndarray) -> None:
        self._gens["silhouette"] = SilhouetteGenerator(mask, "silhouette")
        self.morph_to("silhouette", duration=1.2)

    def generate_from_sdf(self, sdf: np.ndarray) -> None:
        self.generate_from_mask(np.asarray(sdf) > 0.5)

    def generate_from_primitives(self, primitives: list[dict]) -> None:
        rng = np.random.default_rng(self._rng_seed)
        result = generate_from_primitives(primitives, self.n, rng)
        self._gens["silhouette"] = _StaticGen("silhouette", result)
        self.morph_to("silhouette", duration=1.0)

    def update(self, dt: float) -> None:
        dt = float(max(0.008, min(0.05, dt)))
        self.t += dt
        self._fps = 1.0 / dt if dt > 1e-6 else 0.0
        if self.expr_left > 0:
            self.expr_left = max(0.0, self.expr_left - dt)
            if self.expr_left <= 0:
                self.expr = ""
                if self.emotion.fear > 0.5 and self.shape in FIGURE_SHAPES:
                    self.emotion.set("fear", 0.0)
        if self._dissolve_speed != 0.0:
            self.dissolve = min(1.0, max(0.0, self.dissolve + self._dissolve_speed * dt))
            if self.dissolve in (0.0, 1.0):
                self._dissolve_speed = 0.0
        if self._morph_t < 1.0:
            self._morph_t = min(1.0, self._morph_t + dt / self._morph_dur)
        self._step_walk(dt)
        self.anim.update(dt)
        if self.anim.name == "idle" and self.state in (BodyState.WAVE, BodyState.SURPRISED, BodyState.HAPPY):
            self.state = overlay_to_state(self.mode)
            if self.shape in FIGURE_SHAPES:
                clip = clip_for_state(self.state)
                if clip != "idle" and self.anim.name != clip:
                    self.anim.transition_to(clip, duration=0.3)
        hb_phase = self.t * (self._heartbeat_bpm / 60.0) * math.tau
        if self.skeleton is not None and self.shape in FIGURE_SHAPES:
            self.skeleton.origin_x = self.origin_x
            self.skeleton.origin_y = self.origin_y
            self.skeleton.scale = self.style.body_scale
            self._ik_debug = self.anim.apply(
                self.skeleton,
                self.t,
                speaking=self.speaking,
                listening=self.listening or self.mode == "listening",
                heartbeat_phase=hb_phase,
                heartbeat_amp=self._heartbeat_amp * (0.25 if self.mode == "sleeping" else 1.0),
            )
            emo = self.emotion.pose_offsets()
            for name, delta in emo.items():
                if self.skeleton.has(name) and abs(delta) > 1e-4:
                    self.skeleton.bone(name).angle += delta
            self.skeleton.solve()
        ctx = self._context()
        result = self._generate(self.shape, ctx)
        tx, ty = result.x, result.y
        if self._morph_from is not None and self._morph_t < 1.0:
            fx, fy = self._morph_from
            if fx.shape[0] != tx.shape[0]:
                fx, fy, _ = _fit(fx, fy, tx.shape[0])
            tx, ty = lerp_targets(fx, fy, tx, ty, self._morph_t)
        elif self._morph_t >= 1.0:
            self._morph_from = None
        tx, ty = self._secondary(tx, ty, result.region)
        p = self.particles
        p.bx, p.by = tx, ty
        p.tx, p.ty = tx, ty
        p.region = result.region if result.region.shape[0] == p.n else p.region
        params = self._sim_params()
        self.sim.step(p, dt, params, self.t)
        sparkle_ok = (
            self.shape in PROCEDURAL_SHAPES
            and self.mode in ("idle", "speaking", "tool")
            and not self.muted
            and self.mode != "sleeping"
        )
        self.sim.idle_sparks(p, dt, self.t, sparkle_ok)
        if self.expr == "happy" and self.shape in PROCEDURAL_SHAPES:
            p.energy[:] = np.minimum(2.2, p.energy + 0.4)

    def snapshot(self) -> dict[str, Any]:
        snap = self.particles.snapshot()
        snap["mode"] = self.mode
        snap["expr"] = self.expr
        snap["expr_t"] = self.expr_left
        snap["shape"] = self.shape
        snap["anim"] = self.anim.name
        snap["morph"] = self._morph_t
        snap["debug"] = self.debug_info() if self.debug else None
        return snap

    def debug_info(self) -> dict[str, Any]:
        bones = []
        if self.skeleton is not None:
            for b in self.skeleton.bones:
                bones.append((b.ox, b.oy, b.tip_x, b.tip_y, b.name))
        bbox = self.skeleton.bbox() if self.skeleton is not None else (0, 0, 0, 0)
        return {
            "bones": bones,
            "ik": {k: (float(v[0]), float(v[1])) for k, v in self._ik_debug.items()},
            "bbox": bbox,
            "fps": self._fps,
            "n": self.n,
            "anim": self.anim.name,
            "shape": self.shape,
            "morph": self._morph_t,
            "state": self.state.value,
        }

    def _ensure_figure_def(self, name: str) -> None:
        if name not in FIGURE_SHAPES:
            return
        self.definition = humanoid_definition()
        self.skeleton = self.definition.make_skeleton()
        self.skeleton.origin_x = self.origin_x
        self.skeleton.origin_y = self.origin_y
        self.skeleton.scale = self.style.body_scale
        self.skeleton.solve()

    def _context(self) -> ShapeContext:
        return ShapeContext(
            t=self.t,
            mode=self.mode,
            level=self.level,
            viseme=self.viseme,
            muted=self.muted,
            expr=self.expr if self.expr_left > 0.35 or self.expr == "happy" else "",
            glyph=self._glyph,
            seed=self.particles.seed,
            u=self.particles.u,
            i=self.particles.i,
            skeleton=self.skeleton,
            head_radius=self.definition.head_radius if self.definition else 0.125,
            blocky=False,
            rng=np.random.default_rng(self._rng_seed + self.n),
        )

    def _generate(self, name: str, ctx: ShapeContext):
        gen = self._gens.get(name) or self._gens["orb"]
        return gen.generate(self.n, ctx)

    def _secondary(self, tx: np.ndarray, ty: np.ndarray, region: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        p = self.particles
        if self.ghost or self.shape == "ghost":
            ty = (ty * 1.08 + (ty - self.origin_y) * 0.06).astype(np.float32)
            tx = (tx + 0.02 * np.sin(self.t * 0.7 + p.seed * 5)).astype(np.float32)
        if self.dissolve > 0.01:
            u = smooth01(self.dissolve)
            ang = p.seed * math.tau + self.t * 0.4
            tx = (tx + np.cos(ang) * 0.85 * u).astype(np.float32)
            ty = (ty + np.sin(ang) * 0.85 * u).astype(np.float32)
        holo = float(self.style.hologram)
        if holo > 0.01:
            ty = (ty + 0.018 * holo * np.sin(ty * 38.0 + self.t * 9.0)).astype(np.float32)
            tx = (tx + 0.01 * holo * np.sin(self.t * 17.0 + p.i)).astype(np.float32)
            flick = (np.sin(self.t * 40.0 + p.seed * 20) * 0.5 + 0.5) > (0.08 * holo)
            p.visible = flick.astype(np.uint8)
            # Chromatic: extra pink drift
            pink = p.kind == 1
            tx[pink] = (tx[pink] + 0.012 * holo).astype(np.float32)
        else:
            p.visible[:] = 1
        if self.mode == "thinking" and self.shape in FIGURE_SHAPES and self.skeleton is not None:
            # Orbit a few particles around the head without moving the skeleton.
            head = self.skeleton.bone("head")
            orbit = p.u < 0.12
            ang = self.t * 3.2 + p.seed * math.tau
            r = 0.16 + 0.04 * np.sin(self.t * 2.0 + p.seed)
            tx = tx.copy()
            ty = ty.copy()
            tx[orbit] = (head.ox + np.cos(ang[orbit]) * r[orbit]).astype(np.float32)
            ty[orbit] = (head.oy + np.sin(ang[orbit]) * r[orbit] * 0.85).astype(np.float32)
        if self.speaking and self.shape in FIGURE_SHAPES:
            p.energy[:] = np.where(region <= 3, np.minimum(2.0, p.energy + 0.25), p.energy)
        return tx, ty

    def _sim_params(self) -> SimParams:
        pull = 6.8
        damp = 0.90
        noise = 0.55
        sparkle = self.shape in PROCEDURAL_SHAPES
        if self.mode in ("thinking", "listening", "speaking", "tool") and self.shape in PROCEDURAL_SHAPES:
            pull = 10.2
            damp = 0.86
        if self.expr == "chaotic" and self.expr_left > 0.4:
            pull = 10.5
        if self.muted:
            pull = 4.2
            noise = 0.12
        if self.mode == "sleeping":
            pull = 3.1
            noise = 0.08
            sparkle = False
        if self.expr == "happy" and self.shape in PROCEDURAL_SHAPES:
            noise = 0.9
        if self.shape in FIGURE_SHAPES:
            pull = 10.2
            damp = 0.86
            noise = 0.12
            sparkle = False
            if self.mode == "sleeping":
                pull = 4.2
                noise = 0.06
        cohesion = self.cohesion * self.emotion.cohesion_scale()
        if self.ghost or self.shape == "ghost":
            cohesion *= 0.42
            noise *= 1.35
        cohesion *= 1.0 - 0.85 * self.dissolve
        noise *= self.emotion.noise_scale()
        burst = 0.0
        lift = 0.0
        if self.expr == "happy" and self.shape in PROCEDURAL_SHAPES:
            noise = 0.9
            lift = 3.4
        if self.expr == "chaotic" and self.expr_left <= 0.35 and self.expr_left > 0:
            burst = 18.0
            cohesion *= 0.35
        return SimParams(
            spring=pull,
            damping=damp,
            noise=noise,
            cohesion=cohesion,
            sparkle=sparkle,
            burst=burst,
            lift=lift,
        )

    def _step_walk(self, dt: float) -> None:
        if self._walk_tx is None or self._walk_ty is None:
            return
        dx = self._walk_tx - self.origin_x
        dy = self._walk_ty - self.origin_y
        dist = math.hypot(dx, dy)
        if dist < 0.02:
            self.origin_x, self.origin_y = self._walk_tx, self._walk_ty
            self._walk_tx = self._walk_ty = None
            if self.anim.name == "walk":
                self.anim.transition_to("idle", duration=0.3)
                self.state = BodyState.IDLE
            return
        step = min(dist, 0.28 * dt)
        self.origin_x += dx / dist * step
        self.origin_y += dy / dist * step


class _StaticGen:
    def __init__(self, name: str, result) -> None:
        self.name = name
        self._result = result

    def generate(self, count: int, ctx: ShapeContext):
        del ctx
        from .particles.targets import resample_xy

        x, y, region = resample_xy(self._result.x, self._result.y, count)
        from .shapes.base import ShapeResult

        return ShapeResult(x=x, y=y, region=region)


def _fit(x: np.ndarray, y: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    from .particles.targets import resample_xy

    return resample_xy(x, y, n)
