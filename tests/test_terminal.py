import os
import sys
import threading
import time

from pathlib import Path

from backend.services.terminal import abort_running_commands, get_hub, read_terminal, reset_abort, reset_hub, run_foreground
from backend.tools import _HANG_COMMAND, execute_tool, run_command


def test_hang_regex_covers_node_server_not_npm_install() -> None:
    assert _HANG_COMMAND.search("npm start")
    assert _HANG_COMMAND.search("node server.js")
    assert _HANG_COMMAND.search("npm run dev")
    assert not _HANG_COMMAND.search("npm install axios")
    assert not _HANG_COMMAND.search("npx create-react-app catcrew")


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
    monkeypatch.setattr("backend.tools.current_workspace", lambda: tmp_path)
    monkeypatch.setattr("backend.tools._allowed_roots", lambda: [tmp_path.resolve()])
    monkeypatch.setattr(
        "backend.services.terminal.run_background",
        lambda *a, **k: "Started in background pid=7 terminal=xyz",
    )
    out = run_command("node server.js", cwd=str(tmp_path))
    assert "background" in out.lower()
