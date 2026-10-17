from pathlib import Path

from jinx_ai.agent_context import list_skills
from jinx_ai.config import BASE_DIR
from jinx_ai.desktop import looks_like_program_name, open_url, system_time
from jinx_ai.tools import copy_path, execute_tool, glob_files, search_files, tools_schema_for


def test_open_workspace_html_uses_file_url(tmp_path, monkeypatch) -> None:
    from jinx_ai.tools import open_workspace_html

    page = tmp_path / "index.html"
    page.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr("jinx_ai.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("jinx_ai.tools._allowed_roots", lambda: [tmp_path.resolve()])
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
    out = glob_files("tools.py", str(BASE_DIR / "jinx_ai"))
    assert "tools.py" in out


def test_copy_path_inside_jinx_tree() -> None:
    src = BASE_DIR / "jinx_ai" / "skills" / "desktop" / "SKILL.md"
    dest = BASE_DIR / "data" / "test-copy-skill.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        msg = copy_path(str(src), str(dest))
        assert dest.is_file()
        assert "Copied" in msg
    finally:
        if dest.exists():
            dest.unlink()


def test_resolve_jinx_ai_path_prefers_source_over_workspace(tmp_path, monkeypatch) -> None:
    from jinx_ai.tools import resolve_user_path

    fake_ws = tmp_path / "laragon-project"
    fake_ws.mkdir()
    (fake_ws / "jinx_ai").mkdir()
    monkeypatch.setattr("jinx_ai.tools.current_workspace", lambda: fake_ws)
    monkeypatch.setattr("jinx_ai.tools.current_folders", lambda: [fake_ws])
    resolved = resolve_user_path("jinx_ai/titlebar.py")
    assert resolved == (BASE_DIR / "jinx_ai" / "titlebar.py").resolve()
    assert resolved.is_file()


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
    monkeypatch.setattr("jinx_ai.tools.current_folders", lambda: [tmp_path])
    out = search_files("book_studio", str(tmp_path))
    assert "app.php" in out


def test_bundled_skills_are_indexed() -> None:
    names = {skill.name for skill in list_skills()}
    assert {"desktop", "web-research", "home-lan", "coding-companion", "static-web", "docker"} <= names
