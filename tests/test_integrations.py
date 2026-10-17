"""The Blender installer (stdlib script) — run against a fake Blender config folder, no Blender needed."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location("install_addon", Path(__file__).resolve().parents[1] / "integrations" / "blender" / "install_addon.py")
inst = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inst)


def _make_zip(path: Path, nested: bool = True) -> Path:
    prefix = "blender_mcp_addon/" if nested else ""
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(prefix + "__init__.py", 'bl_info = {"name": "MCP"}\n')
        bundle.writestr(prefix + "server.py", "x = 1\n")
    return path


@pytest.mark.parametrize("nested", [True, False])
def test_install_from_zip_writes_addon_and_autostart(tmp_path: Path, nested: bool) -> None:
    archive = _make_zip(tmp_path / "a.zip", nested)
    config = tmp_path / "cfg" / "4.2"
    assert inst.main(["--source", str(archive), "--config-dir", str(config), "--port", "9999"]) == 0
    assert (config / "scripts" / "addons" / "blender_mcp_addon" / "__init__.py").is_file()
    script = config / "scripts" / "startup" / inst.STARTUP_NAME
    text = script.read_text(encoding="utf-8")
    compile(text, str(script), "exec")  # valid Python, formatting braces resolved
    assert "PORT = 9999" in text and 'HOST = "127.0.0.1"' in text and "start_server" in text


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    config = tmp_path / "cfg" / "4.2"
    assert inst.main(["--dry-run", "--config-dir", str(config)]) == 0
    assert not config.exists()


def test_uninstall_removes_both(tmp_path: Path) -> None:
    config = tmp_path / "cfg" / "4.2"
    inst.main(["--source", str(_make_zip(tmp_path / "a.zip")), "--config-dir", str(config)])
    assert inst.main(["--uninstall", "--config-dir", str(config)]) == 0
    assert not (config / "scripts" / "addons" / "blender_mcp_addon").exists()
    assert not (config / "scripts" / "startup" / inst.STARTUP_NAME).exists()


def test_reinstall_replaces_old_addon_files(tmp_path: Path) -> None:
    config = tmp_path / "cfg" / "4.2"
    old = config / "scripts" / "addons" / "blender_mcp_addon"
    old.mkdir(parents=True)
    (old / "stale.py").write_text("x", encoding="utf-8")
    inst.main(["--source", str(_make_zip(tmp_path / "a.zip")), "--config-dir", str(config)])
    assert not (old / "stale.py").exists() and (old / "server.py").is_file()


def test_unsafe_zip_and_missing_addon_are_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as bundle:
        bundle.writestr("../evil.py", "x")
    with pytest.raises(SystemExit):
        inst.fetch_source(str(bad), tmp_path / "w1")
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as bundle:
        bundle.writestr("readme.txt", "no addon here")
    with pytest.raises(SystemExit):
        inst.fetch_source(str(empty), tmp_path / "w2")


def test_config_root_per_platform_and_version_pick(tmp_path: Path) -> None:
    assert inst.config_root("win32", {"APPDATA": "C:/Users/x/AppData/Roaming"}, tmp_path).parts[-3:] == ("Roaming", "Blender Foundation", "Blender")
    assert inst.config_root("darwin", {}, tmp_path) == tmp_path / "Library" / "Application Support" / "Blender"
    assert inst.config_root("linux", {}, tmp_path) == tmp_path / ".config" / "blender"
    root = tmp_path / "Blender"
    for name in ("3.6", "4.10", "4.2", "cache"):
        (root / name).mkdir(parents=True)
    assert [p.name for p in inst.version_dirs(root)] == ["3.6", "4.2", "4.10"]
    assert inst.pick_targets(root, "4.2", None, create=True) == [root / "4.2"]
    assert inst.pick_targets(root, "9.9", None, create=False) == []
    assert inst.pick_targets(root, None, tmp_path / "x", create=False) == [tmp_path / "x"]


def test_no_blender_folder_is_a_clear_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(inst, "config_root", lambda *a, **k: tmp_path / "nothing")
    assert inst.main(["--source", str(_make_zip(tmp_path / "a.zip"))]) == 2
    assert "Start Blender once" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------ Unity helper

_USPEC = importlib.util.spec_from_file_location("install_unity_helper", Path(__file__).resolve().parents[1] / "integrations" / "unity" / "install_unity_helper.py")
unity = importlib.util.module_from_spec(_USPEC)
_USPEC.loader.exec_module(unity)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "Game"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    (project / "Packages").mkdir()
    (project / "Packages" / "manifest.json").write_text('{"dependencies": {"com.unity.ugui": "1.0.0"}}', encoding="utf-8")
    return project


def test_unity_helper_installs_and_uninstalls(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert unity.main(["--project", str(project), "--add-package"]) == 0
    helper = project / "Assets" / "Editor" / "CatCrewUnityMCP.cs"
    assert "class CatCrewUnityMCP" in helper.read_text(encoding="utf-8")
    import json

    deps = json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8"))["dependencies"]
    assert deps["com.unity.ugui"] == "1.0.0" and "com.coplaydev.unity-mcp" in deps
    assert unity.main(["--project", str(project), "--add-package"]) == 0  # second run changes nothing
    assert unity.main(["--project", str(project), "--uninstall"]) == 0
    assert not helper.exists()


def test_unity_helper_refuses_a_folder_that_is_not_a_project(tmp_path: Path) -> None:
    assert unity.main(["--project", str(tmp_path)]) == 2


def test_unity_helper_source_is_wrapped_and_names_the_server() -> None:
    text = unity.HELPER.read_text(encoding="utf-8")
    assert text.count("{") == text.count("}")
    assert text.lstrip().startswith("//") and "#if UNITY_EDITOR" in text and "[InitializeOnLoad]" in text
    assert "localhost" in text and "8080" in text and "jinx" not in text.lower()
