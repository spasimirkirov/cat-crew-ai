"""What a model can do, worked out from what its host tells us.

Four capability tags are shown in the UI and used to warn about a poor fit for a
role:

* ``vision``    accepts images (screenshots).
* ``reasoning`` thinks step by step before answering (deep-thinking models).
* ``tools``     supports native function calling.
* ``chat``      plain conversation. Every usable text model has it.

Two more tags mark models that are *not* chat models and are kept out of the
pickers: ``embed`` (embeddings) and ``image`` (image generation).

A host's own metadata always wins over a guess from the model id: a host may say
``vlm`` and ``tool_use``, Gemini lists the methods a model supports, Groq marks
retired models ``active: false``. Names are the fallback, matched on whole words
so ``r1`` does not fire inside ``gpt-4r1x``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

CAP_ORDER = ("vision", "reasoning", "tools", "chat")
ALL_CAPS = CAP_ORDER + ("embed", "image")

# Substrings that mark a model as taking images.
_VISION_PARTS = (
    "vision",
    "llava",
    "minicpm-v",
    "moondream",
    "pixtral",
    "internvl",
    "molmo",
    "smolvlm",
    "paligemma",
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "gemini",
    "gemma3",
    "gemma-3",
    "gemma4",
    "gemma-4",
    "claude-3",
    "claude-4",
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-haiku-4",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-5",
    "llama-4",
    "llama4",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
)
# Whole words (see _words).
_VISION_TOKENS = {"vl", "vlm", "vision"}

_REASONING_PARTS = (
    "reasoner",
    "thinking",
    "deepseek-r1",
    "qwq",
    "gpt-oss",
    "magistral",
    "reasoning",
)
_REASONING_TOKENS = {"r1", "o1", "o3", "o4"}

_TOOLS_PARTS = ("function-calling", "function_calling", "tool-use", "tool_use", "-tools")

_EMBED_PARTS = ("embed", "rerank", "bge-")
_IMAGE_PARTS = (
    "flux",
    "sdxl",
    "stable-diffusion",
    "sd3",
    "imagegen",
    "imagen",
    "dall-e",
    "janus",
    "flash-image",
    "image-preview",
    "image-generation",
)
# Not chat models at all (speech, moderation, safety classifiers).
_NOT_CHAT_PARTS = ("whisper", "tts", "moderation", "guard", "aqa", "transcribe", "speech")

# Some model families are known not to call tools.
_NO_TOOLS_PARTS = ("gemma-2", "gemma2", "llava", "moondream", "deepseek-r1-distill", "o1-mini", "o1-preview", "llama-guard")


def _has_any(low: str, parts: Iterable[str]) -> bool:
    return any(part in low for part in parts)


def _words(low: str) -> set[str]:
    """Whole words of a model id, split on ``- _ / : @`` and spaces (``qwen2.5-vl-7b`` -> qwen2.5, vl, 7b)."""
    return {t for t in re.split(r"[\s/_:@\-]+", low) if t}


def kind_of(name: str, meta: dict[str, Any] | None = None) -> str:
    """``"chat"`` for a usable text model, else ``"embed"``, ``"image"`` or ``"skip"``."""
    meta = meta or {}
    explicit = str(meta.get("kind") or "").lower()
    if explicit in {"embed", "embedding", "embeddings"}:
        return "embed"
    if explicit in {"image", "imagegen"}:
        return "image"
    if explicit in {"skip", "not_chat"}:
        return "skip"
    low = (name or "").lower()
    if _has_any(low, _NOT_CHAT_PARTS):
        return "skip"
    if _has_any(low, _EMBED_PARTS):
        return "embed"
    if _has_any(low, _IMAGE_PARTS):
        return "image"
    return "chat"


def classify(name: str, meta: dict[str, Any] | None = None) -> list[str]:
    """Capability tags for one model, in display order.

    ``meta`` carries what the host reported: ``vision`` / ``tools`` / ``reasoning``
    as ``True`` (it said so), ``False`` (it said not) or absent (guess from the name),
    plus ``kind`` for embeddings and image models.
    """
    meta = meta or {}
    kind = kind_of(name, meta)
    if kind in {"embed", "image"}:
        return [kind]
    if kind == "skip":
        return []
    low = (name or "").lower()
    words = _words(low)

    def decide(key: str, guess: bool) -> bool:
        said = meta.get(key)
        return bool(said) if said is not None else guess

    vision = decide("vision", _has_any(low, _VISION_PARTS) or bool(words & _VISION_TOKENS))
    reasoning = decide("reasoning", _has_any(low, _REASONING_PARTS) or bool(words & _REASONING_TOKENS))
    # Tool calling is the norm for current chat models; only families known not to have it are excluded.
    tools = decide("tools", _has_any(low, _TOOLS_PARTS) or not _has_any(low, _NO_TOOLS_PARTS))
    found = {"vision": vision, "reasoning": reasoning, "tools": tools, "chat": True}
    return [cap for cap in CAP_ORDER if found[cap]]


def merge_caps(*groups: Iterable[str]) -> list[str]:
    """Union of capability lists in display order (used when two sources describe one model)."""
    seen = {cap for group in groups for cap in group}
    ordered = [cap for cap in ALL_CAPS if cap in seen]
    return ordered


# Roles that need a particular capability to do their job. Only a warning: a model
# without it still works, it is just a poor fit.
ROLE_NEEDS: dict[str, tuple[str, str]] = {
    "vision": ("vision", "Vision needs a model that accepts images."),
    "coding": ("tools", "Coding works best with a model that supports tool calling."),
    "planning": ("reasoning", "Planning works best with a reasoning model."),
}


def role_fit_warning(role: str, caps: Iterable[str]) -> str:
    """A one-line warning when ``caps`` lacks what ``role`` needs, else ``""``."""
    need = ROLE_NEEDS.get(role)
    if need is None:
        return ""
    cap, message = need
    return "" if cap in set(caps) else message
