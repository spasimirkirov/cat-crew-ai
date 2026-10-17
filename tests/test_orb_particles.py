import numpy as np

from jinx_ai.orb_particles import ParticleField, value_noise


def test_value_noise_is_bounded() -> None:
    x = np.linspace(0, 4, 40, dtype=np.float32)
    y = np.linspace(1, 5, 40, dtype=np.float32)
    n = value_noise(x, y)
    assert n.shape == x.shape
    assert float(n.min()) >= 0.0
    assert float(n.max()) <= 1.0


def test_idle_orb_is_not_a_dot() -> None:
    field = ParticleField(120)
    field.set_style("orb")
    for _ in range(12):
        field.update(0.016)
    spread = float(np.hypot(field.x, field.y).mean())
    assert spread > 0.12
    assert float(np.std(field.x)) > 0.04


def test_thinking_forms_a_brain() -> None:
    field = ParticleField(160)
    field.set_style("orb")
    field.set_mode("thinking")
    for _ in range(40):
        field.update(0.016)
    xs = field.tx
    left = float(xs[xs < 0].size)
    right = float(xs[xs > 0].size)
    assert left > 20
    assert right > 20
    assert float(np.std(xs)) > 0.08


def test_listening_heart_has_a_bottom_point() -> None:
    field = ParticleField(160)
    field.set_style("orb")
    field.set_mode("listening")
    field.set_level(0.8)
    for _ in range(30):
        field.update(0.016)
    assert float(field.ty.max()) > 0.12
    assert float(np.ptp(field.ty)) > 0.28


def test_chaotic_expression_then_releases() -> None:
    field = ParticleField(100)
    field.set_expression("chaotic", duration=0.5)
    assert field.expr == "chaotic"
    steps = 0
    while field.expr and steps < 80:
        field.update(0.02)
        steps += 1
    assert field.expr == ""
    assert steps > 10


def test_idle_orb_is_rounder_than_smile() -> None:
    idle = ParticleField(120)
    idle.set_style("orb")
    idle.set_mode("idle")
    for _ in range(25):
        idle.update(0.016)
    smile = ParticleField(120)
    smile.set_style("orb")
    smile.set_mode("speaking")
    for _ in range(25):
        smile.update(0.016)
    idle_spread = float(np.std(np.hypot(idle.tx, idle.ty)))
    smile_spread = float(np.std(np.hypot(smile.tx, smile.ty)))
    assert idle_spread < smile_spread + 0.02


def test_happy_expression_lifts_particles() -> None:
    field = ParticleField(80)
    field.set_mode("idle")
    for _ in range(8):
        field.update(0.016)
    y0 = float(field.y.mean())
    field.set_expression("happy", duration=1.0)
    for _ in range(20):
        field.update(0.016)
    assert float(field.y.mean()) < y0
