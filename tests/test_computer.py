"""Generic platform guards — stack-agnostic. Landing-specific truth lives in test_arcane_site.py."""

from pathlib import Path

from backend.providers.base import ToolCall, _call_key
from backend.tools import repair_agent_path, run_command, write_file
from backend.services.workspace import set_folders


def test_delegate_call_key_is_bot_id() -> None:
    a = ToolCall(name="delegate_to_bot", arguments={"bot_id": "ekko", "task": "one"})
    b = ToolCall(name="delegate_to_bot", arguments={"bot_id": "ekko", "task": "two"})
    c = ToolCall(name="delegate_to_bot", arguments={"bot_id": "catcrew", "task": "one"})
    assert _call_key(a) == _call_key(b)
    assert _call_key(a) != _call_key(c)


def test_repair_mashed_windows_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    mashed = str(tmp_path.resolve()).replace("\\", "").replace("/", "").replace(":", "")
    assert Path(repair_agent_path(mashed)) == tmp_path.resolve()
    repaired = repair_agent_path(r"D:\laragon\www" + "\t" + "eam-arcane")
    assert "\t" not in repaired
    assert repaired.replace("\\", "/").endswith("www/team-arcane")


def test_repair_rejects_flattened_workspace_plus_suffix(tmp_path: Path, monkeypatch) -> None:
    """Regression: after a few correct writes, Gemma started echoing an
    already-flattened workspace path back with a filename tacked on —
    e.g. ``D:laragonwwwteam-arcanesrccomponentsHero.js`` when the
    workspace is ``D:\\laragon\\www\\team-arcane`` — and the app silently
    wrote a garbage flat file into the project root instead of erroring.
    There's no reliable way to guess where "srccomponentsHero.js" should
    be split back into src/components/Hero.js, so this must raise instead
    of guessing."""
    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    mashed_ws = str(tmp_path.resolve()).replace("\\", "").replace("/", "").replace(":", "")
    flattened_with_suffix = mashed_ws + "srccomponentsHero.js"
    try:
        repair_agent_path(flattened_with_suffix)
        assert False, "expected a ValueError for a flattened workspace + suffix"
    except ValueError as exc:
        assert "separators" in str(exc) or "relative path" in str(exc)


def test_write_file_reports_flattened_path_as_error_not_garbage_write(
    tmp_path: Path, monkeypatch
) -> None:
    """Through execute_tool (the real call path from an agent turn — see
    tools.py's top-level try/except ValueError), the flattened-path guard
    becomes a clean "Error: ..." string the bot can retry from, not a
    silently-written garbage file and not an unhandled exception."""
    from backend.tools import execute_tool

    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("backend.tools.current_folders", lambda: [tmp_path])
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [tmp_path.resolve()])
    set_folders([tmp_path])
    mashed_ws = str(tmp_path.resolve()).replace("\\", "").replace("/", "").replace(":", "")
    result = execute_tool(
        "write_file",
        {"path": mashed_ws + "srccomponentsHero.js", "content": "export default function Hero() {}"},
    )
    assert result.startswith("Error:")
    # nothing was written anywhere under the workspace as a flat garbage file
    assert not any(tmp_path.rglob("*Hero*"))


def test_write_file_refuses_stub_and_catcrew_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("backend.tools.current_folders", lambda: [tmp_path])
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [tmp_path.resolve()])
    set_folders([tmp_path])
    stub = write_file("src/Hero.jsx", "export default function Hero() { return <div></div>; }")
    assert stub.startswith("Refused:")
    bad = write_file(
        "src/App.jsx",
        "async function load() { return await list_roster(); }\nexport default function App(){return <h1>Hi</h1>}",
    )
    assert bad.startswith("Refused:")
    ok = write_file("src/Hero.jsx", "export default function Hero() { return <section><h1>Arcane</h1></section>; }")
    assert ok.startswith("Wrote")


def test_write_file_allows_any_stack(tmp_path: Path, monkeypatch) -> None:
    """The platform must not hard-code one stack: vanilla Vite, PHP redirects, Blade-ish files."""
    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("backend.tools.current_folders", lambda: [tmp_path])
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [tmp_path.resolve()])
    set_folders([tmp_path])
    cfg = write_file("vite.config.js", "import { defineConfig } from 'vite';\nexport default defineConfig({ base: './' });\n")
    assert cfg.startswith("Wrote")
    php = write_file("public/index.php", "<?php header('Location: /login'); exit;\n")
    assert php.startswith("Wrote")
    compose = write_file("docker-compose.yml", "services:\n  app:\n    image: php:8.3-fpm\n")
    assert compose.startswith("Wrote")


def test_execute_tool_coerces_nested_dict_args(tmp_path: Path, monkeypatch) -> None:
    """Gemma nests args like {"path": {"path": ...}} — unwrap, never crash with .strip()."""
    from backend.tools import execute_tool

    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("backend.tools.current_folders", lambda: [tmp_path])
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [tmp_path.resolve()])
    set_folders([tmp_path])
    out = execute_tool(
        "write_file",
        {
            "path": {"path": "src/Hero.jsx"},
            "content": {
                "content": "export default function Hero() { return <section><h1>Arcane</h1></section>; }"
            },
        },
    )
    assert out.startswith("Wrote")
    assert (tmp_path / "src" / "Hero.jsx").is_file()
    read = execute_tool("read_file", {"path": ["src/Hero.jsx"]})
    assert "Arcane" in read


def test_run_command_refuses_npm_install_without_package_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    out = run_command("npm install bootstrap")
    assert out.startswith("Refused: no package.json")


def test_computer_listing_is_ground_truth(tmp_path: Path) -> None:
    from backend.platform.runner import computer_listing

    assert "EMPTY" in computer_listing(tmp_path)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    listing = computer_listing(tmp_path)
    assert "package.json" in listing
    assert "src/" in listing


def test_bridge_build_skips_projects_without_build_script(tmp_path: Path) -> None:
    from backend.platform.runner import _bridge_build

    assert _bridge_build(tmp_path) == ""
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "vite"}}', encoding="utf-8")
    assert _bridge_build(tmp_path) == ""
