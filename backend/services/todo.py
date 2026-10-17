"""Harness-owned task checklist on disk. The model does not own this file.

Lives at ``data/scratch/<session-id>/TODO.md``. Python seeds it from the
clarify spec and injects a short snapshot into the system prompt so "continue"
still sees open work after compaction / round caps. A new chat session gets
an empty board.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.services.session_fs import ensure_session_work_dir

_ITEM = re.compile(r"^(\s*)[-*]\s+\[([ xX])\]\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_GOAL_PREFIX = re.compile(r"^(goal|task spec|clarified task|task)\s*:\s*", re.I)


@dataclass
class TodoItem:
    text: str
    done: bool = False


@dataclass
class TodoBoard:
    goal: str
    items: list[TodoItem]

    def open_items(self) -> list[TodoItem]:
        return [item for item in self.items if not item.done]

    def all_done(self) -> bool:
        return bool(self.items) and not self.open_items()


def todo_path():
    return ensure_session_work_dir() / "TODO.md"


def load_board() -> TodoBoard | None:
    path = todo_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_board(text)


def save_board(board: TodoBoard) -> None:
    path = todo_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_board(board), encoding="utf-8")


def parse_board(text: str) -> TodoBoard:
    goal = ""
    items: list[TodoItem] = []
    in_checklist = False
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.lower() in {"## checklist", "## todo", "## tasks"}:
            in_checklist = True
            continue
        if stripped.startswith("#") and not stripped.startswith("##"):
            title = stripped.lstrip("#").strip()
            if title.lower() != "task":
                goal = title
            continue
        if stripped.lower().startswith("goal:"):
            goal = stripped.split(":", 1)[1].strip()
            continue
        match = _ITEM.match(line)
        if match:
            items.append(TodoItem(text=match.group(3).strip(), done=match.group(2).lower() == "x"))
            in_checklist = True
            continue
        if in_checklist and stripped.startswith("##"):
            in_checklist = False
    if not goal:
        for raw in (text or "").splitlines():
            body = raw.strip()
            if body and not body.startswith("#") and not _ITEM.match(raw):
                goal = body
                break
    return TodoBoard(goal=goal or "Current task", items=items)


def render_board(board: TodoBoard) -> str:
    lines = [f"# {board.goal.strip() or 'Task'}", "", "## Checklist"]
    if not board.items:
        lines.append("- [ ] (no steps yet)")
    for item in board.items:
        mark = "x" if item.done else " "
        lines.append(f"- [{mark}] {item.text}")
    return "\n".join(lines) + "\n"


def board_from_spec(spec: str) -> TodoBoard:
    goal = ""
    items: list[TodoItem] = []
    for raw in (spec or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            text = bullet.group(1).strip()
            if text:
                items.append(TodoItem(text=text, done=False))
            continue
        if not goal:
            goal = _GOAL_PREFIX.sub("", line).strip()
    if not items and goal:
        items = [TodoItem(text=goal, done=False)]
    if not goal:
        goal = "Current task"
        if not items:
            items = [TodoItem(text=goal, done=False)]
    return TodoBoard(goal=goal, items=items)


def seed_from_spec(spec: str) -> TodoBoard:
    board = board_from_spec(spec)
    save_board(board)
    return board


def check_item(needle: str) -> str:
    board = load_board()
    if board is None:
        return "No checklist yet."
    needle = (needle or "").strip().lower()
    if not needle:
        return "Say which checklist item to mark done."
    matched = 0
    for item in board.items:
        if _check_matches(needle, item.text):
            item.done = True
            matched += 1
    if not matched:
        return f"No checklist item matched {needle!r}.\n" + snapshot(board)
    save_board(board)
    return snapshot(board)


def _check_matches(needle: str, item_text: str) -> bool:
    """Mark a step done only when the needle is actually that step."""
    a = (needle or "").strip().lower()
    b = (item_text or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if b.startswith(a) or a.startswith(b):
        return True
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b)) >= 0.4
    return False


def add_item(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return "Empty checklist item."
    board = load_board() or TodoBoard(goal="Current task", items=[])
    if any(item.text.lower() == body.lower() for item in board.items):
        return snapshot(board)
    board.items.append(TodoItem(text=body, done=False))
    save_board(board)
    return snapshot(board)


def snapshot(board: TodoBoard | None = None, *, limit: int = 1600) -> str:
    board = board if board is not None else load_board()
    if board is None:
        return ""
    open_n = len(board.open_items())
    done_n = len(board.items) - open_n
    lines = [
        f"Task: {board.goal}",
        f"Checklist: {done_n}/{len(board.items)} done.",
    ]
    for item in board.items:
        mark = "x" if item.done else " "
        lines.append(f"- [{mark}] {item.text}")
    if board.all_done():
        lines.append("All items are done. Do not invent extra scope.")
    else:
        lines.append("After you finish a step, call update_todo to check it off.")
        lines.append("Do not rewrite data/scratch/<session>/TODO.md with write_file.")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def prompt_appendix(*, mode: str, spec: str, always_surface: bool = False) -> str:
    """Seed from a new spec; otherwise keep the open board for continue.

    When ``always_surface`` is True (called from the system prompt every turn),
    show the current board even on follow-up turns so CatCrewAI can see what is still
    open and what is already done. The ``all_done and not body`` fast path still
    suppresses the block when the board is fully done and there is no new spec.
    """
    if (mode or "") != "developer":
        return ""
    body = (spec or "").strip()
    if body:
        board = seed_from_spec(body)
    else:
        board = load_board()
    if board is None or not board.items:
        return ""
    if board.all_done() and not body and not always_surface:
        return ""
    if always_surface:
        done = [item for item in board.items if item.done]
        if done:
            return (
                "\n\nPreviously completed this session (do NOT redo these unless asked):\n"
                + "\n".join(f"- {item.text}" for item in done)
                + "\n"
            )
    snap = snapshot(board)
    if not snap:
        return ""
    return (
        "\n\nHarness checklist (source of truth on disk, not chat history):\n"
        + snap
    )
