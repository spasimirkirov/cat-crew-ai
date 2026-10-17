"""Bake Mixamo FBX clips onto the 2D particle rig.

The overlay never draws the Mixamo mesh. We skip Geometry, keep Model +
AnimationCurve nodes, and map mixamorig Euler deltas onto our bone names.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

_DIR = Path(__file__).resolve().parent.parent / "mixamo_animations"
_CACHE = _DIR / ".cache"
_TICKS = 46186158000.0
_HEADER = b"Kaydara FBX Binary  \x00\x1a\x00"
_CACHE_VERSION = 2

_CLIP_FILES = {
    "idle": ("Idle.fbx", "Female Standing Pose 1.fbx"),
    "wave": ("Waving.fbx",),
    "wave_both": ("Waving With Both Hands.fbx",),
}

_BONE_MAP = {
    "mixamorig:Hips": "pelvis",
    "mixamorig:Spine": "spine",
    "mixamorig:Spine1": "chest",
    "mixamorig:Spine2": "chest",
    "mixamorig:Neck": "neck",
    "mixamorig:Head": "head",
    "mixamorig:LeftShoulder": "l_shoulder",
    "mixamorig:RightShoulder": "r_shoulder",
    "mixamorig:LeftArm": "l_upper_arm",
    "mixamorig:LeftForeArm": "l_lower_arm",
    "mixamorig:LeftHand": "l_hand",
    "mixamorig:RightArm": "r_upper_arm",
    "mixamorig:RightForeArm": "r_lower_arm",
    "mixamorig:RightHand": "r_hand",
    "mixamorig:LeftUpLeg": "l_upper_leg",
    "mixamorig:LeftLeg": "l_lower_leg",
    "mixamorig:LeftFoot": "l_foot",
    "mixamorig:RightUpLeg": "r_upper_leg",
    "mixamorig:RightLeg": "r_lower_leg",
    "mixamorig:RightFoot": "r_foot",
}

_SKIP_OBJECTS = frozenset(
    {
        "Geometry",
        "Deformer",
        "Deformer",
        "Pose",
        "Material",
        "Texture",
        "Video",
        "NodeAttribute",
        "CollectionExclusive",
        "DisplayLayer",
        "SceneInfo",
        "GlobalSettings",
        "Implementation",
        "BindingTable",
        "AnimLayer",
        "Constraint",
    }
)

_KEEP_OBJECTS = frozenset({"Model", "AnimationCurve", "AnimationCurveNode", "AnimationStack", "AnimationLayer"})

_AXIS = {"X": 0, "Y": 1, "Z": 2, "d|X": 0, "d|Y": 1, "d|Z": 2}

_clips: dict[str, "MixamoClip | None"] = {}


@dataclass
class MixamoClip:
    name: str
    duration: float
    times: np.ndarray
    angles: dict[str, np.ndarray]

    def sample(self, t: float, *, loop: bool) -> dict[str, float]:
        times = self.times
        if times.size == 0:
            return {}
        dur = max(float(self.duration), 1e-4)
        if loop:
            u = float(t) % dur
        else:
            u = min(max(float(t), 0.0), dur)
        if times.size == 1:
            return {k: float(v[0]) for k, v in self.angles.items()}
        i = int(np.searchsorted(times, u, side="right") - 1)
        i = max(0, min(i, times.size - 2))
        t0 = float(times[i])
        t1 = float(times[i + 1])
        w = 0.0 if t1 <= t0 else (u - t0) / (t1 - t0)
        out: dict[str, float] = {}
        for name, arr in self.angles.items():
            out[name] = float(arr[i] * (1.0 - w) + arr[i + 1] * w)
        return out


def clip_dir() -> Path:
    return _DIR


def available() -> bool:
    return any((_DIR / name).is_file() for names in _CLIP_FILES.values() for name in names)


def duration(name: str) -> float | None:
    clip = load_clip(name)
    return None if clip is None else float(clip.duration)


def sample(name: str, t: float) -> dict[str, float]:
    key = (name or "idle").strip().lower()
    if key in ("talking", "stand"):
        key = "idle"
    clip = load_clip(key)
    if clip is None:
        return {}
    loop = key not in ("wave", "wave_both")
    return clip.sample(t, loop=loop)


def load_clip(name: str) -> MixamoClip | None:
    key = (name or "idle").strip().lower()
    if key in ("talking", "stand"):
        key = "idle"
    if key not in _CLIP_FILES:
        return None
    if key in _clips:
        return _clips[key]
    clip = _load_or_bake(key)
    _clips[key] = clip
    return clip


def _load_or_bake(key: str) -> MixamoClip | None:
    cache = _CACHE / f"{key}.npz"
    if cache.is_file():
        try:
            clip = _from_npz(key, cache)
            if clip is not None:
                return clip
        except Exception:
            pass
    path = _find_fbx(key)
    if path is None:
        return None
    try:
        clip = _bake_fbx(key, path)
    except Exception:
        return None
    if clip is None:
        return None
    try:
        _CACHE.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "times": clip.times,
            "duration": np.float32(clip.duration),
            "version": np.int32(_CACHE_VERSION),
        }
        for bone, arr in clip.angles.items():
            payload[f"b_{bone}"] = arr.astype(np.float32)
        np.savez_compressed(cache, **payload)
    except Exception:
        pass
    return clip


def _from_npz(key: str, path: Path) -> MixamoClip | None:
    data = np.load(path)
    version = int(data["version"]) if "version" in data.files else 0
    if version != _CACHE_VERSION:
        return None
    times = np.asarray(data["times"], dtype=np.float32)
    dur = float(data["duration"][0]) if np.ndim(data["duration"]) else float(data["duration"])
    angles = {k[2:]: np.asarray(data[k], dtype=np.float32) for k in data.files if k.startswith("b_")}
    return MixamoClip(name=key, duration=dur, times=times, angles=angles)


def _find_fbx(key: str) -> Path | None:
    for name in _CLIP_FILES.get(key, ()):
        path = _DIR / name
        if path.is_file():
            return path
    return None


def _bake_fbx(key: str, path: Path) -> MixamoClip | None:
    parsed = _parse_fbx(path)
    if not parsed:
        return None
    duration_s, bones = parsed
    if duration_s < 0.05:
        duration_s = 1.0
    fps = 30.0
    n = max(2, int(round(duration_s * fps)) + 1)
    times = np.linspace(0.0, duration_s, n, dtype=np.float32)
    angles: dict[str, np.ndarray] = {}
    for bone, axes in bones.items():
        mapped = _BONE_MAP.get(bone) or _BONE_MAP.get(bone.split("\x00", 1)[0])
        if not mapped:
            continue
        series = []
        for axis in range(3):
            pair = axes.get(axis)
            if pair is None:
                series.append(np.zeros(n, np.float32))
                continue
            kt, kv = pair
            series.append(_sample_curve(kt, kv, times))
        rest = [float(s[0]) for s in series]
        spans = [float(np.ptp(s)) for s in series]
        axis = int(np.argmax(spans))
        if spans[axis] < 1.5:
            continue
        delta = np.deg2rad(series[axis] - rest[axis]).astype(np.float32)
        scale = 0.78
        if mapped in ("pelvis", "spine", "chest"):
            scale = 0.28
        if mapped.startswith("r_"):
            delta = -delta
        if mapped in angles:
            # Spine1/Spine2 both map to chest — keep the livelier one.
            if float(np.ptp(angles[mapped])) >= float(np.ptp(delta)):
                continue
        angles[mapped] = (delta * scale).astype(np.float32)
    if not angles:
        return None
    return MixamoClip(name=key, duration=duration_s, times=times, angles=angles)


def _sample_curve(kt: np.ndarray, kv: np.ndarray, times: np.ndarray) -> np.ndarray:
    if kt.size == 0 or kv.size == 0:
        return np.zeros(times.shape[0], np.float32)
    tsec = kt.astype(np.float64) / _TICKS
    return np.interp(times, tsec, kv.astype(np.float64)).astype(np.float32)


def _parse_fbx(path: Path) -> tuple[float, dict[str, dict[int, tuple[np.ndarray, np.ndarray]]]] | None:
    data = path.read_bytes()
    if not data.startswith(_HEADER[:20]):
        return None
    version = struct.unpack_from("<I", data, 23)[0]
    big = version >= 7500
    pos = 27
    models: dict[int, str] = {}
    curves: dict[int, dict[str, np.ndarray]] = {}
    nodes: dict[int, str] = {}
    links: list[tuple[str, int, int, str]] = []
    duration_s = 0.0

    def read_node(at: int) -> tuple[int, str | None, int, int]:
        if at + 13 > len(data):
            return at, None, 0, at
        if big:
            end, nprop, _plen = struct.unpack_from("<QQQ", data, at)
            name_at = at + 24
        else:
            end, nprop, _plen = struct.unpack_from("<III", data, at)
            name_at = at + 12
        if end == 0:
            return at, None, 0, at
        nlen = data[name_at]
        name = data[name_at + 1 : name_at + 1 + nlen].decode("ascii", "replace")
        prop_at = name_at + 1 + nlen
        return int(end), name, int(nprop), prop_at

    def walk(at: int, limit: int, parent: str) -> int:
        nonlocal duration_s
        cur = at
        while cur < limit:
            end, name, nprop, prop_at = read_node(cur)
            if name is None:
                break
            if end <= cur or end > len(data):
                break
            if parent == "Objects" and name not in _KEEP_OBJECTS:
                cur = end
                continue
            if name in _SKIP_OBJECTS:
                cur = end
                continue
            props, child_at = _read_props(data, prop_at, nprop)
            if name == "Model" and props:
                uid = _as_id(props[0])
                raw = _as_str(props[1]) if len(props) > 1 else ""
                if uid is not None and raw:
                    models[uid] = raw.split("\x00", 1)[0]
                cur = end
                continue
            if name == "AnimationCurve" and props:
                uid = _as_id(props[0])
                if uid is not None:
                    curves[uid] = {}
                    walk_curve(child_at, end, uid)
                cur = end
                continue
            if name == "AnimationCurveNode" and props:
                uid = _as_id(props[0])
                raw = _as_str(props[1]) if len(props) > 1 else ""
                if uid is not None:
                    nodes[uid] = raw.split("\x00", 1)[0]
                cur = end
                continue
            if name == "C" and props:
                kind = _as_str(props[0])
                src = _as_id(props[1]) if len(props) > 1 else None
                dst = _as_id(props[2]) if len(props) > 2 else None
                prop = _as_str(props[3]) if len(props) > 3 else ""
                if kind and src is not None and dst is not None:
                    links.append((kind, src, dst, prop))
            elif name == "AnimationStack":
                duration_s = max(duration_s, _stack_duration(data, child_at, end, big))
            if child_at < end:
                walk(child_at, end, name)
            cur = end
        return cur

    def walk_curve(at: int, limit: int, uid: int) -> None:
        cur = at
        while cur < limit:
            end, name, nprop, prop_at = read_node(cur)
            if name is None:
                break
            props, _child = _read_props(data, prop_at, nprop)
            if name in ("KeyTime", "KeyValueFloat") and props:
                arr = _as_array(props[0])
                if arr is not None:
                    curves[uid][name] = arr
            cur = end

    walk(pos, len(data), "")
    bone_axes: dict[str, dict[int, tuple[np.ndarray, np.ndarray]]] = {}
    node_model: dict[int, int] = {}
    curve_axis: dict[int, int] = {}
    for kind, src, dst, prop in links:
        if src in nodes and dst in models and (kind == "OO" or "Rotation" in prop):
            node_model[src] = dst
        elif dst in nodes and src in models and (kind == "OO" or "Rotation" in prop):
            node_model[dst] = src
        elif kind in ("OP", "OO") and src in curves and dst in nodes:
            axis = _AXIS.get(prop.split("|")[-1] if prop else "", _AXIS.get(prop, -1))
            if axis >= 0:
                curve_axis[src] = axis
        elif kind == "OP" and dst in curves and src in nodes:
            axis = _AXIS.get(prop.split("|")[-1] if prop else "", _AXIS.get(prop, -1))
            if axis >= 0:
                curve_axis[dst] = axis
    # If OP property is missing, assign X/Y/Z in connection order.
    pending: dict[int, list[int]] = {}
    for kind, src, dst, prop in links:
        if src in curves and dst in nodes and src not in curve_axis:
            pending.setdefault(dst, []).append(src)
    for nid, curve_ids in pending.items():
        for i, cid in enumerate(curve_ids[:3]):
            curve_axis.setdefault(cid, i)
    for cid, axis in curve_axis.items():
        nid = None
        for kind, src, dst, _prop in links:
            if src == cid and dst in nodes:
                nid = dst
                break
        if nid is None or nid not in node_model:
            continue
        model_id = node_model[nid]
        bone = models.get(model_id, "")
        if not bone:
            continue
        packed = curves.get(cid, {})
        kt = packed.get("KeyTime")
        kv = packed.get("KeyValueFloat")
        if kt is None or kv is None:
            continue
        bone_axes.setdefault(bone, {})[axis] = (kt.astype(np.float64), kv.astype(np.float32))
        duration_s = max(duration_s, float(kt.max() / _TICKS) if kt.size else 0.0)
    if not bone_axes:
        return None
    return duration_s, bone_axes


def _stack_duration(data: bytes, at: int, limit: int, big: bool) -> float:
    cur = at
    local_start = 0.0
    local_stop = 0.0
    while cur < limit:
        if big:
            end, nprop, _plen = struct.unpack_from("<QQQ", data, cur)
            nlen_at = cur + 24
        else:
            end, nprop, _plen = struct.unpack_from("<III", data, cur)
            nlen_at = cur + 12
        if end == 0 or end <= cur:
            break
        nlen = data[nlen_at]
        name = data[nlen_at + 1 : nlen_at + 1 + nlen].decode("ascii", "replace")
        prop_at = nlen_at + 1 + nlen
        props, _c = _read_props(data, prop_at, int(nprop))
        if name in ("LocalStop", "ReferenceStop") and props:
            val = _as_id(props[0])
            if val:
                local_stop = max(local_stop, float(val) / _TICKS)
        if name in ("LocalStart", "ReferenceStart") and props:
            val = _as_id(props[0])
            if val:
                local_start = float(val) / _TICKS
        cur = end
    return max(0.0, local_stop - local_start)


def _read_props(data: bytes, at: int, nprop: int) -> tuple[list[Any], int]:
    props: list[Any] = []
    cur = at
    for _ in range(max(0, int(nprop))):
        if cur >= len(data):
            break
        code = chr(data[cur])
        cur += 1
        try:
            val, cur = _read_prop(data, cur, code)
        except Exception:
            break
        props.append(val)
    return props, cur


def _read_prop(data: bytes, cur: int, code: str) -> tuple[Any, int]:
    if code == "Y":
        return struct.unpack_from("<h", data, cur)[0], cur + 2
    if code == "C":
        return bool(data[cur]), cur + 1
    if code == "I":
        return struct.unpack_from("<i", data, cur)[0], cur + 4
    if code == "F":
        return struct.unpack_from("<f", data, cur)[0], cur + 4
    if code == "D":
        return struct.unpack_from("<d", data, cur)[0], cur + 8
    if code == "L":
        return struct.unpack_from("<q", data, cur)[0], cur + 8
    if code in "SR":
        n = struct.unpack_from("<I", data, cur)[0]
        start = cur + 4
        return data[start : start + n], start + n
    if code in "fdlib":
        length, encoding, enc_len = struct.unpack_from("<III", data, cur)
        cur += 12
        blob = data[cur : cur + enc_len]
        cur += enc_len
        if encoding == 1:
            blob = zlib.decompress(blob)
        dtype = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "b": "|u1"}[code]
        arr = np.frombuffer(blob, dtype=dtype, count=length).copy()
        return arr, cur
    return None, cur + 1


def _as_id(val: Any) -> int | None:
    if isinstance(val, (int, np.integer)):
        return int(val)
    return None


def _as_str(val: Any) -> str:
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", "replace")
    if isinstance(val, str):
        return val
    return ""


def _as_array(val: Any) -> np.ndarray | None:
    if isinstance(val, np.ndarray):
        return val
    return None
