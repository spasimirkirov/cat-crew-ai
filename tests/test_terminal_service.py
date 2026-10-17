"""The user's integrated terminal: shell choice, cd tracking, streaming, stdin, interrupt, cleanup, WebSocket."""

from __future__ import annotations

import os
import queue
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.core.chat_service import ChatService
from backend.services import terminal_service as ts

BASE = "http://127.0.0.1:8765"


def _py(code: str) -> str:
    """A command that runs ``code`` with this interpreter, in whichever shell the platform uses."""
    if os.name == "nt":
        return f"& '{sys.executable}' -c \"{code}\""
    return f"'{sys.executable}' -c \"{code}\""


class Collector:
    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.q.put(event)

    def until(self, kind: str, timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                event = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            self.events.append(event)
            if event["type"] == kind:
                return event
        raise AssertionError(f"no {kind!r} event; saw {self.events}")

    def text(self, stream: str = "") -> str:
        return "".join(e["data"] for e in self.events if e["type"] == "output" and (not stream or e["stream"] == stream))


@pytest.fixture
def session(tmp_path):
    out = Collector()
    s = ts.TerminalSession("p1", tmp_path, out)
    yield s, out
    s.close()


# ---- shell selection ----------------------------------------------------------------------------


def test_windows_prefers_powershell_and_falls_back_to_cmd(monkeypatch) -> None:
    monkeypatch.delenv("CATCREW_TERMINAL_SHELL", raising=False)
    monkeypatch.setattr(ts, "_which", lambda n: f"C:\\bin\\{n}" if n == "powershell.exe" else "")
    argv = ts.shell_argv("dir", windows=True)
    assert argv[0].endswith("powershell.exe") and "-NoProfile" in argv and "UTF8; dir\n" in argv[-1]
    assert "exit $LASTEXITCODE" in argv[-1]  # the program's own exit code, not PowerShell's 1
    monkeypatch.setattr(ts, "_which", lambda n: "")
    assert ts.shell_argv("dir", windows=True)[:4] == ["cmd.exe", "/d", "/s", "/c"]
    assert ts.shell_label(windows=True) == "cmd"


def test_windows_can_be_told_to_use_cmd(monkeypatch) -> None:
    monkeypatch.setattr(ts, "_which", lambda n: f"C:\\win\\{n}")
    argv = ts.shell_argv("dir", windows=True, preferred="cmd")
    assert argv[0].endswith("cmd.exe") and argv[-1].endswith("dir") and argv[1:4] == ["/d", "/s", "/c"]


def test_posix_uses_bash_or_zsh_from_the_environment_else_bash(monkeypatch, tmp_path) -> None:
    zsh = tmp_path / "zsh"
    zsh.write_text("")
    monkeypatch.delenv("CATCREW_TERMINAL_SHELL", raising=False)
    monkeypatch.setenv("SHELL", str(zsh))
    assert ts.shell_argv("ls", windows=False) == [str(zsh), "-c", "ls"]
    monkeypatch.setenv("SHELL", "/usr/bin/fish")  # not one we drive
    monkeypatch.setattr(ts, "_which", lambda n: f"/usr/bin/{n}" if n == "bash" else "")
    assert ts.shell_argv("ls", windows=False) == ["/usr/bin/bash", "-c", "ls"]
    monkeypatch.setattr(ts, "_which", lambda n: "")
    assert ts.shell_argv("ls", windows=False)[0] == "/bin/sh"


def test_the_environment_variable_picks_the_shell(monkeypatch) -> None:
    monkeypatch.setenv("CATCREW_TERMINAL_SHELL", "zsh")
    monkeypatch.setattr(ts, "_which", lambda n: f"/opt/{n}")
    assert ts.shell_argv("ls", windows=False)[0] == "/opt/zsh"


# ---- cd -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "target"),
    [("cd", ""), ("cd ..", ".."), ("cd src", "src"), ('cd "my dir"', "my dir"), ("Set-Location src", "src"), ("cd /d src", "src")],
)
def test_parse_cd_recognises_a_bare_cd(line, target) -> None:
    assert ts.parse_cd(line) == target


@pytest.mark.parametrize("line", ["cd x && npm test", "cd x; ls", "echo cd", "ls | cd", "", "cdx"])
def test_parse_cd_leaves_everything_else_to_the_shell(line) -> None:
    assert ts.parse_cd(line) is None


def test_resolve_cd(tmp_path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    assert ts.resolve_cd(tmp_path, "a") == (tmp_path / "a").resolve()
    assert ts.resolve_cd(tmp_path / "a", "..") == tmp_path.resolve()
    assert ts.resolve_cd(tmp_path, "nope") is None
    assert ts.resolve_cd(tmp_path, "", home=tmp_path / "a") == (tmp_path / "a").resolve()
    assert ts.resolve_cd(tmp_path, "~/b", home=tmp_path / "a") == (tmp_path / "a" / "b").resolve()


@pytest.mark.parametrize("line", ["cmd", "CMD", "powershell", "pwsh.exe", "bash", " zsh ", "wsl"])
def test_a_bare_shell_is_recognised(line) -> None:
    assert ts.is_bare_shell(line)


@pytest.mark.parametrize("line", ["cmd /c dir", "bash -c ls", "echo cmd", "python", "cmdx", ""])
def test_a_shell_with_arguments_or_anything_else_is_not_a_bare_shell(line) -> None:
    assert ts.is_bare_shell(line) == ""


def test_typing_a_shell_name_does_not_start_a_stuck_nested_shell(session) -> None:
    s, out = session
    s.run("cmd")
    assert out.until("exit")["code"] == 0
    assert "already runs" in out.text("stderr") and s.running is False
    s.run(_py("print('still fine')"))  # the session is untouched
    out.until("exit")
    assert "still fine" in out.text()


# ---- running commands ---------------------------------------------------------------------------


def test_a_command_streams_output_and_exits_zero(session) -> None:
    s, out = session
    s.run(_py("print('hello terminal')"))
    exit_ = out.until("exit")
    assert exit_["code"] == 0 and exit_["cwd"] == str(s.cwd)
    assert "hello terminal" in out.text("stdout")


def test_stderr_is_a_separate_stream_and_the_exit_code_is_reported(session) -> None:
    s, out = session
    s.run(_py("import sys; sys.stderr.write('boom'); sys.exit(3)"))
    assert out.until("exit")["code"] == 3
    assert "boom" in out.text("stderr") and "boom" not in out.text("stdout")


def test_a_command_runs_in_the_session_directory(session, tmp_path) -> None:
    s, out = session
    s.run(_py("import os; print(os.getcwd())"))
    out.until("exit")
    assert Path(out.text().strip()).resolve() == tmp_path.resolve()


def test_cd_persists_between_commands(session, tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    s, out = session
    s.run("cd sub")
    assert out.until("exit")["cwd"] == str((tmp_path / "sub").resolve())
    s.run(_py("import os; print(os.getcwd())"))
    out.until("exit")
    assert Path(out.text().strip().splitlines()[-1]).resolve() == (tmp_path / "sub").resolve()


def test_cd_to_a_missing_directory_fails_and_stays_put(session, tmp_path) -> None:
    s, out = session
    s.run("cd nowhere")
    event = out.until("exit")
    assert event["code"] == 1 and event["cwd"] == str(tmp_path)
    assert "no such directory" in out.text("stderr")


def test_stdin_reaches_the_running_command(session) -> None:
    s, out = session
    s.run(_py("name = input(); print('hi ' + name)"))
    out.until("started")
    deadline = time.time() + 10
    while not s.send_input("Ada\n") and time.time() < deadline:
        time.sleep(0.05)
    out.until("exit")
    assert "hi Ada" in out.text("stdout")


def test_input_with_nothing_running_is_refused(session) -> None:
    s, _out = session
    assert s.send_input("x\n") is False


def test_a_second_command_while_busy_is_refused(session) -> None:
    s, out = session
    s.run(_py("import time; time.sleep(30)"))
    out.until("started")
    s.run(_py("print(1)"))
    assert "still running" in out.until("error")["message"]
    assert s.interrupt() is True
    assert out.until("exit")["interrupted"] is True


def test_interrupt_kills_a_long_command_quickly(session) -> None:
    s, out = session
    started = time.time()
    s.run(_py("import time; time.sleep(60)"))
    out.until("started")
    time.sleep(0.5)
    assert s.interrupt()
    event = out.until("exit", timeout=15)
    assert event["interrupted"] is True and time.time() - started < 20
    assert s.running is False
    s.run(_py("print('again')"))  # the session is reusable afterwards
    out.until("exit")
    assert "again" in out.text()


def test_huge_output_is_capped(session, monkeypatch) -> None:
    monkeypatch.setattr(ts, "MAX_OUTPUT_CHARS", 2000)
    s, out = session
    s.run(_py("print('x' * 50000)"))
    out.until("exit")
    assert len(out.text()) < 20_000 and "[output truncated]" in out.text("stderr")


def test_close_kills_the_running_command_and_silences_the_session(tmp_path) -> None:
    out = Collector()
    s = ts.TerminalSession("p1", tmp_path, out)
    s.run(_py("import time; time.sleep(60)"))
    out.until("started")
    proc = s._proc
    s.close()
    assert proc.wait(timeout=10) is not None
    time.sleep(0.5)
    while not out.q.empty():
        assert out.q.get_nowait()["type"] != "exit"  # no exit after close


def test_provider_keys_are_not_handed_to_the_shell(session, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("MY_OWN_VAR", "kept")
    s, out = session
    s.run(_py("import os; print(os.environ.get('OPENROUTER_API_KEY', 'absent'), os.environ.get('MY_OWN_VAR'))"))
    out.until("exit")
    assert "absent kept" in out.text()


# ---- registry -----------------------------------------------------------------------------------


def test_opening_a_project_again_replaces_and_kills_the_old_session_and_resumes_cwd(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    svc = ts.TerminalService()
    first_out = Collector()
    first = svc.open("p1", tmp_path, first_out)
    first.run("cd sub")
    first_out.until("exit")
    second = svc.open("p1", tmp_path, Collector())
    assert first.closed and not second.closed
    assert second.cwd == (tmp_path / "sub").resolve()  # resumed where the last one ended
    assert svc.active() == 1
    svc.close_all()
    assert second.closed and svc.active() == 0


def test_the_session_cap_closes_the_oldest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ts, "MAX_SESSIONS", 2)
    svc = ts.TerminalService()
    a = svc.open("a", tmp_path, Collector())
    svc.open("b", tmp_path, Collector())
    svc.open("c", tmp_path, Collector())
    assert a.closed and svc.active() == 2
    svc.close_all()


def test_project_root_prefers_the_first_existing_folder_else_scratch(tmp_path, monkeypatch) -> None:
    real = tmp_path / "site"
    real.mkdir()
    assert ts.project_root([str(tmp_path / "gone"), str(real)], "p1") == real
    import backend.services.session_fs as fs

    monkeypatch.setattr(fs, "scratch_root", lambda: tmp_path / "scratch")
    root = ts.project_root([], "p9")
    assert root.is_dir() and root.parent == tmp_path / "scratch"


# ---- WebSocket ----------------------------------------------------------------------------------


@pytest.fixture
def client(isolated):
    app = create_app(chat_service=ChatService(use_memory=False))
    with TestClient(app, base_url=BASE) as c:
        yield c


def _project(client, tmp_path):
    folder = tmp_path / "ws"
    folder.mkdir(exist_ok=True)
    r = client.post("/api/projects", json={"name": "Alpha", "folders": [str(folder)]})
    assert r.status_code == 201, r.text
    return r.json()["id"], folder


def _connect(client, pid, **headers):
    return client.websocket_connect(f"/ws/projects/{pid}/terminal", headers={"host": "127.0.0.1:8765", **headers})


def _drain(ws, until: str, limit: int = 200) -> list[dict]:
    frames = []
    for _ in range(limit):
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] == until:
            return frames
    raise AssertionError(f"no {until!r} frame in {frames[-5:]}")


def test_websocket_runs_a_command_in_the_project_folder(client, tmp_path) -> None:
    pid, folder = _project(client, tmp_path)
    with _connect(client, pid) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready" and Path(ready["cwd"]).resolve() == folder.resolve()
        assert ready["shell"] and ready["platform"] == os.name
        ws.send_json({"type": "run", "command": _py("print('over the wire')")})
        frames = _drain(ws, "exit")
        assert frames[-1]["code"] == 0
        assert "over the wire" in "".join(f["data"] for f in frames if f["type"] == "output")
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_websocket_stdin_and_interrupt(client, tmp_path) -> None:
    pid, _ = _project(client, tmp_path)
    with _connect(client, pid) as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "command": _py("import time; time.sleep(60)")})
        _drain(ws, "started")
        ws.send_json({"type": "interrupt"})
        assert _drain(ws, "exit")[-1]["interrupted"] is True


def test_websocket_unknown_project_is_closed_without_a_shell(client) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as gone:
        with _connect(client, "nope"):
            pass
    assert gone.value.code == 4404
    assert ts.get_service().active() == 0


def test_websocket_bad_origin_never_starts_a_shell(client, tmp_path) -> None:
    from starlette.websockets import WebSocketDisconnect

    pid, _ = _project(client, tmp_path)
    with pytest.raises(WebSocketDisconnect):
        with _connect(client, pid, origin="https://evil.example"):
            pass
    assert ts.get_service().active() == 0


def test_websocket_disconnect_kills_the_running_command(client, tmp_path) -> None:
    pid, _ = _project(client, tmp_path)
    with _connect(client, pid) as ws:
        ws.receive_json()
        ws.send_json({"type": "run", "command": _py("import time; time.sleep(60)")})
        _drain(ws, "started")
        session = ts.get_service()._sessions[pid]
        proc = session._proc
    deadline = time.time() + 10
    while time.time() < deadline and (proc.poll() is None):
        time.sleep(0.1)
    assert proc.poll() is not None and session.closed
    assert ts.get_service().active() == 0


def test_deleting_a_project_closes_its_terminal(client, tmp_path) -> None:
    pid, _ = _project(client, tmp_path)
    with _connect(client, pid) as ws:
        ws.receive_json()
        session = ts.get_service()._sessions[pid]
        assert client.delete(f"/api/projects/{pid}").status_code == 204
        assert session.closed
