"""Overlay / body state. Does not duplicate Life."""

from __future__ import annotations

from enum import Enum


class BodyState(str, Enum):
    IDLE = "idle"
    TALKING = "talking"
    LISTENING = "listening"
    THINKING = "thinking"
    WAVE = "wave"
    WALK = "walk"
    SLEEPING = "sleeping"
    HAPPY = "happy"
    SURPRISED = "surprised"
    CUSTOM = "custom"


_OVERLAY_TO_STATE = {
    "idle": BodyState.IDLE,
    "listening": BodyState.LISTENING,
    "thinking": BodyState.THINKING,
    "speaking": BodyState.TALKING,
    "tool": BodyState.CUSTOM,
    "error": BodyState.SURPRISED,
    "muted": BodyState.IDLE,
    "sleeping": BodyState.SLEEPING,
}

_STATE_TO_CLIP = {
    BodyState.IDLE: "idle",
    BodyState.TALKING: "idle",
    BodyState.LISTENING: "listening",
    BodyState.THINKING: "think",
    BodyState.WAVE: "wave",
    BodyState.WALK: "walk",
    BodyState.SLEEPING: "sleep",
    BodyState.HAPPY: "happy",
    BodyState.SURPRISED: "surprised",
    BodyState.CUSTOM: "idle",
}


def overlay_to_state(mode: str) -> BodyState:
    return _OVERLAY_TO_STATE.get(str(mode or "idle"), BodyState.IDLE)


def clip_for_state(state: BodyState) -> str:
    return _STATE_TO_CLIP.get(state, "idle")
