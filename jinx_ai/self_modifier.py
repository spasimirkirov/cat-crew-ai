"""Self-heal and hot-restart for jinx-ai.

Lets Jinx modify her own code, test it in a subprocess sandbox, and if it
fails, ask llama-server for a fix up to SELF_HEAL_MAX_ATTEMPTS times. After a
successful test the app restarts with os.execl so the new code loads.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import requests

from .config import (
    BASE_DIR,
    LLAMA_SERVER_URL,
    SELF_HEAL_MAX_ATTEMPTS,
    SELF_HEAL_TEST_TIMEOUT,
)


def _ask_local(prompt: str, system: str = "") -> str:
    from .settings_store import resolve_role_brain
    from .llama_server import server_v1

    _host, model = resolve_role_brain("coding")
    if not model:
        _host, model = resolve_role_brain("companion")
    root = server_v1(LLAMA_SERVER_URL)
    payload = {
        "model": model or "local",
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.2,
    }
    resp = requests.post(
        f"{root}/chat/completions",
        json=payload,
        timeout=300,
        headers={"Authorization": "Bearer jinx"},
    )
    resp.raise_for_status()
    return str(((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "")


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
    """Ask the local llama-server for new (or fixed) code for a file."""
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
    """Hot-restart the GUI via ``main.py`` (not whatever script spawned the agent)."""
    from .single_instance import release

    release()
    python = sys.executable
    main_py = str((BASE_DIR / "main.py").resolve())
    os.execl(python, python, main_py)


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
        from .pending_restart import note_jinx_source_edit, schedule_self_modify_restart

        note_jinx_source_edit(result["file"])
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

