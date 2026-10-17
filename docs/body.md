# Particle body engine

Jinx’s overlay is a **particle organism**. The skeleton is a hidden 2D rig. Particles are the body. There is no VRM, Live2D, mesh, or sprite sheet.

## Layout

```
Life / overlay mode
        |
    BodyController          public API
        |
  +-- BodyDefinition       JSON/dataclass bones + regions
  +-- Skeleton             hierarchical 2D bones (female + hair)
  +-- AnimationController  Mixamo clips when present, else procedural
  +-- ShapeGenerator       orb glyphs, or the female figure
  +-- ParticleSimulation  springs, damping, cohesion, dt
  +-- ParticleRenderer    QPainter today; swap later
```

`jinx_ai/orb_particles.py` is a **facade**. `ParticleField` still has `set_mode` / `set_style` / `snapshot` so the overlay and existing tests keep working. Simulation lives in `jinx_ai/body/`.

Appearance has **two** bodies:

- **Orb** — energy ball. Overlay mode morphs the attractor into a smiling face, heart, brain + neurons, or star.
- **Figure** — female particle silhouette with hair. Mixamo FBX in `jinx_ai/mixamo_animations/` drives idle / wave when those files exist (skeleton only — the mesh is never drawn).

## Coordinates

Body space: origin at the pelvis/root, **y+ down** (same as the overlay). `origin_x` / `origin_y` move the whole figure. The renderer maps unit space onto the overlay widget. Do not store screen pixels on particles.

## Public API

```python
body.set_shape("humanoid")
body.play("wave", duration=1.8, arm="right")
body.set_state("listening")
body.set_emotion("happy", 0.8)
body.set_speaking(True)
body.morph_to("orb", duration=1.2)
body.move_to(x, y)
body.walk_to(x, y)
body.set_heartbeat(bpm, intensity)
body.dissolve_body(1.0)
body.reform(1.0)
```

Overlay: `AvatarWindow.play_animation("wave")`, `morph_to`, `set_emotion`. Appearance radios pick the shape (`orb_style` in `data/settings.json`).

Orb modes:

| Mode | Glyph |
| --- | --- |
| idle / muted / sleeping | sphere |
| speaking / happy | smiling face |
| listening | heart |
| thinking | brain + neurons |
| tool / error | star |
| chaotic expression | neuron graph |

## Mixamo

Drop Mixamo **FBX** files in `jinx_ai/mixamo_animations/`. The parser skips Geometry, keeps animation curves, and caches `jinx_ai/mixamo_animations/.cache/*.npz`. Expected names:

- `Idle.fbx` (or `Female Standing Pose 1.fbx`) → idle
- `Waving.fbx` → wave
- `Waving With Both Hands.fbx` → wave with `arm="both"`

If a clip is missing, the figure falls back to procedural idle/wave. Hair bones stay procedural.

## Adding a shape

Do not add a third Appearance style unless the user asks. Extra generators can still be registered for tools (`generate_from_mask`).

## Adding an animation

Prefer a Mixamo clip. Otherwise add a clip in `jinx_ai/body/animation.py` (`_clip` + optional IK in `_apply_ik`). Call `body.play("name")`. Do not keyframe particle coordinates.

## Renderer

`jinx_ai/body/render/qpainter.py` consumes `snapshot()`. Trails on the overlay still use the existing QImage fade buffer. Debug skeleton: Settings → Appearance → Body debug.

## Settings (`look`)

| Key | Default | Meaning |
| --- | --- | --- |
| `orb_style` | `orb` | `orb` or `humanoid` |
| `hologram` | false | scanline + flicker |
| `body_debug` | false | skeleton overlay |
