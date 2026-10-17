"""Code Mode — run a short Python script; only stdout/stderr enter context."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from ..session_fs import ensure_session_work_dir

_BLOCK = re.compile(
    r"\b(os\.system|subprocess|ctypes|socket|urllib\.request|"
    r"requests\.|httpx\.|shutil\.rmtree|Path\.unlink|"
    r"__import__\s*\(\s*['\"]os['\"]|"
    r"open\s*\([^)]*['\"]w)",
    re.I,
)
_MAX_CODE = 12_000
_OUT_CAP = 8_000


def run_code_mode(code: str, *, timeout: int = 30) -> str:
    """Write ``code`` under session scratch and run it with the app's Python."""
    raw = str(code or "").strip()
    if not raw:
        return "Error: run_code_mode needs Python source."
    if len(raw) > _MAX_CODE:
        return f"Error: code too long ({len(raw)} > {_MAX_CODE})."
    if _BLOCK.search(raw):
        return (
            "Refused: code mode blocks shell/network/destructive APIs. "
            "Use run_command or file tools for those."
        )

    folder = ensure_session_work_dir() / "code_mode"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"script-{uuid.uuid4().hex[:10]}.py"
    path.write_text(raw + ("\n" if not raw.endswith("\n") else ""), encoding="utf-8")

    import os
    import sys

    from ..terminal import run_foreground
    from ..workspace import current_workspace

    py = sys.executable or "python"
    cmd = f'"{py}" "{path}"'
    workdir = current_workspace()
    if not workdir.is_dir():
        workdir = path.parent
    limit = max(5, min(int(timeout or 30), 120))
    try:
        out = run_foreground(cmd, workdir, timeout=limit, env=os.environ.copy())
    except Exception as exc:  # noqa: BLE001
        return f"Code mode error: {exc}\nScript: {path}"
    text = (out or "").strip() or "(no output)"
    if len(text) > _OUT_CAP:
        text = text[:_OUT_CAP] + f"\n... [truncated, {len(out)} chars]"
    return f"[code mode stdout]\nScript: {path}\n---\n{text}"
