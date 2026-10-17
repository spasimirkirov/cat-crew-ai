"""Headless terminal hub: run shell commands for agents and stream their output.

Output is fanned out to subscribers (the API layer bridges them to the
WebSocket event stream); there is no GUI code here."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_MAX_CHARS = 240_000
_TAIL_DEFAULT = 80

Listener = Callable[[str, dict[str, Any]], None]


def _kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    pid = proc.pid
    if os.name == "nt":
        flags = _CREATE_NO_WINDOW
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=8,
                creationflags=flags,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=4)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
            except OSError:
                pass
        return
    try:
        os.killpg(os.getpgid(pid), 15)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


class TerminalSession:
    def __init__(
        self,
        sid: str,
        title: str,
        cwd: str,
        command: str,
        background: bool,
        *,
        interactive: bool = False,
    ) -> None:
        self.sid = sid
        self.title = title
        self.cwd = cwd
        self.command = command
        self.background = background
        self.interactive = interactive
        self.chunks: deque[str] = deque()
        self.chars = 0
        self.running = not interactive
        self.exit_code: int | None = 0 if interactive else None
        self.pid: int | None = None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def append(self, text: str) -> None:
        if not text:
            return
        with self.lock:
            self.chunks.append(text)
            self.chars += len(text)
            while self.chars > _MAX_CHARS and self.chunks:
                dropped = self.chunks.popleft()
                self.chars -= len(dropped)

    def snapshot(self) -> str:
        with self.lock:
            return "".join(self.chunks)

    def tail(self, lines: int = _TAIL_DEFAULT) -> str:
        body = self.snapshot()
        if not body:
            return "(no output yet)"
        parts = body.splitlines()
        n = max(1, min(int(lines or _TAIL_DEFAULT), 400))
        return "\n".join(parts[-n:])


class TerminalHub:
    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._order: list[str] = []
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Listener) -> None:
        with self._lock:
            self._listeners.append(fn)

    def unsubscribe(self, fn: Listener) -> None:
        with self._lock:
            self._listeners = [item for item in self._listeners if item is not fn]

    def _emit(self, event: str, **payload: Any) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event, payload)
            except Exception:  # noqa: BLE001
                pass

    def create(
        self,
        title: str,
        cwd: str,
        command: str,
        *,
        background: bool,
        interactive: bool = False,
    ) -> TerminalSession:
        sid = uuid.uuid4().hex[:10]
        session = TerminalSession(
            sid,
            title[:48] or "Terminal",
            str(cwd),
            command,
            background,
            interactive=interactive,
        )
        with self._lock:
            self._sessions[sid] = session
            self._order.append(sid)
        self._emit("created", sid=sid, title=session.title, cwd=cwd, command=command)
        return session

    def get(self, sid: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(sid)

    def list_sessions(self) -> list[TerminalSession]:
        with self._lock:
            return [self._sessions[sid] for sid in self._order if sid in self._sessions]

    def active_sid(self) -> str:
        with self._lock:
            for sid in reversed(self._order):
                session = self._sessions.get(sid)
                if session and session.running:
                    return sid
            return self._order[-1] if self._order else ""

    def close(self, sid: str, exit_code: int | None = None) -> None:
        session = self.get(sid)
        if session is None:
            return
        session.running = False
        session.exit_code = exit_code
        self._emit("closed", sid=sid, exit_code=exit_code)

    def write(self, sid: str, text: str) -> None:
        session = self.get(sid)
        if session is None or not text:
            return
        session.append(text)
        self._emit("chunk", sid=sid, text=text)

    def kill(self, sid: str) -> str:
        session = self.get(sid)
        if session is None:
            return "Error: no such terminal."
        _kill_tree(session.proc)
        return f"Killed terminal {sid}."

    def kill_all(self) -> None:
        for session in self.list_sessions():
            if session.running:
                _kill_tree(session.proc)


_hub: TerminalHub | None = None
_abort = threading.Event()
_fg_lock = threading.Lock()
_fg_procs: list[subprocess.Popen] = []


def get_hub() -> TerminalHub:
    global _hub
    if _hub is None:
        _hub = TerminalHub()
    return _hub


def reset_hub() -> None:
    global _hub
    if _hub is not None:
        _hub.kill_all()
    _hub = TerminalHub()
    _abort.clear()
    with _fg_lock:
        _fg_procs.clear()


def reset_abort() -> None:
    _abort.clear()


def abort_running_commands() -> None:
    """Stop the current foreground command. Background tabs stay until the user kills them."""
    _abort.set()
    with _fg_lock:
        procs = list(_fg_procs)
    for proc in procs:
        _kill_tree(proc)


def _track(proc: subprocess.Popen, *, foreground: bool) -> None:
    if not foreground:
        return
    with _fg_lock:
        _fg_procs.append(proc)


def _untrack(proc: subprocess.Popen) -> None:
    with _fg_lock:
        try:
            _fg_procs.remove(proc)
        except ValueError:
            pass


def _title_for(command: str) -> str:
    body = " ".join((command or "").split())
    return body[:42] or "Terminal"


def _popen(command: str, cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    kwargs: dict[str, Any] = {
        "args": command,
        "shell": True,
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(**kwargs)


def _pump(session: TerminalSession, proc: subprocess.Popen, timeout: float | None) -> int:
    """Read stdout until exit, abort, or timeout. Returns the process exit code."""
    session.proc = proc
    session.pid = proc.pid
    deadline = None if timeout is None or timeout <= 0 else time.monotonic() + timeout
    lines: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        try:
            stream = proc.stdout
            if stream is None:
                return
            while True:
                line = stream.readline()
                if line == "":
                    break
                lines.put(line)
        except Exception:  # noqa: BLE001
            pass
        finally:
            lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    timed_out = False
    while True:
        if _abort.is_set() and not session.background:
            _kill_tree(proc)
            get_hub().write(session.sid, "\n[stopped]\n")
            break
        if deadline is not None and time.monotonic() > deadline:
            timed_out = True
            _kill_tree(proc)
            get_hub().write(session.sid, f"\n[timed out after {int(timeout)}s]\n")
            break
        try:
            chunk = lines.get(timeout=0.12)
        except queue.Empty:
            if proc.poll() is not None:
                while True:
                    try:
                        rest = lines.get_nowait()
                    except queue.Empty:
                        break
                    if rest is None:
                        break
                    get_hub().write(session.sid, rest)
                break
            continue
        if chunk is None:
            break
        get_hub().write(session.sid, chunk)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
    code = proc.returncode if proc.returncode is not None else -1
    if timed_out and code is None:
        code = -1
    get_hub().close(session.sid, code)
    return int(code if code is not None else -1)


def run_foreground(
    command: str,
    cwd: Path,
    *,
    timeout: int,
    env: dict[str, str],
) -> str:
    hub = get_hub()
    session = hub.create(_title_for(command), str(cwd), command, background=False)
    try:
        proc = _popen(command, cwd, env)
    except OSError as exc:
        hub.write(session.sid, f"Error running command: {exc}\n")
        hub.close(session.sid, 1)
        return f"Error running command: {exc}"
    _track(proc, foreground=True)
    try:
        if _abort.is_set():
            _kill_tree(proc)
            hub.write(session.sid, "\n[stopped]\n")
            hub.close(session.sid, -1)
            return "Stopped: command aborted."
        code = _pump(session, proc, timeout)
    finally:
        _untrack(proc)
    out = session.snapshot().strip() or "(no output)"
    if _abort.is_set():
        return f"Stopped: command aborted.\n{out}"
    if timeout and timeout > 0 and "[timed out" in out:
        return f"Command timed out after {timeout}s: {command}\n{out}"
    return f"exit {code} @ {cwd}\n{out}"


def run_in_session(
    session: TerminalSession,
    command: str,
    *,
    env: dict[str, str],
    timeout: int = 0,
) -> int:
    """Run a command inside an existing tab and reprint the prompt when it ends."""
    hub = get_hub()
    cwd = Path(session.cwd)
    session.command = command
    session.running = True
    session.exit_code = None
    try:
        proc = _popen(command, cwd, env)
    except OSError as exc:
        hub.write(session.sid, f"Error running command: {exc}\n")
        session.running = False
        session.exit_code = 1
        hub.close(session.sid, 1)
        hub.write(session.sid, f"{cwd}> ")
        return 1
    _track(proc, foreground=True)
    try:
        code = _pump(session, proc, timeout)
    finally:
        _untrack(proc)
        session.running = False
    hub.write(session.sid, f"{cwd}> ")
    return int(code)


def run_background(command: str, cwd: Path, *, env: dict[str, str]) -> str:
    hub = get_hub()
    session = hub.create(_title_for(command), str(cwd), command, background=True)
    try:
        proc = _popen(command, cwd, env)
    except OSError as exc:
        hub.write(session.sid, f"Error running command: {exc}\n")
        hub.close(session.sid, 1)
        return f"Error running command: {exc}"
    session.proc = proc
    session.pid = proc.pid

    def worker() -> None:
        _pump(session, proc, timeout=None)

    threading.Thread(target=worker, daemon=True).start()
    time.sleep(1.2)
    if _abort.is_set():
        _kill_tree(proc)
        return "Stopped: command aborted."
    tail = session.tail(60)
    return (
        f"Started in background pid={proc.pid} terminal={session.sid} @ {cwd}. "
        "Leave it running. Typical URL http://localhost:3000 — wait, then "
        "read_terminal if you need errors, then open_url.\n"
        f"--- output so far ---\n{tail}"
    )


def read_terminal(session_id: str = "", lines: int = _TAIL_DEFAULT) -> str:
    hub = get_hub()
    sid = (session_id or "").strip() or hub.active_sid()
    if not sid:
        return "No terminal sessions yet. Output from run_command appears here."
    session = hub.get(sid)
    if session is None:
        listed = _list_text(hub)
        return f"Error: unknown terminal '{sid}'.\n{listed}"
    state = "running" if session.running else f"exit {session.exit_code}"
    body = session.tail(lines)
    return (
        f"terminal {session.sid} ({state}) cwd={session.cwd}\n"
        f"$ {session.command}\n{body}"
    )


def list_terminals() -> str:
    return _list_text(get_hub())


def _list_text(hub: TerminalHub) -> str:
    rows = hub.list_sessions()
    if not rows:
        return "No terminal sessions."
    lines = []
    for session in rows:
        state = "running" if session.running else f"exit {session.exit_code}"
        lines.append(f"{session.sid}  {state}  {session.title}")
    return "\n".join(lines)


def _shell_title(cwd: Path | str) -> str:
    path = Path(cwd)
    return path.name or str(path)
