from pathlib import Path

from backend.config import BASE_DIR
from backend.services.sessions import new_session, session_folders
from backend.services.workspace import add_folder, current_folders, current_workspace, folder_picker_start, remove_folder, set_folders, terminal_folders


def test_session_folders_empty_list_stays_empty() -> None:
    assert session_folders({"folders": []}) == []


def test_session_folders_legacy_uses_workspace(tmp_path: Path) -> None:
    assert session_folders({"workspace": str(tmp_path)}) == [str(tmp_path.resolve())]


def test_session_folders_ignores_legacy_app_source_workspace() -> None:
    assert session_folders({"workspace": str(BASE_DIR)}) == []


def test_set_folders_can_be_empty(tmp_path: Path) -> None:
    set_folders([tmp_path])
    assert current_folders() == [tmp_path.resolve()]
    set_folders([])
    assert current_folders() == []
    add_folder(tmp_path)
    assert current_folders() == [tmp_path.resolve()]
    remove_folder(tmp_path)
    assert current_folders() == []


def test_terminal_folders_uses_workspace_roots(tmp_path: Path) -> None:
    previous = current_folders()
    other = tmp_path / "other"
    other.mkdir()
    try:
        set_folders([tmp_path])
        assert terminal_folders() == [tmp_path.resolve()]
        set_folders([tmp_path, other])
        assert terminal_folders() == [tmp_path.resolve(), other.resolve()]
        set_folders([])
        assert terminal_folders() == [current_workspace()]
        assert current_workspace() != BASE_DIR
    finally:
        set_folders(previous)


def test_folder_picker_start_is_not_app_source() -> None:
    previous = current_folders()
    set_folders([])
    start = folder_picker_start()
    assert start.resolve() != BASE_DIR.resolve()
    set_folders([BASE_DIR])
    assert folder_picker_start().resolve() == BASE_DIR.resolve()
    set_folders(previous)


def test_new_session_starts_with_empty_explorer(tmp_path: Path, monkeypatch) -> None:
    from backend.services import sessions

    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions, "INDEX_PATH", tmp_path / "sessions" / "index.json")
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    set_folders([tmp_path])
    session = new_session(mode="developer")
    assert session["folders"] == []
    assert session["workspace"] == ""
    assert current_folders() == []
    assert not (tmp_path / ".cat-crew-ai").exists()
    assert (tmp_path / "scratch" / session["id"]).is_dir()


def test_prepare_computer_folder_creates_and_rejects_install(tmp_path: Path) -> None:
    from backend.services.workspace import is_app_install, prepare_computer_folder

    missing = tmp_path / "site"
    created = prepare_computer_folder(missing)
    assert created == missing.resolve()
    assert created.is_dir()
    assert is_app_install(BASE_DIR)
    try:
        prepare_computer_folder(BASE_DIR)
        raise AssertionError("install tree must not be a project computer")
    except ValueError:
        pass


def test_open_project_thread_binds_shared_computer(tmp_path: Path, monkeypatch) -> None:
    from backend.services import sessions
    from backend.platform.registry import PlatformBuilder, reset_registry_cache
    from backend.services.sessions import open_project_thread, session_folders

    store = tmp_path / "registry.json"
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    computer = tmp_path / "shop"
    computer.mkdir()
    PlatformBuilder().project("p1", "Shop", folders=[str(computer)]).save()
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions, "INDEX_PATH", tmp_path / "sessions" / "index.json")
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    previous = current_folders()
    try:
        set_folders([])
        first = open_project_thread("p1", "chief", title="Shop")
        assert Path(session_folders(first)[0]).resolve() == computer.resolve()
        first["folders"] = []
        first["workspace"] = ""
        sessions.save_session(first, activate=True)
        again = open_project_thread("p1", "chief")
        assert again["id"] == first["id"]
        assert Path(session_folders(again)[0]).resolve() == computer.resolve()
        one = open_project_thread("p1", "catcrew")
        assert one["id"] != first["id"]
        assert Path(session_folders(one)[0]).resolve() == computer.resolve()
        assert current_folders()[0] == computer.resolve()
    finally:
        set_folders(previous)
        reset_registry_cache()
