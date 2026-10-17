"""Image generation over OpenRouter's Unified Image API -- the alternative to local ComfyUI for the
Images role. Rides on the same OpenRouter key already configured for chat models: no key of its own.

Wire contract (OpenRouter's own docs, ``openrouter.ai/docs/guides/overview/multimodal/image-generation``,
September 2026): ``POST {base_url}/images`` (a dedicated endpoint, not chat completions), body
``{model, prompt, size?, seed?, ...}``, response ``{"data": [{"b64_json": "...", "media_type": "image/png"}], ...}``.
Images always come back base64-encoded, never as a URL -- decoded and written to disk here, same as
every image ``generate_image`` returns regardless of which host made it.

OpenRouter has no negative-prompt field, so a ``negative`` is folded into the prompt text instead of
dropped silently. There is also no separate width/height pair -- both map to the documented ``size``
parameter (``"WIDTHxHEIGHT"``) when both are given, and are otherwise left to the model's own default.
"""

from __future__ import annotations

import base64
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from backend.config import BASE_DIR

GENERATED_DIR = BASE_DIR / "data" / "generated"
_TIMEOUT_S = 120.0
_EXT_BY_MEDIA_TYPE = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}


def _endpoint() -> str:
    from backend.services.api_access import compat_root, spec_for

    spec = spec_for("openrouter")
    base = spec.base_url if spec else ""
    return f"{compat_root(base)}/images"


def generate_image(
    prompt: str,
    model: str,
    *,
    negative: str = "",
    width: int = 0,
    height: int = 0,
    seed: int = -1,
) -> Path:
    """Generate one image with an OpenRouter image-generation model. Raises ``RuntimeError`` with a
    safe, user-facing message on any failure -- never a raw exception, never the API key."""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("Empty image prompt")
    if negative and negative.strip():
        text = f"{text}\n\nAvoid: {negative.strip()}"
    model = (model or "").strip()
    if not model:
        raise RuntimeError("No OpenRouter image model picked. Open Models, pick Images -> OpenRouter, then a model.")

    from backend.services import api_access

    key = api_access.resolved_key("openrouter")
    if not key:
        raise RuntimeError("No OpenRouter API key is configured. Add one on the Models page (Hosts).")

    body: dict[str, Any] = {"model": model, "prompt": text}
    if width and height:
        body["size"] = f"{int(width)}x{int(height)}"
    if seed is not None and seed >= 0:
        body["seed"] = int(seed)

    try:
        response = requests.post(
            _endpoint(),
            json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=_TIMEOUT_S,
        )
    except requests.Timeout:
        raise RuntimeError("OpenRouter did not answer in time generating the image.") from None
    except requests.ConnectionError:
        raise RuntimeError("Could not reach OpenRouter to generate the image.") from None
    except requests.RequestException:
        raise RuntimeError("The OpenRouter image request failed.") from None

    if response.status_code == 401:
        raise RuntimeError("OpenRouter rejected the API key generating the image.")
    if response.status_code >= 400:
        reason = ""
        try:
            payload = response.json()
            reason = str((payload.get("error") or {}).get("message") or payload.get("error") or "").strip()
        except ValueError:
            pass
        raise RuntimeError(f"OpenRouter answered HTTP {response.status_code} generating the image." + (f" ({reason[:200]})" if reason else ""))

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError("OpenRouter's image response was not valid JSON.") from None

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not rows or not isinstance(rows, list):
        raise RuntimeError("OpenRouter finished but returned no image.")
    first = rows[0] if isinstance(rows[0], dict) else {}
    b64 = str(first.get("b64_json") or "")
    if not b64:
        raise RuntimeError("OpenRouter's response had no image data.")
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:
        raise RuntimeError("OpenRouter's image data could not be decoded.") from None

    ext = _EXT_BY_MEDIA_TYPE.get(str(first.get("media_type") or "").lower(), "png")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    out = GENERATED_DIR / f"openrouter_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
    out.write_bytes(raw)
    return out


def format_saved(paths: list[Path], caption: str = "") -> str:
    if not paths:
        return "Image generation finished but returned no files."
    label = (caption or "Generated").replace("\n", " ")[:80]
    lines = []
    for path in paths:
        posix = path.as_posix()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
            lines.append(f"Saved image: {path}\n\n![{label}]({posix})")
        else:
            lines.append(f"Saved file: {path}")
    return "\n\n".join(lines)
