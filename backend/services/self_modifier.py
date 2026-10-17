"""Self-heal and hot-restart for cat-crew-ai.

Lets CatCrewAI modify her own code, test it in a subprocess sandbox, and if it
fails, ask the Coding model for a fix up to SELF_HEAL_MAX_ATTEMPTS times. After a
successful test the app restarts with os.execl so the new code loads.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


from backend.config import BASE_DIR, SELF_HEAL_MAX_ATTEMPTS, SELF_HEAL_TEST_TIMEOUT


def _coding_brain() -> tuple[str, str]:
    """The Coding pick (Chat when unset), with the current project's override when there is one."""
    from backend.services.memory import _role_brain

    host, model = _role_brain("coding")
    if not model:
        host, model = _role_brain("companion")
    return host, model


def _ask_local(prompt: str, system: str = "") -> str:
    """One no-tools call on the Coding model (Chat when Coding is unset)."""
    from backend.core.provider_factory import provider_from_brain

    host, model = _coding_brain()
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    return str(provider_from_brain(host, model).chat(messages).content or "")


def _extract_python_code(text: str) -> str:
    """Pull Python code out of the reply (strip ```python fences)."""
    text = (text or "").strip()
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.DOTALL)
    if blocks:
        return blocks[-1].strip()
    return text


# --- Code edits ---

def modify_code(file_path: str | Path, content: str) -> Path:
    """Write new contents to a .py file (project tree only)."""
    path = Path(file_path).resolve()
    base = BASE_DIR.resolve()

    if path.suffix != ".py":
        raise ValueError(f"I can only modify .py files: {file_path}")
    try:
        path.relative_to(base)
    except ValueError:
        raise ValueError(f"File is outside the project: {file_path}")

    path.write_text(content, encoding="utf-8")
    return path


# --- Test sandbox ---

def _compute_module_name(path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(BASE_DIR.resolve())
    except ValueError:
        return None
    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def sandbox_test(
    file_path: str | Path, timeout: int = SELF_HEAL_TEST_TIMEOUT
) -> tuple[bool, str]:
    """Run the script in a subprocess sandbox; return (ok, output/error)."""
    path = Path(file_path).resolve()

    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()

    module = _compute_module_name(path)
    if module:
        cmd = [sys.executable, "-c", f"import {module}"]
        cwd = str(BASE_DIR)
    else:
        cmd = [sys.executable, str(path)]
        cwd = str(path.parent)

    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except subprocess.TimeoutExpired:
        return False, f"Test timed out ({timeout}s)."

    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()

    return True, (r.stdout or "").strip()

# --- Generate / heal ---

def generate_code(
    file_path: str | Path,
    instruction: str,
    current: str | None = None,
    error: str | None = None,
) -> str:
    """Ask the Coding model for new (or fixed) code for a file."""
    system = (
        "You are an experienced Python programmer. Return ONLY the full, valid "
        "Python source of the file — no explanations and no markdown fences."
    )
    if current is None:
        try:
            existing = Path(file_path).read_text(encoding="utf-8")
            if existing.strip():
                current = existing
        except OSError:
            current = None

    if current is not None and error:
        prompt = (
            f"File: {file_path}\n"
            f"Task: {instruction}\n\n"
            f"Current code:\n```python\n{current}\n```\n\n"
            f"This code fails tests with:\n{error}\n\n"
            f"Fix the error and return ONLY the full corrected Python code."
        )
    elif current is not None:
        prompt = (
            f"File: {file_path}\n"
            f"Task: {instruction}\n\n"
            f"Current code:\n```python\n{current}\n```\n\n"
            f"Make the requested change and return ONLY the full Python file."
        )
    else:
        prompt = (
            f"File: {file_path}\n"
            f"Task: {instruction}\n\n"
            f"Return ONLY the full Python source of the file."
        )
    return _extract_python_code(_ask_local(prompt, system=system))


def restart_app() -> None:
    """Hot-restart the backend server (``python -m backend``) in place.

    The frontend reconnects its WebSocket on its own once the server is back.
    """
    from backend.services.instance_lock import release

    release()
    python = sys.executable
    os.execl(python, python, "-m", "backend")


def self_heal(
    file_path: str,
    instruction: str,
    max_attempts: int = SELF_HEAL_MAX_ATTEMPTS,
) -> dict:
    """Loop: generate -> write -> test -> heal (up to max_attempts)."""
    path = Path(file_path)

    try:
        code = generate_code(path, instruction)
        modify_code(path, code)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "file": str(path), "attempts": 0, "error": str(exc)}

    last_output = ""
    for attempt in range(1, max_attempts + 1):
        ok, output = sandbox_test(path)
        if ok:
            return {"ok": True, "file": str(path), "attempts": attempt, "error": ""}

        last_output = output
        if attempt < max_attempts:
            try:
                code = generate_code(path, instruction, current=code, error=output)
                modify_code(path, code)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "file": str(path),
                    "attempts": attempt,
                    "error": str(exc),
                }

    return {
        "ok": False,
        "file": str(path),
        "attempts": max_attempts,
        "error": last_output,
    }


def self_modify(file_path: str, instruction: str) -> str:
    """Tool entry: self-heal, then hot-restart on success with verify-resume flag."""
    result = self_heal(file_path, instruction)

    if result["ok"]:
        from backend.services.pending_restart import note_catcrew_source_edit, schedule_self_modify_restart

        note_catcrew_source_edit(result["file"])
        msg = schedule_self_modify_restart(
            reason=f"self_modify: {instruction}",
            goal=str(instruction or "").strip(),
            files=[str(result["file"])],
            delay=3.0,
        )
        return (
            f"Updated '{result['file']}' (after {result['attempts']} "
            f"attempt(s)). Tests passed. {msg}"
        )

    return (
        f"Could not self-heal after {result['attempts']} attempt(s) for "
        f"'{result['file']}'. Last error:\n{result['error']}"
    )

