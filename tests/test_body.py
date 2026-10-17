"""Particle body engine: IK, skeleton, simulation, morph, shapes."""

from __future__ import annotations

import math

import numpy as np

from jinx_ai.body.animation import AnimationController
from jinx_ai.body.controller import BodyController
from jinx_ai.body.definition import humanoid_definition
from jinx_ai.body.ik import solve_two_bone
from jinx_ai.body.particles.simulation import ParticleSimulation, SimParams
from jinx_ai.body.particles.targets import lerp_targets, resample_xy
from jinx_ai.body.shapes.silhouette import fox_mask, generate_from_mask
from jinx_ai.orb_particles import ParticleField


def test_ik_reachable_and_lengths() -> None:
    start = np.array([0.0, 0.0], dtype=np.float32)
    target = np.array([0.2, 0.1], dtype=np.float32)
    mid, end = solve_two_bone(start, target, 0.2, 0.18, bend=1.0)
    l1 = float(np.hypot(*(mid - start)))
    l2 = float(np.hypot(*(end - mid)))
    assert abs(l1 - 0.2) < 0.02
    assert abs(l2 - 0.18) < 0.02


def test_ik_clamps_unreachable() -> None:
    start = np.array([0.0, 0.0], dtype=np.float32)
    target = np.array([4.0, 0.0], dtype=np.float32)
    _mid, end = solve_two_bone(start, target, 0.2, 0.18, bend=1.0)
    dist = float(np.hypot(*(end - start)))
    assert dist <= 0.2 + 0.18 + 1e-3
    assert dist >= 0.2 + 0.18 - 0.02


def test_ik_minimum_distance() -> None:
    start = np.array([0.0, 0.0], dtype=np.float32)
    target = np.array([0.0, 0.0], dtype=np.float32)
    _mid, end = solve_two_bone(start, target, 0.3, 0.12, bend=1.0)
    dist = float(np.hypot(*(end - start)))
    assert dist >= abs(0.3 - 0.12) - 0.02


def test_ik_bend_mirrors() -> None:
    start = np.array([0.0, 0.0], dtype=np.float32)
    target = np.array([0.25, 0.0], dtype=np.float32)
    mid_a, _ = solve_two_bone(start, target, 0.2, 0.18, bend=1.0)
    mid_b, _ = solve_two_bone(start, target, 0.2, 0.18, bend=-1.0)
    assert abs(float(mid_a[1] + mid_b[1])) < 0.05
    assert float(mid_a[1]) * float(mid_b[1]) < 0


def test_skeleton_parent_child_and_scale() -> None:
    skel = humanoid_definition().make_skeleton()
    skel.solve()
    pelvis = skel.bone("pelvis")
    head = skel.bone("head")
    assert head.oy < pelvis.oy
    y0 = float(head.oy)
    skel.scale = 2.0
    skel.solve()
    head2 = skel.bone("head")
    assert abs(head2.oy) > abs(y0) * 1.2
    skel.root_angle = math.pi
    skel.solve()
    assert skel.bone("spine").wangle != 0.0


def test_particles_converge_and_no_nan() -> None:
    body = BodyController(80)
    body.set_shape("orb", morph=0.0)
    p = body.particles
    p.x[:] = 1.4
    p.y[:] = 1.4
    for _ in range(40):
        body.update(0.016)
    err = float(np.hypot(p.x - p.tx, p.y - p.ty).mean())
    assert err < 0.45
    assert np.isfinite(p.x).all()
    assert np.isfinite(p.vx).all()


def test_dt_stability_and_velocity_limit() -> None:
    sim = ParticleSimulation()
    body = BodyController(40)
    p = body.particles
    p.vx[:] = 50
    p.vy[:] = -50
    sim.step(p, 0.05, SimParams(vmax=8.5), 0.0)
    speed = np.sqrt(p.vx * p.vx + p.vy * p.vy)
    assert float(speed.max()) <= 8.51


def test_morph_different_counts() -> None:
    a = np.stack([np.linspace(-1, 1, 600), np.zeros(600)], axis=1)
    b = np.stack([np.zeros(200), np.linspace(-1, 1, 200)], axis=1)
    ax, ay, _ = resample_xy(a[:, 0], a[:, 1], 300)
    bx, by, _ = resample_xy(b[:, 0], b[:, 1], 300)
    x, y = lerp_targets(ax, ay, bx, by, 1.0)
    assert x.shape == (300,)
    assert np.isfinite(x).all()
    assert float(np.abs(x - bx).max()) < 1e-5


def test_humanoid_and_orb_generators() -> None:
    body = BodyController(200)
    body.set_shape("humanoid", morph=0.0)
    for _ in range(12):
        body.update(0.016)
    assert body.skeleton is not None
    assert body.skeleton.has("hair_tail")
    ys = body.particles.ty
    assert float(ys.max() - ys.min()) > 0.6
    from jinx_ai.body.shapes.glyphs import effect_targets

    idle_ctx = BodyController(80)._context()
    idle_ctx.mode = "idle"
    smile_ctx = BodyController(80)._context()
    smile_ctx.mode = "speaking"
    orb_x, orb_y = effect_targets(idle_ctx, 1.0, False)
    smile_x, smile_y = effect_targets(smile_ctx, 1.0, False)
    assert float(np.std(np.hypot(orb_x, orb_y))) < float(np.std(np.hypot(smile_x, smile_y))) + 0.05


def test_wave_returns_to_idle() -> None:
    body = BodyController(120)
    body.set_shape("humanoid", morph=0.0)
    body.play("wave", duration=0.6)
    assert body.anim.name == "wave"
    steps = 0
    while body.anim.name == "wave" and steps < 80:
        body.update(0.02)
        steps += 1
    assert body.anim.name == "idle"
    assert steps > 8


def test_morph_humanoid_orb_keeps_count() -> None:
    body = BodyController(180)
    body.set_shape("humanoid", morph=0.0)
    n = body.n
    body.morph_to("orb", duration=0.4)
    for _ in range(30):
        body.update(0.016)
    assert body.n == n or body.n >= 180
    assert body.shape == "orb"
    assert body._morph_t >= 1.0
    assert np.isfinite(body.particles.tx).all()


def test_silhouette_fox_targets() -> None:
    rng = np.random.default_rng(3)
    result = generate_from_mask(fox_mask(48), 120, rng)
    assert result.x.shape == (120,)
    assert float(np.ptp(result.x)) > 0.2


def test_person_pictogram_has_leg_gap() -> None:
    from jinx_ai.body.shapes.pictograms import person_mask

    m = person_mask(96)
    row = m[int(0.78 * 96)]
    mid = 48
    assert not bool(row[mid])
    assert bool(row[mid - 12]) or bool(row[mid - 10])
    assert bool(row[mid + 12]) or bool(row[mid + 10])
    head = m[int(0.16 * 96)]
    assert int(head.sum()) > 8


def test_silhouette_choice_tolerates_bad_weights() -> None:
    from jinx_ai.body.shapes.silhouette import SilhouetteGenerator, fox_mask

    gen = SilhouetteGenerator(fox_mask(48), "fox")
    gen._w = np.full(gen._px.shape[0], 0.3, dtype=np.float64)
    ctx = BodyController(80)._context()
    result = gen.generate(80, ctx)
    assert result.x.shape == (80,)
    assert np.isfinite(result.x).all()


def test_humanoid_legs_are_split() -> None:
    body = BodyController(240)
    body.set_shape("humanoid", morph=0.0)
    for _ in range(16):
        body.update(0.016)
    ty = body.particles.ty
    tx = body.particles.tx
    low = ty > float(np.quantile(ty, 0.72))
    xs = tx[low]
    assert float(np.ptp(xs)) > 0.08


def test_particle_field_styles_still_work() -> None:
    field = ParticleField(100)
    field.set_style("orb")
    for _ in range(8):
        field.update(0.016)
    assert field.style == "orb"
    field.set_style("humanoid")
    field.update(0.016)
    assert field.style == "humanoid"
    assert field.body.skeleton is not None
    assert field.body.skeleton.has("hair_l")


def test_female_mask_has_hair_and_leg_gap() -> None:
    from jinx_ai.body.shapes.pictograms import female_mask

    m = female_mask(96)
    hair = m[int(0.10 * 96)]
    assert int(hair.sum()) > 8
    row = m[int(0.78 * 96)]
    mid = 48
    assert not bool(row[mid])
    assert bool(row[mid - 12]) or bool(row[mid - 10])


def test_mixamo_idle_clip_optional() -> None:
    from jinx_ai.body import mixamo

    if not mixamo.available():
        return
    clip = mixamo.load_clip("idle")
    if clip is None:
        return
    assert clip.duration > 0.05
    pose = clip.sample(0.1, loop=True)
    assert pose
    assert any(k.endswith("_arm") or k in ("head", "spine", "chest") for k in pose)


def test_mixamo_wave_drives_right_arm() -> None:
    from jinx_ai.body import mixamo

    if mixamo.load_clip("wave") is None:
        return
    body = BodyController(80)
    body.set_shape("humanoid", morph=0.0)
    body.play("wave", duration=1.2)
    while body.anim.blend < 1.0 and body.anim.clip_t < 1.0:
        body.update(0.02)
    seen: list[float] = []
    mix_seen: list[float] = []
    for _ in range(16):
        body.update(0.02)
        mix = mixamo.sample("wave", body.anim.clip_t)
        if "r_upper_arm" in mix and body.skeleton is not None:
            rest = body.skeleton.bone("r_upper_arm").rest_angle
            seen.append(body.skeleton.bone("r_upper_arm").angle - rest)
            mix_seen.append(mix["r_upper_arm"])
    assert seen
    err = float(np.mean(np.abs(np.array(seen) - np.array(mix_seen))))
    assert err < 0.08
    assert float(np.ptp(seen)) > 0.15
