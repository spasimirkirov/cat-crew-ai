"""Code Mode — run a short Python script; only stdout/stderr enter context.

This is a context-saving tool, not a sandbox. The script runs as an ordinary Python process with
the same rights as ``run_command`` (any file it can reach, any network), so it goes through the
same approval gate (``harness/approvals.DEFAULT_DESTRUCTIVE``) and gets the same scrubbed
environment (no API keys, ``tools.subprocess_env``). ``_STEER`` below only redirects a model that
reaches for a shell or the network to the tool meant for it; a determined script gets past a
regular expression, and nothing here pretends otherwise."""

from __future__ import annotations

import re
import uuid

from backend.services.session_fs import ensure_session_work_dir

# A nudge, not a fence: see the module docstring.
_STEER = re.compile(
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
    if _STEER.search(raw):
        return (
            "Refused: code mode is for transforming data. Run shell commands, network calls "
            "and deletions with run_command or the file tools, where the person can see them."
        )

    folder = ensure_session_work_dir() / "code_mode"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"script-{uuid.uuid4().hex[:10]}.py"
    path.write_text(raw + ("\n" if not raw.endswith("\n") else ""), encoding="utf-8")

    import sys

    from backend.services.terminal import run_foreground
    from backend.tools import subprocess_env
    from backend.services.workspace import current_workspace

    py = sys.executable or "python"
    # -B: no .pyc litter next to the scratch scripts.
    cmd = f'"{py}" -B "{path}"'
    workdir = current_workspace()
    if not workdir.is_dir():
        workdir = path.parent
    limit = max(5, min(int(timeout or 30), 120))
    try:
        out = run_foreground(cmd, workdir, timeout=limit, env=subprocess_env())
    except Exception as exc:  # noqa: BLE001
        return f"Code mode error: {exc}\nScript: {path}"
    text = (out or "").strip() or "(no output)"
    if len(text) > _OUT_CAP:
        text = text[:_OUT_CAP] + f"\n... [truncated, {len(out)} chars]"
    return f"[code mode stdout]\nScript: {path}\n---\n{text}"
