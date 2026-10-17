"""Offload oversized tool results to session scratch (TrueForge-style).

Keeps full output on disk and injects a short preview + path into the model
context so local/cloud API turns stay cheaper.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from ..config import TOOL_RESULT_LIMIT
from backend.services.session_fs import ensure_session_work_dir

# Preview kept in the model context; full body lives on disk.
OFFLOAD_THRESHOLD = max(4_000, min(TOOL_RESULT_LIMIT, 8_000))
_PREVIEW = 1_200

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def maybe_offload_tool_result(
    name: str,
    result: str,
    *,
    threshold: int = OFFLOAD_THRESHOLD,
    preview: int = _PREVIEW,
) -> str:
    """If ``result`` is huge, write it under ``data/scratch/<session>/tool_results/``."""
    text = result if isinstance(result, str) else str(result)
    if len(text) <= max(500, int(threshold)):
        return text
    try:
        folder = ensure_session_work_dir() / "tool_results"
        folder.mkdir(parents=True, exist_ok=True)
        stem = _SAFE.sub("-", str(name or "tool").strip())[:40] or "tool"
        path = folder / f"{stem}-{uuid.uuid4().hex[:8]}.txt"
        path.write_text(text, encoding="utf-8")
    except OSError:
        # Fall back to truncate if disk fails.
        return text[:threshold] + f"\n... [truncated, {len(text)} characters total]"
    head = text[: max(200, int(preview))].rstrip()
    return (
        f"[large tool result offloaded — {len(text)} characters]\n"
        f"Path: {path}\n"
        f"Read with read_file if you need more than this preview.\n"
        f"--- preview ---\n"
        f"{head}\n"
        f"... [end preview]"
    )


def offload_path_for_tests(path: Path) -> bool:
    return path.is_file() and "tool_results" in path.parts
