"""Tests for self-modify restart resume flag + anti-loop gates."""

from __future__ import annotations

from pathlib import Path

import jinx_ai.pending_restart as pr


def setup_function() -> None:
    pr.reset_runtime_state()


def teardown_function() -> None:
    pr.reset_runtime_state()
    try:
        if pr.PENDING_PATH.is_file():
            # Never leave a real resume flag from unit tests.
            if "pytest" in str(pr.PENDING_PATH) or "tmp" in str(pr.PENDING_PATH).lower():
                pr.PENDING_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def test_write_take_pending_is_one_shot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "PENDING_PATH", tmp_path / "pending_self_modify.json")
    monkeypatch.setattr(pr, "_active_session", lambda: "sess-1")
    monkeypatch.setattr(pr, "_todo_goal", lambda: "Rename chrome")
    pr.clear_touched_jinx_files()
    pr.write_pending(
        reason="title change",
        goal="",
        files=["jinx_ai/titlebar.py"],
        generation=1,
    )
    assert pr.PENDING_PATH.is_file()
    first = pr.take_pending()
    assert first is not None
    assert first["kind"] == "self_modify_verify"
    assert first["session_id"] == "sess-1"
    assert first["goal"] == "Rename chrome"
    assert first["files"] == ["jinx_ai/titlebar.py"]
    assert first["generation"] == 1
    assert "title change" in first["reason"]
    assert pr.take_pending() is None
    assert not pr.PENDING_PATH.is_file()


def test_format_resume_message_done_not_restart_loop() -> None:
    text = pr.format_resume_message(
        {
            "goal": "New title",
            "reason": "patched titlebar",
            "files": ["jinx_ai/titlebar.py", "jinx_ai/config.py"],
            "generation": 1,
            "max_generations": 3,
        }
    )
    assert "INTERNAL" in text
    assert "invisible" in text.lower() or "never see" in text.lower()
    assert "New title" in text
    assert "jinx_ai/titlebar.py" in text
    assert "DONE" in text or "stop" in text.lower()
    assert "Do NOT call restart_jinx" in text or "do NOT call restart_jinx" in text


def test_restart_refused_without_edits(monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.self_modifier.restart_app", lambda: None)
    pr.reset_runtime_state()
    msg = pr.schedule_self_modify_restart(reason="noop")
    assert msg.startswith("Refused restart_jinx")
    assert "no Jinx source edits" in msg


def test_restart_refused_on_verify_without_new_edits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "PENDING_PATH", tmp_path / "pending_self_modify.json")
    monkeypatch.setattr("jinx_ai.self_modifier.restart_app", lambda: None)
    pr.reset_runtime_state()
    pr.begin_verify_pass(
        {"generation": 1, "goal": "title", "files": ["jinx_ai/titlebar.py"]}
    )
    msg = pr.schedule_self_modify_restart(reason="recheck")
    assert msg.startswith("Refused restart_jinx")
    assert "verify pass" in msg.lower() or "no new" in msg.lower()


def test_restart_allowed_after_edit_then_auto(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "PENDING_PATH", tmp_path / "pending_self_modify.json")
    monkeypatch.setattr(pr, "BASE_DIR", tmp_path)
    monkeypatch.setattr("jinx_ai.self_modifier.restart_app", lambda: None)
    pr.reset_runtime_state()
    (tmp_path / "jinx_ai").mkdir()
    target = tmp_path / "jinx_ai" / "titlebar.py"
    target.write_text("x", encoding="utf-8")
    pr.note_jinx_source_edit(target)
    msg = pr.schedule_self_modify_restart(reason="title", delay=99)
    assert msg.startswith("Restarting Jinx")
    assert pr.PENDING_PATH.is_file()
    pending = pr.peek_pending()
    assert pending is not None
    assert pending["generation"] == 1
    # Second schedule while already scheduled
    again = pr.schedule_self_modify_restart(reason="again", delay=99)
    assert again.startswith("Refused")


def test_verify_then_edit_increments_generation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "PENDING_PATH", tmp_path / "pending_self_modify.json")
    monkeypatch.setattr(pr, "BASE_DIR", tmp_path)
    monkeypatch.setattr("jinx_ai.self_modifier.restart_app", lambda: None)
    pr.reset_runtime_state()
    (tmp_path / "jinx_ai").mkdir()
    target = tmp_path / "jinx_ai" / "titlebar.py"
    target.write_text("x", encoding="utf-8")
    pr.begin_verify_pass({"generation": 1, "goal": "title"})
    pr.note_jinx_source_edit(target)
    msg = pr.schedule_self_modify_restart(reason="fix", delay=99)
    assert "verify pass 2/" in msg
    pending = pr.peek_pending()
    assert pending is not None
    assert pending["generation"] == 2


def test_generation_cap_blocks_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "PENDING_PATH", tmp_path / "pending_self_modify.json")
    monkeypatch.setattr(pr, "BASE_DIR", tmp_path)
    monkeypatch.setattr("jinx_ai.self_modifier.restart_app", lambda: None)
    pr.reset_runtime_state()
    (tmp_path / "jinx_ai").mkdir()
    target = tmp_path / "jinx_ai" / "titlebar.py"
    target.write_text("x", encoding="utf-8")
    pr.begin_verify_pass({"generation": pr.MAX_GENERATIONS, "goal": "title"})
    pr.note_jinx_source_edit(target)
    msg = pr.schedule_self_modify_restart(reason="again", delay=99)
    assert msg.startswith("Refused")
    assert "cap" in msg.lower()


def test_maybe_auto_restart_after_edits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pr, "PENDING_PATH", tmp_path / "pending_self_modify.json")
    monkeypatch.setattr(pr, "BASE_DIR", tmp_path)
    monkeypatch.setattr("jinx_ai.self_modifier.restart_app", lambda: None)
    pr.reset_runtime_state()
    assert pr.maybe_auto_restart_after_edits() == ""
    (tmp_path / "jinx_ai").mkdir()
    target = tmp_path / "jinx_ai" / "titlebar.py"
    target.write_text("x", encoding="utf-8")
    pr.note_jinx_source_edit(target)
    msg = pr.maybe_auto_restart_after_edits(delay=99)
    assert msg.startswith("Restarting Jinx")
    assert "auto" in (pr.peek_pending() or {}).get("reason", "")


def test_note_jinx_source_edit_tracks_relative(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pr, "BASE_DIR", tmp_path)
    (tmp_path / "jinx_ai").mkdir()
    target = tmp_path / "jinx_ai" / "titlebar.py"
    target.write_text("x", encoding="utf-8")
    pr.clear_touched_jinx_files()
    pr.note_jinx_source_edit(target)
    assert pr.touched_jinx_files() == ["jinx_ai/titlebar.py"]
    pr.note_jinx_source_edit(target)
    assert pr.touched_jinx_files() == ["jinx_ai/titlebar.py"]
