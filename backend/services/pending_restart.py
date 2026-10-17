"""Durable flag: after self-modify restart, resume verify once — then stop.

Written when ``restart_catcrew`` / ``self_modify`` are about to reboot the app.
Consumed once on the next ChatWindow ready so CatCrewAI continues with tools
(read_file / screenshot / tests) instead of waiting for the user.

Hard rules (anti-loop):
- ``restart_catcrew`` only after CatCrewAI source was edited **in this process**.
- Verify pass must **not** restart unless she made new edits.
- Cap generations so a stuck model cannot reboot forever.
- Resume text is for the model only (hidden chat turn).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from backend.config import BASE_DIR

PENDING_PATH = BASE_DIR / "data" / "pending_self_modify.json"

# How many verify→fix→restart cycles we allow for one user goal.
MAX_GENERATIONS = 3

_touched_catcrew: list[str] = []
_restart_scheduled = False
# Set when a pending verify pass is consumed after boot.
_resume_generation = 0
_resume_active = False
_resume_goal = ""


def note_catcrew_source_edit(path: Path | str) -> None:
    """Record a successful edit under the CatCrewAI install tree (this process)."""
    try:
        resolved = Path(path).resolve()
        base = BASE_DIR.resolve()
        resolved.relative_to(base)
    except (OSError, ValueError):
        return
    rel = str(resolved.relative_to(BASE_DIR.resolve())).replace("\\", "/")
    if rel not in _touched_catcrew:
        _touched_catcrew.append(rel)


def touched_catcrew_files() -> list[str]:
    return list(_touched_catcrew)


def clear_touched_catcrew_files() -> None:
    _touched_catcrew.clear()


def mark_restart_scheduled() -> None:
    global _restart_scheduled
    _restart_scheduled = True


def begin_verify_pass(pending: dict[str, Any]) -> None:
    """Call once after consuming the pending flag on boot."""
    global _resume_generation, _resume_active, _resume_goal
    try:
        _resume_generation = max(1, int(pending.get("generation") or 1))
    except (TypeError, ValueError):
        _resume_generation = 1
    _resume_active = True
    _resume_goal = str(pending.get("goal") or pending.get("reason") or "").strip()
    clear_touched_catcrew_files()


def reset_runtime_state() -> None:
    """Test helper — clear in-process restart / verify flags."""
    global _restart_scheduled, _resume_generation, _resume_active, _resume_goal
    clear_touched_catcrew_files()
    _restart_scheduled = False
    _resume_generation = 0
    _resume_active = False
    _resume_goal = ""


def _active_session() -> str:
    try:
        from backend.services.sessions import active_session_id
        from backend.services.trace import current_session_id

        return str(current_session_id() or active_session_id() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _todo_goal() -> str:
    try:
        from backend.services.todo import load_board

        board = load_board()
        return str(board.goal or "").strip() if board else ""
    except Exception:  # noqa: BLE001
        return ""


def write_pending(
    *,
    reason: str = "",
    goal: str = "",
    files: list[str] | None = None,
    session_id: str = "",
    generation: int = 1,
) -> Path:
    """Persist a one-shot resume payload before ``os.execl``."""
    sid = (session_id or _active_session()).strip()
    file_list = [
        str(f).replace("\\", "/")
        for f in (files or touched_catcrew_files())
        if str(f).strip()
    ]
    seen: set[str] = set()
    uniq_files: list[str] = []
    for item in file_list:
        if item not in seen:
            seen.add(item)
            uniq_files.append(item)
    payload: dict[str, Any] = {
        "kind": "self_modify_verify",
        "session_id": sid,
        "reason": (reason or "").strip(),
        "goal": (goal or _todo_goal() or "").strip(),
        "files": uniq_files,
        "generation": max(1, int(generation)),
        "max_generations": MAX_GENERATIONS,
        "created_at": time.time(),
    }
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return PENDING_PATH


def peek_pending() -> dict[str, Any] | None:
    if not PENDING_PATH.is_file():
        return None
    try:
        data = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def take_pending() -> dict[str, Any] | None:
    """Read and delete the pending flag (one-shot)."""
    data = peek_pending()
    try:
        if PENDING_PATH.is_file():
            PENDING_PATH.unlink()
    except OSError:
        pass
    return data


def format_resume_message(pending: dict[str, Any]) -> str:
    """Internal verify prompt — never shown as a normal user bubble."""
    goal = str(pending.get("goal") or "").strip()
    reason = str(pending.get("reason") or "").strip()
    files = pending.get("files") if isinstance(pending.get("files"), list) else []
    file_lines = (
        "\n".join(f"- {f}" for f in files if str(f).strip())
        or "- (see recent backend edits)"
    )
    try:
        generation = max(1, int(pending.get("generation") or 1))
    except (TypeError, ValueError):
        generation = 1
    try:
        max_gen = max(1, int(pending.get("max_generations") or MAX_GENERATIONS))
    except (TypeError, ValueError):
        max_gen = MAX_GENERATIONS

    parts = [
        "[INTERNAL SELF-MODIFY VERIFY — invisible to the user]",
        f"Verify pass {generation} of {max_gen} after restart. New code is loaded.",
        "Do not wait for the user. Do not quit. Do not flag developers.",
        "Do NOT paste this block into the chat. The user must never see it.",
    ]
    if goal:
        parts.append(f"Goal: {goal}")
    if reason:
        parts.append(f"Restart reason: {reason}")
    parts.append("Files to check:")
    parts.append(file_lines)
    parts.append(
        "Steps:\n"
        "1) Verify with tools only (read_file / screenshot / compile / pytest as needed).\n"
        "2) If the change is CORRECT: reply with a short user-facing confirmation. "
        "Do NOT call restart_catcrew. The task is DONE — stop.\n"
        "3) If the change is WRONG: edit with replace_in_file/write_file, then call "
        "restart_catcrew ONCE (only after those new edits). Never restart just to re-check.\n"
        "restart_catcrew is refused when you have not edited CatCrewAI source in this process."
    )
    if generation >= max_gen:
        parts.append(
            f"This is the LAST allowed verify pass ({generation}/{max_gen}). "
            "Do not call restart_catcrew. Report the outcome to the user and stop."
        )
    return "\n".join(parts)


def refuse_restart_message(*, reason: str) -> str:
    return (
        f"Refused restart_catcrew: {reason} "
        "If the change already looks correct, tell the user you are done — "
        "do not restart again."
    )


def can_schedule_restart(*, force: bool = False) -> str | None:
    """Return None if OK to restart, else a human refusal string."""
    if _restart_scheduled:
        return "a restart is already scheduled in this process"
    if _resume_active:
        next_gen = _resume_generation + 1
        if next_gen > MAX_GENERATIONS:
            return (
                f"verify generation cap reached "
                f"({_resume_generation}/{MAX_GENERATIONS})"
            )
    if force:
        return None
    if not touched_catcrew_files():
        if _resume_active:
            return (
                "no new CatCrewAI source edits in this verify pass "
                "(reading/checking alone is not enough)"
            )
        return "no CatCrewAI source edits in this process yet"
    return None


def next_restart_generation() -> int:
    if _resume_active:
        return _resume_generation + 1
    return 1


def schedule_self_modify_restart(
    *,
    reason: str = "",
    goal: str = "",
    files: list[str] | None = None,
    delay: float = 1.5,
    force: bool = False,
) -> str:
    """Write the resume flag, then reboot into ``main.py`` after ``delay`` seconds."""
    import threading

    from backend.services.self_modifier import restart_app

    blocked = can_schedule_restart(force=bool(force))
    if blocked:
        return refuse_restart_message(reason=blocked)

    file_list = list(files) if isinstance(files, list) else None
    if not file_list:
        file_list = touched_catcrew_files()
    generation = next_restart_generation()
    path = write_pending(
        reason=reason,
        goal=goal or _resume_goal,
        files=file_list,
        generation=generation,
    )
    clear_touched_catcrew_files()
    mark_restart_scheduled()
    threading.Timer(max(0.2, float(delay)), restart_app).start()
    return (
        f"Restarting CatCrewAI in {delay:g}s so new code loads "
        f"(verify pass {generation}/{MAX_GENERATIONS}). "
        f"After boot I will auto-verify once "
        f"(flag: {path.name}). Do not call restart_catcrew again unless you edit more files."
    )


def maybe_auto_restart_after_edits(
    *,
    reason: str = "",
    goal: str = "",
    delay: float = 1.5,
) -> str:
    """End-of-turn helper: if she edited CatCrewAI source and forgot restart_catcrew, do it."""
    if _restart_scheduled:
        return ""
    try:
        from backend.harness.runtime import nest_depth

        if nest_depth() > 0:
            return ""
    except Exception:  # noqa: BLE001
        pass
    if not touched_catcrew_files():
        return ""
    blocked = can_schedule_restart(force=False)
    if blocked:
        return ""
    return schedule_self_modify_restart(
        reason=reason or "auto: CatCrewAI source edited this turn",
        goal=goal or _resume_goal,
        files=touched_catcrew_files(),
        delay=delay,
        force=False,
    )
