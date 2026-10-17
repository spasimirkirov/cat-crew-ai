import os
import sys
import threading
import time

from pathlib import Path

from jinx_ai.terminal import (
    abort_running_commands,
    get_hub,
    read_terminal,
    reset_abort,
    reset_hub,
    run_foreground,
)
from jinx_ai.tools import _HANG_COMMAND, execute_tool, run_command


def test_hang_regex_covers_node_server_not_npm_install() -> None:
    assert _HANG_COMMAND.search("npm start")
    assert _HANG_COMMAND.search("node server.js")
    assert _HANG_COMMAND.search("npm run dev")
    assert not _HANG_COMMAND.search("npm install axios")
    assert not _HANG_COMMAND.search("npx create-react-app jinx")


def test_hub_tail_and_read_terminal() -> None:
    reset_hub()
    hub = get_hub()
    session = hub.create("npm start", "C:\\tmp", "npm start", background=True)
    hub.write(session.sid, "Error: listen EADDRINUSE: address already in use :::3000\n")
    hub.close(session.sid, 1)
    text = read_terminal(session.sid, 20)
    assert "EADDRINUSE" in text
    listed = execute_tool("read_terminal", {"action": "list"})
    assert session.sid in listed


def test_abort_kills_long_command(tmp_path) -> None:
    reset_hub()
    reset_abort()
    result: dict[str, str] = {}

    def work() -> None:
        result["out"] = run_foreground(
            f'"{sys.executable}" -c "import time; time.sleep(30)"',
            tmp_path,
            timeout=40,
            env=os.environ.copy(),
        )

    thread = threading.Thread(target=work)
    thread.start()
    time.sleep(0.5)
    abort_running_commands()
    thread.join(12)
    assert not thread.is_alive()
    assert "stop" in (result.get("out") or "").lower()
    reset_abort()


def test_run_command_node_server_is_background(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jinx_ai.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("jinx_ai.tools._allowed_roots", lambda: [tmp_path.resolve()])
    monkeypatch.setattr(
        "jinx_ai.terminal.run_background",
        lambda *a, **k: "Started in background pid=7 terminal=xyz",
    )
    out = run_command("node server.js", cwd=str(tmp_path))
    assert "background" in out.lower()


def test_close_last_tab_hides_via_signal() -> None:
    from PyQt6.QtWidgets import QApplication

    from jinx_ai.terminal import TerminalPane

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    reset_hub()
    pane = TerminalPane()
    closed: list[bool] = []
    pane.last_closed.connect(lambda: closed.append(True))
    pane.ensure_session()
    assert pane.tabs.count() >= 1
    while pane.tabs.count():
        pane.close_current()
    assert pane.tabs.count() == 0
    assert closed
    pane.ensure_session()
    assert pane.tabs.count() == 1


def test_shell_tab_uses_single_workspace_folder(tmp_path) -> None:
    from PyQt6.QtWidgets import QApplication, QLineEdit

    from jinx_ai.terminal import TerminalPane
    from jinx_ai.workspace import current_folders, set_folders

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    previous = current_folders()
    try:
        set_folders([tmp_path])
        reset_hub()
        pane = TerminalPane()
        pane.sync_workspace()
        pane.ensure_session()
        session = get_hub().get(pane._current_sid())
        assert session is not None
        assert Path(session.cwd) == tmp_path.resolve()
        assert pane.findChild(QLineEdit, "terminalPrompt") is None
        assert pane.cwd_combo.isHidden()
    finally:
        set_folders(previous)


def test_idle_tab_retargets_when_workspace_changes(tmp_path) -> None:
    from PyQt6.QtWidgets import QApplication

    from jinx_ai.terminal import TerminalPane
    from jinx_ai.workspace import current_folders, set_folders

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    stale = tmp_path / "stale"
    fresh = tmp_path / "fresh"
    stale.mkdir()
    fresh.mkdir()
    previous = current_folders()
    try:
        set_folders([stale])
        reset_hub()
        pane = TerminalPane()
        pane.ensure_session()
        session = get_hub().get(pane._current_sid())
        assert session is not None
        assert Path(session.cwd) == stale.resolve()
        set_folders([fresh])
        pane.sync_workspace()
        session = get_hub().get(pane._current_sid())
        assert session is not None
        assert Path(session.cwd) == fresh.resolve()
    finally:
        set_folders(previous)


def test_multi_folder_picker_sets_new_tab_cwd(tmp_path) -> None:
    from PyQt6.QtWidgets import QApplication

    from jinx_ai.terminal import TerminalPane
    from jinx_ai.workspace import current_folders, set_folders

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    other = tmp_path / "other"
    other.mkdir()
    previous = current_folders()
    try:
        set_folders([tmp_path, other])
        reset_hub()
        pane = TerminalPane()
        pane.sync_workspace()
        assert not pane.cwd_combo.isHidden()
        pane.cwd_combo.setCurrentIndex(1)
        pane.ensure_session()
        session = get_hub().get(pane._current_sid())
        assert session is not None
        assert Path(session.cwd) == other.resolve()
    finally:
        set_folders(previous)
