from pathlib import Path

import pytest

from backend.services import todo
from backend.tools import execute_tool, reset_tool_allowlist, set_tool_allowlist, tools_schema_for
from backend.services.trace import bind_session
from backend.services.workspace import current_folders, set_folders, set_workspace


@pytest.fixture
def scratch_dir(tmp_path: Path, monkeypatch):
    previous = current_folders()
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    set_folders([])
    bind_session("sess-a")
    yield tmp_path / "scratch"
    bind_session("")
    set_folders(previous)


def test_board_from_spec_uses_goal_and_bullets() -> None:
    board = todo.board_from_spec(
        "Goal: Tiny landing page.\n- Write index.html\n- Add styles.css"
    )
    assert board.goal.startswith("Tiny landing")
    assert [item.text for item in board.items] == ["Write index.html", "Add styles.css"]
    assert not board.all_done()


def test_seed_snapshot_and_check(scratch_dir: Path) -> None:
    spec = "Tiny cafe site.\n- index.html hero\n- styles.css"
    appendix = todo.prompt_appendix(mode="developer", spec=spec)
    assert "Harness checklist" in appendix
    assert "index.html" in appendix
    assert (scratch_dir / "sess-a" / "TODO.md").is_file()
    out = todo.check_item("index.html")
    assert "[x]" in out
    assert "styles.css" in out
    board = todo.load_board()
    assert board is not None
    assert board.open_items()[0].text.startswith("styles")


def test_continue_keeps_open_board(scratch_dir: Path) -> None:
    todo.seed_from_spec("Ship the site.\n- hero\n- footer")
    again = todo.prompt_appendix(mode="developer", spec="")
    assert "hero" in again
    assert "footer" in again
    todo.check_item("hero")
    todo.check_item("footer")
    assert todo.prompt_appendix(mode="developer", spec="") == ""


def test_check_item_does_not_mark_the_whole_goal_from_a_feature_phrase(
    scratch_dir: Path,
) -> None:
    todo.seed_from_spec(
        "Create a React site for CatCrewAI with a holographic widget, MCP, and autonomous coding.\n"
        "- Write index.html\n"
        "- Write styles.css"
    )
    out = todo.check_item("A holographic desktop widget")
    assert "No checklist item matched" in out
    board = todo.load_board()
    assert board is not None
    assert all(not item.done for item in board.items)
    todo.check_item("index.html")
    board = todo.load_board()
    assert board is not None
    assert board.items[0].done is True


def test_assistant_gets_no_checklist(scratch_dir: Path) -> None:
    text = todo.prompt_appendix(mode="assistant", spec="Build a huge app\n- a\n- b")
    assert text == ""
    assert not (scratch_dir / "sess-a" / "TODO.md").exists()


def test_execute_tool_honors_allowlist(scratch_dir: Path) -> None:
    token = set_tool_allowlist(frozenset({"update_todo"}))
    try:
        denied = execute_tool("write_file", {"path": "x.html", "content": "no"})
        assert "not available" in denied
        todo.seed_from_spec("Site.\n- index.html")
        ok = execute_tool("update_todo", {"action": "check", "item": "index"})
        assert "[x]" in ok
    finally:
        reset_tool_allowlist(token)
    # Unrestricted again for other tests in this process.
    assert "not available" not in execute_tool("list_allowed_directories", {})


def test_update_todo_not_in_assistant_schema() -> None:
    names = {item["function"]["name"] for item in tools_schema_for("assistant")}
    assert "update_todo" not in names
    dev = {item["function"]["name"] for item in tools_schema_for("developer")}
    assert "update_todo" in dev


def test_new_session_does_not_see_other_session_todo(scratch_dir: Path) -> None:
    todo.seed_from_spec("Old site.\n- leftover.html")
    bind_session("sess-b")
    assert todo.load_board() is None
    assert todo.prompt_appendix(mode="developer", spec="") == ""
    assert (scratch_dir / "sess-a" / "TODO.md").is_file()
    assert not (scratch_dir / "sess-b" / "TODO.md").exists()
    todo.seed_from_spec("New site.\n- index.html")
    assert (scratch_dir / "sess-b" / "TODO.md").is_file()
    bind_session("sess-a")
    board = todo.load_board()
    assert board is not None
    assert "leftover.html" in board.items[0].text


def test_seed_does_not_touch_project_folder(tmp_path: Path, monkeypatch) -> None:
    previous = current_folders()
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    set_workspace(tmp_path)
    bind_session("sess-a")
    todo.seed_from_spec("Site.\n- index.html")
    assert not (tmp_path / ".cat-crew-ai").exists()
    assert (tmp_path / "scratch" / "sess-a" / "TODO.md").is_file()
    bind_session("")
    set_folders(previous)
