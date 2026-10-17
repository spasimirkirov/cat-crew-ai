from pathlib import Path

from backend.agent_context import list_skills
from backend.config import BASE_DIR
from backend.services.desktop import looks_like_program_name, open_url, system_time
from backend.tools import copy_path, execute_tool, glob_files, search_files, tools_schema_for


def test_open_workspace_html_uses_file_url(tmp_path, monkeypatch) -> None:
    from backend.tools import open_workspace_html

    page = tmp_path / "index.html"
    page.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [tmp_path.resolve()])
    opened: list[str] = []
    monkeypatch.setattr("os.startfile", lambda path: opened.append(str(path)), raising=False)
    uri = page.resolve().as_uri()
    msg = open_workspace_html(uri)
    assert "Opened" in msg
    assert opened and Path(opened[0]) == page.resolve()


def test_open_url_rejects_non_http() -> None:
    assert open_url("file:///etc/passwd").startswith("Error:")
    assert open_url("javascript:alert(1)").startswith("Error:")


def test_system_time_has_local_clock() -> None:
    text = system_time()
    assert text.startswith("Local:")
    assert "T" in text


def test_glob_files_finds_tools() -> None:
    out = glob_files("tools.py", str(BASE_DIR / "backend"))
    assert "tools.py" in out


def test_copy_path_inside_catcrew_tree() -> None:
    src = BASE_DIR / "backend" / "skills" / "desktop" / "SKILL.md"
    dest = BASE_DIR / "data" / "test-copy-skill.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        msg = copy_path(str(src), str(dest))
        assert dest.is_file()
        assert "Copied" in msg
    finally:
        if dest.exists():
            dest.unlink()


def _project(tmp_path, monkeypatch, name: str):
    ws = tmp_path / name
    ws.mkdir()
    monkeypatch.setattr("backend.tools.current_workspace", lambda: ws)
    monkeypatch.setattr("backend.tools.current_folders", lambda: [ws])
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [BASE_DIR.resolve(), ws.resolve()])
    return ws


def test_a_project_with_its_own_backend_folder_never_reaches_catcrew_source(tmp_path, monkeypatch) -> None:
    from backend.tools import resolve_user_path

    ws = _project(tmp_path, monkeypatch, "laragon-project")
    (ws / "backend").mkdir()
    # CatCrewAI has backend/config.py; the project does not yet. It is still the project's path.
    assert resolve_user_path("backend/config.py") == (ws / "backend" / "config.py").resolve()


def test_self_modify_still_finds_an_existing_catcrew_file(tmp_path, monkeypatch) -> None:
    from backend.tools import resolve_user_path

    _project(tmp_path, monkeypatch, "landing-page")  # no backend/ folder of its own
    resolved = resolve_user_path("backend/config.py")
    assert resolved == (BASE_DIR / "backend" / "config.py").resolve() and resolved.is_file()


def test_a_new_backend_file_is_created_in_the_project_not_in_catcrew(tmp_path, monkeypatch) -> None:
    """The eval that wrote backend/slug.py into CatCrewAI's source and restarted the app."""
    from backend.tools import resolve_user_path

    ws = _project(tmp_path, monkeypatch, "eval-ws")
    assert resolve_user_path("backend/slug.py") == (ws / "backend" / "slug.py").resolve()
    assert resolve_user_path("slug.py") == (ws / "slug.py").resolve()


def test_catcrew_as_the_open_project_creates_its_own_files(monkeypatch) -> None:
    from backend.tools import resolve_user_path

    monkeypatch.setattr("backend.tools.current_workspace", lambda: BASE_DIR)
    monkeypatch.setattr("backend.tools.current_folders", lambda: [BASE_DIR])
    assert resolve_user_path("backend/brand_new.py") == (BASE_DIR / "backend" / "brand_new.py").resolve()


def test_resolve_readme_stays_in_project_workspace(tmp_path, monkeypatch) -> None:
    from backend.tools import resolve_user_path

    fake_ws = tmp_path / "team-arcane"
    fake_ws.mkdir()
    monkeypatch.setattr("backend.tools.current_workspace", lambda: fake_ws)
    monkeypatch.setattr("backend.tools.current_folders", lambda: [fake_ws])
    monkeypatch.setattr(
        "backend.tools._allowed_roots",
        lambda: [BASE_DIR.resolve(), fake_ws.resolve()],
    )
    resolved = resolve_user_path("README.md")
    assert resolved == (fake_ws / "README.md").resolve()


def test_looks_like_program_name() -> None:
    assert looks_like_program_name("Notepad")
    assert looks_like_program_name("chrome.exe")
    assert not looks_like_program_name("notepad & calc")
    assert not looks_like_program_name("")


def test_developer_schema_includes_desktop_and_glob() -> None:
    names = {item["function"]["name"] for item in tools_schema_for("developer")}
    for name in ("glob_files", "copy_path", "open_url", "clipboard", "screenshot"):
        assert name in names
    assert execute_tool("open_url", {"url": "ftp://x"}).startswith("Error:")


def test_search_files_default_glob_hits_php(tmp_path, monkeypatch) -> None:
    (tmp_path / "app.php").write_text("function book_studio() {}\n")
    monkeypatch.setattr("backend.tools.current_folders", lambda: [tmp_path])
    out = search_files("book_studio", str(tmp_path))
    assert "app.php" in out


def test_bundled_skills_are_indexed() -> None:
    names = {skill.name for skill in list_skills()}
    assert {"desktop", "web-research", "home-lan", "coding-companion", "static-web", "docker", "blender-mcp", "unity-mcp"} <= names


def test_search_files_takes_several_patterns_in_one_glob(tmp_path, monkeypatch) -> None:
    """gpt-oss passes "**/*.js,**/*.ts,**/*.py"; that used to match nothing and say "No matches."."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export const get_usr = 1\n", encoding="utf-8")
    (tmp_path / "users.py").write_text("def get_usr(): pass\n", encoding="utf-8")
    monkeypatch.setattr("backend.tools.resolve_user_path", lambda _p: tmp_path)
    out = search_files("get_usr", ".", "**/*.js,**/*.ts,**/*.py")
    assert "app.ts:1" in out and "users.py:1" in out
    missing = search_files("nothing_here", ".", "*.py")
    assert missing.startswith("No matches for 'nothing_here'") and "*.py" in missing
