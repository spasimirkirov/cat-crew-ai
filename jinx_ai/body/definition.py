"""Serializable body definitions. Bones are logical regions, not a mesh."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .skeleton import Bone, Skeleton

_PI = math.pi
_HALF = math.pi / 2.0


@dataclass
class BoneDef:
    name: str
    parent: str | None
    local: tuple[float, float] = (0.0, 0.0)
    length: float = 0.0
    angle: float = 0.0
    width: float = 0.08
    region: str = ""
    weight: float = 1.0
    enabled: bool = True


@dataclass
class BodyDefinition:
    name: str
    bones: list[BoneDef] = field(default_factory=list)
    particle_density: float = 1.0
    default_animation: str = "idle"
    head_radius: float = 0.13

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BodyDefinition:
        raw_bones = data.get("bones") or []
        bones: list[BoneDef] = []
        for item in raw_bones:
            if isinstance(item, BoneDef):
                bones.append(item)
                continue
            local = item.get("local") or (item.get("local_x", 0.0), item.get("local_y", 0.0))
            bones.append(
                BoneDef(
                    name=str(item["name"]),
                    parent=item.get("parent"),
                    local=(float(local[0]), float(local[1])),
                    length=float(item.get("length") or 0.0),
                    angle=float(item.get("angle") or 0.0),
                    width=float(item.get("width") or 0.08),
                    region=str(item.get("region") or item.get("name") or ""),
                    weight=float(item.get("weight") or 1.0),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        return cls(
            name=str(data.get("name") or "body"),
            bones=bones,
            particle_density=float(data.get("particle_density") or 1.0),
            default_animation=str(data.get("default_animation") or "idle"),
            head_radius=float(data.get("head_radius") or 0.13),
        )

    def make_skeleton(self) -> Skeleton:
        bones = [
            Bone(
                name=b.name,
                parent=b.parent,
                local_x=b.local[0],
                local_y=b.local[1],
                length=b.length,
                angle=b.angle,
                rest_angle=b.angle,
                width=b.width,
                rest_width=b.width,
                region=b.region or b.name,
                enabled=b.enabled,
                weight=b.weight,
            )
            for b in self.bones
        ]
        skel = Skeleton(bones=bones)
        skel.rebuild_index()
        skel.solve()
        return skel


def _b(
    name: str,
    parent: str | None,
    *,
    local: tuple[float, float] = (0.0, 0.0),
    length: float = 0.0,
    angle: float = 0.0,
    width: float = 0.08,
    region: str = "",
    weight: float = 1.0,
) -> BoneDef:
    return BoneDef(
        name=name,
        parent=parent,
        local=local,
        length=length,
        angle=angle,
        width=width,
        region=region or name,
        weight=weight,
    )


def humanoid_definition() -> BodyDefinition:
    """Front-facing female proportions with hair. y+ is down (overlay space)."""
    return BodyDefinition(
        name="humanoid",
        particle_density=1.2,
        head_radius=0.115,
        bones=[
            _b("root", None, width=0.02, region="pelvis", weight=0.05),
            _b("pelvis", "root", local=(0.0, 0.14), length=0.0, width=0.145, region="pelvis", weight=1.4),
            _b("spine", "pelvis", length=0.26, angle=-_HALF, width=0.088, region="torso", weight=2.8),
            _b("chest", "spine", length=0.05, angle=0.0, width=0.105, region="torso", weight=2.0),
            _b("neck", "chest", length=0.05, angle=0.0, width=0.024, region="neck", weight=0.35),
            _b("head", "neck", length=0.02, angle=0.0, width=0.20, region="head", weight=4.4),
            _b("hair_crown", "head", local=(0.0, -0.04), length=0.0, width=0.18, region="hair", weight=1.8),
            _b("hair_bang", "head", local=(0.02, 0.02), length=0.09, angle=math.pi * 0.92, width=0.042, region="hair", weight=0.7),
            _b(
                "hair_l",
                "head",
                local=(-0.08, 0.01),
                length=0.18,
                angle=math.pi * 0.78,
                width=0.038,
                region="hair",
                weight=1.2,
            ),
            _b(
                "hair_r",
                "head",
                local=(0.08, 0.01),
                length=0.18,
                angle=math.pi * 1.22,
                width=0.038,
                region="hair",
                weight=1.2,
            ),
            _b(
                "hair_tail",
                "head",
                local=(0.04, 0.05),
                length=0.26,
                angle=math.pi * 0.92,
                width=0.048,
                region="hair",
                weight=1.6,
            ),
            _b("l_shoulder", "chest", local=(-0.12, 0.03), length=0.0, width=0.028, region="left_arm", weight=0.15),
            _b(
                "l_upper_arm",
                "l_shoulder",
                length=0.16,
                angle=_HALF + 0.38,
                width=0.032,
                region="left_arm",
                weight=1.5,
            ),
            _b("l_lower_arm", "l_upper_arm", length=0.14, angle=0.12, width=0.026, region="left_arm", weight=1.2),
            _b("l_hand", "l_lower_arm", length=0.035, angle=0.04, width=0.024, region="left_hand", weight=0.45),
            _b("r_shoulder", "chest", local=(0.12, 0.03), length=0.0, width=0.028, region="right_arm", weight=0.15),
            _b(
                "r_upper_arm",
                "r_shoulder",
                length=0.16,
                angle=_HALF - 0.38,
                width=0.032,
                region="right_arm",
                weight=1.5,
            ),
            _b("r_lower_arm", "r_upper_arm", length=0.14, angle=-0.12, width=0.026, region="right_arm", weight=1.2),
            _b("r_hand", "r_lower_arm", length=0.035, angle=-0.04, width=0.024, region="right_hand", weight=0.45),
            _b(
                "l_upper_leg",
                "pelvis",
                local=(-0.07, 0.02),
                length=0.21,
                angle=_HALF + 0.10,
                width=0.048,
                region="left_leg",
                weight=2.1,
            ),
            _b("l_lower_leg", "l_upper_leg", length=0.19, angle=-0.02, width=0.036, region="left_leg", weight=1.7),
            _b("l_foot", "l_lower_leg", length=0.04, angle=0.12, width=0.032, region="left_foot", weight=0.45),
            _b(
                "r_upper_leg",
                "pelvis",
                local=(0.07, 0.02),
                length=0.21,
                angle=_HALF - 0.10,
                width=0.048,
                region="right_leg",
                weight=2.1,
            ),
            _b("r_lower_leg", "r_upper_leg", length=0.19, angle=0.02, width=0.036, region="right_leg", weight=1.7),
            _b("r_foot", "r_lower_leg", length=0.04, angle=-0.12, width=0.032, region="right_foot", weight=0.45),
        ],
    )


def robot_definition() -> BodyDefinition:
    """Blockier humanoid: wider capsules, shorter neck."""
    body = humanoid_definition()
    body.name = "robot"
    body.head_radius = 0.11
    wider = {
        "head": 0.22,
        "chest": 0.22,
        "spine": 0.18,
        "pelvis": 0.17,
        "l_upper_arm": 0.07,
        "r_upper_arm": 0.07,
        "l_upper_leg": 0.09,
        "r_upper_leg": 0.09,
        "neck": 0.07,
    }
    for bone in body.bones:
        if bone.name in wider:
            bone.width = wider[bone.name]
        if bone.name == "neck":
            bone.length = 0.035
        if bone.name in ("l_shoulder", "r_shoulder"):
            bone.local = (bone.local[0] * 1.05, bone.local[1])
    return body
