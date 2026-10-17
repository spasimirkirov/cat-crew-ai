from pathlib import Path

from backend.services.session_fs import ensure_session_work_dir, ensure_workspace_gitignore, gitignore_covers_cat_crew_ai, ignore_cat_crew_ai_in_gitignore, remove_session_work_dir, session_work_dir
from backend.services.workspace import current_folders, folder_picker_start, set_folders, set_workspace


def test_gitignore_covers_common_patterns() -> None:
    assert gitignore_covers_cat_crew_ai(".cat-crew-ai/\n")
    assert gitignore_covers_cat_crew_ai(".cat-crew-ai\n")
    assert gitignore_covers_cat_crew_ai("**/.cat-crew-ai\n")
    assert not gitignore_covers_cat_crew_ai(".catcrew/\n")
    assert not gitignore_covers_cat_crew_ai("# .cat-crew-ai/\n")
    assert not gitignore_covers_cat_crew_ai("!.cat-crew-ai/\n")


def test_ensure_session_work_dir_uses_app_scratch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    previous = current_folders()
    set_folders([])
    path = ensure_session_work_dir("sess-a")
    assert path == tmp_path / "scratch" / "sess-a"
    assert path.is_dir()
    assert not list(tmp_path.glob("**/.cat-crew-ai"))
    set_folders(previous)


def test_does_not_create_project_cat_crew_ai_or_gitignore(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    previous = current_folders()
    set_workspace(tmp_path)
    ensure_session_work_dir("sess-a")
    assert not (tmp_path / ".cat-crew-ai").exists()
    assert not (tmp_path / ".gitignore").exists()
    assert (tmp_path / "scratch" / "sess-a").is_dir()
    set_folders(previous)


def test_attach_appends_cat_crew_ai_to_existing_gitignore(tmp_path: Path) -> None:
    previous = current_folders()
    set_workspace(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n", encoding="utf-8")
    ensure_workspace_gitignore([tmp_path])
    text = gitignore.read_text(encoding="utf-8")
    assert ".cat-crew-ai/" in text
    ignore_cat_crew_ai_in_gitignore(tmp_path)
    assert text.count(".cat-crew-ai/") == 1
    assert not (tmp_path / ".cat-crew-ai").exists()
    set_folders(previous)


def test_remove_session_work_dir_clears_scratch_and_legacy(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    previous = current_folders()
    set_workspace(tmp_path)
    ensure_session_work_dir("sess-a")
    ensure_session_work_dir("sess-b")
    legacy = tmp_path / ".cat-crew-ai" / "sess-a"
    legacy.mkdir(parents=True)
    (legacy / "TODO.md").write_text("x", encoding="utf-8")
    remove_session_work_dir("sess-a", workspace=tmp_path)
    assert not (tmp_path / "scratch" / "sess-a").exists()
    assert not legacy.exists()
    assert (tmp_path / "scratch" / "sess-b").is_dir()
    set_folders(previous)


def test_remove_unknown_session_dir_is_noop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    ensure_session_work_dir("unknown")
    remove_session_work_dir("unknown")
    remove_session_work_dir("")
    assert (tmp_path / "scratch" / "unknown").is_dir()


def test_delete_session_removes_scratch(tmp_path: Path, monkeypatch) -> None:
    from backend.services import sessions
    from backend.services.sessions import delete_session, load_session, new_session

    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions, "INDEX_PATH", tmp_path / "sessions" / "index.json")
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    previous = current_folders()
    set_folders([])
    session = new_session(activate=True)
    sid = str(session["id"])
    work = session_work_dir(sid)
    assert work.is_dir()
    assert session["folders"] == []
    assert session["workspace"] == ""
    delete_session(sid)
    assert load_session(sid) is None
    assert not work.exists()
    set_folders(previous)


def test_folder_picker_starts_at_drive_when_empty() -> None:
    previous = current_folders()
    set_folders([])
    start = folder_picker_start()
    assert start.name == "" or str(start).endswith(":\\") or start == Path.home()
    assert "cat-crew-ai" not in str(start).lower()
    set_folders(previous)
