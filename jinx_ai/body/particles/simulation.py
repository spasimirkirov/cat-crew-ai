"""Spring-damper particle simulation. dt-stable, pooled arrays."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import ParticleData


@dataclass
class SimParams:
    spring: float = 6.8
    damping: float = 0.90
    noise: float = 0.55
    cohesion: float = 1.0
    vmax: float = 8.5
    sparkle: bool = True
    burst: float = 0.0
    lift: float = 0.0


class ParticleSimulation:
    def __init__(self) -> None:
        self._spark_cd = 0.4

    def step(self, particles: ParticleData, dt: float, params: SimParams, t: float) -> None:
        dt = float(max(0.008, min(0.05, dt)))
        p = particles
        pull = float(params.spring) * float(params.cohesion)
        ax = (p.tx - p.x) * pull
        ay = (p.ty - p.y) * pull
        jitter = float(params.noise)
        phase = p.noise_phase
        i = p.i
        shiver_x = np.sin(t * 29.0 + i * 0.51) * 0.55 + np.sin(t * 13.0 + phase * 9)
        shiver_y = np.sin(t * 23.0 + i * 0.33) * 0.55 + np.cos(t * 17.0 + phase * 7)
        ax += shiver_x * 2.8 * jitter
        ay += shiver_y * 2.8 * jitter
        if params.burst > 0.0:
            ax += p.x * float(params.burst)
            ay += p.y * float(params.burst)
        ay -= float(params.lift)
        p.vx += ax * dt
        p.vy += ay * dt
        damp = float(params.damping)
        p.vx *= damp
        p.vy *= damp
        vmax = float(params.vmax)
        speed = np.sqrt(p.vx * p.vx + p.vy * p.vy)
        over = speed > vmax
        if np.any(over):
            scale = (vmax / np.maximum(speed, 1e-6)).astype(np.float32)
            p.vx = np.where(over, p.vx * scale, p.vx)
            p.vy = np.where(over, p.vy * scale, p.vy)
        p.x = (p.x + p.vx * dt).astype(np.float32)
        p.y = (p.y + p.vy * dt).astype(np.float32)
        np.clip(p.x, -2.4, 1.85, out=p.x)
        np.clip(p.y, -2.4, 2.2, out=p.y)
        p.life = (p.life - dt).astype(np.float32)
        dead = p.life <= 0
        if np.any(dead):
            if params.sparkle:
                p.x[dead] = p.tx[dead]
                p.y[dead] = p.ty[dead]
                p.vx[dead] = 0
                p.vy[dead] = 0
            p.life[dead] = p.max_life[dead]
        if p.trail_len > 0 and p.trail_x is not None and p.trail_y is not None:
            p.trail_x[1:] = p.trail_x[:-1]
            p.trail_y[1:] = p.trail_y[:-1]
            p.trail_x[0] = p.x
            p.trail_y[0] = p.y
        np.clip(p.energy, 0.15, 2.4, out=p.energy)

    def idle_sparks(self, particles: ParticleData, dt: float, t: float, active: bool) -> None:
        if not active:
            return
        from .data import PINK

        self._spark_cd -= dt
        if self._spark_cd > 0:
            return
        self._spark_cd = 0.55 + float(particles.seed[0]) * 0.7
        pink = np.nonzero(particles.kind == PINK)[0]
        if pink.size == 0:
            return
        pick = pink[: max(6, pink.size // 8)]
        ang = t * 5.0 + particles.seed[pick] * np.float32(np.pi * 2)
        particles.vx[pick] += (np.cos(ang) * 2.8).astype(np.float32)
        particles.vy[pick] += (np.sin(ang) * 2.1 - 0.4).astype(np.float32)
        particles.life[pick] = np.minimum(particles.max_life[pick], particles.life[pick] + 0.25)
