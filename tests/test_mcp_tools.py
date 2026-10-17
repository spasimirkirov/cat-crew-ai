from pathlib import Path

from jinx_ai import git_ops
from jinx_ai.db_backend import SqliteBackend
from jinx_ai.tools import (
    _html_to_text,
    create_directory,
    execute_tool,
    get_file_info,
    list_allowed_directories,
    tools_schema_for,
)


def test_html_to_markdown_keeps_headings_and_links() -> None:
    html = "<h1>Hi</h1><p>See <a href='https://example.com/x'>docs</a>.</p><ul><li>one</li></ul>"
    text = _html_to_text(html)
    assert "# Hi" in text
    assert "[docs](https://example.com/x)" in text
    assert "- one" in text


def test_web_fetch_paginates(monkeypatch) -> None:
        class _Resp:
            headers = {"Content-Type": "text/plain"}
            text = "x" * 500

            def raise_for_status(self) -> None:
                return None

        import jinx_ai.tools as tools

        monkeypatch.setattr(tools.requests, "get", lambda *a, **k: _Resp())
        first = tools.web_fetch("https://example.com/page", max_length=200, start_index=0)
        assert "chars 0-200 of 500" in first
        assert "start_index=200" in first
        second = tools.web_fetch("https://example.com/page", max_length=200, start_index=200)
        assert "chars 200-400 of 500" in second


def test_file_info_and_mkdir_and_allowed_roots(tmp_path: Path) -> None:
    from jinx_ai.config import BASE_DIR

    info = get_file_info(str(BASE_DIR / "main.py"))
    assert "kind: file" in info
    assert "main.py" in info
    folder = BASE_DIR / "data" / "_jinx_test_mkdir"
    try:
        assert "Created directory" in create_directory(str(folder))
        assert folder.is_dir()
    finally:
        if folder.exists():
            folder.rmdir()
    roots = list_allowed_directories()
    assert str(BASE_DIR.resolve()) in roots


def test_developer_schema_has_stolen_mcp_methods() -> None:
    names = {item["function"]["name"] for item in tools_schema_for("developer")}
    for name in (
        "image_search",
        "git_add",
        "git_unstage",
        "git_branch",
        "git_checkout",
        "git_show",
        "git_pull",
        "git_push",
        "get_file_info",
        "create_directory",
        "update_todo",
        "db_describe_table",
        "hardware_info",
        "recommend_models",
        "comfy_run_workflow",
        "comfy_info",
    ):
        assert name in names


def test_git_branch_and_show(tmp_path: Path) -> None:
    git_ops.run_git(["init"], tmp_path)
    git_ops.run_git(["config", "user.email", "t@example.com"], tmp_path)
    git_ops.run_git(["config", "user.name", "Test"], tmp_path)
    (tmp_path / "a.txt").write_text("hi\n", encoding="utf-8")
    staged = git_ops.stage_paths(["a.txt"], tmp_path)
    assert "Staged" in staged
    committed = git_ops.commit("first", tmp_path)
    assert "first" in committed.lower() or "Committed" in committed
    listed = git_ops.branch_text("", tmp_path)
    assert listed
    created = git_ops.branch_text("feature-x", tmp_path)
    assert "feature-x" in created
    shown = git_ops.show_text("HEAD", tmp_path)
    assert "first" in shown
    (tmp_path / "a.txt").write_text("bye\n", encoding="utf-8")
    git_ops.stage_paths(["a.txt"], tmp_path)
    unstaged = git_ops.unstage(["a.txt"], tmp_path)
    assert "Unstaged" in unstaged


def test_db_describe_sqlite(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "pets.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE pets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.commit()
    conn.close()
    backend = SqliteBackend(db)
    try:
        cols = {col.name: col for col in backend.columns("pets")}
    finally:
        backend.close()
    assert cols["id"].pk
    assert cols["name"].type_name.upper().startswith("TEXT")


def test_execute_tool_git_reset_unstages(monkeypatch) -> None:
    called: list[list[str]] = []

    def _fake(paths: list[str]) -> str:
        called.append(list(paths))
        return "Unstaged 1 path(s)."

    monkeypatch.setattr("jinx_ai.tools.git_unstage", _fake)
    out = execute_tool("git_reset", {"paths": ["a.txt"]})
    assert "Unstaged" in out
    assert called == [["a.txt"]]
