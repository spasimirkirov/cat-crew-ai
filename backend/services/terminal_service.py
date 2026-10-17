"""The user's integrated terminal (the panel under a project's chat).

Not to be confused with ``services/terminal.py``, which is the *agents'* command hub. This one
serves a person typing commands, one project at a time:

* **Command per run.** Every submitted line runs in the platform shell (PowerShell, or cmd, on Windows;
  bash/zsh elsewhere) in the session's current directory. No PTY and no native dependency: output is
  piped, so it is line oriented, not a full-screen terminal. What persists between commands is the
  working directory (a bare ``cd`` is handled here); shell variables and aliases do not.
* **stdin** typed while a command runs is forwarded to it (``python``, ``npm init``, ``read``).
* **Interrupt** kills the command's whole process tree.
* **Cleanup**: a session dies with its WebSocket, is replaced when the project reconnects, and
  ``close_all`` runs at app shutdown, so no shell outlives the app or a project switch.

Output and exits are delivered to callbacks that run on worker threads; the WebSocket layer
bridges them to the event loop.
"""

from __future__ import annotations

import codecs
import os
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from backend.services.terminal import _CREATE_NEW_PROCESS_GROUP, _CREATE_NO_WINDOW, _kill_tree

MAX_SESSIONS = 8
CHUNK = 8192
MAX_OUTPUT_CHARS = 4_000_000  # per command; beyond this output is dropped with one notice
MAX_INPUT_CHARS = 64_000
MAX_COMMAND_CHARS = 32_000

OnEvent = Callable[[dict[str, Any]], None]

_CD_WORDS = {"cd", "chdir", "set-location", "sl", "pushd"}
_SHELL_NAMES = {"cmd", "powershell", "pwsh", "bash", "zsh", "sh", "fish", "wsl"}


# ---- shell selection (pure, so both platforms are unit-testable) ------------------------------


def _which(name: str) -> str:
    return shutil.which(name) or ""


def shell_argv(command: str, *, windows: bool | None = None, preferred: str = "") -> list[str]:
    """argv that runs ``command`` in the platform shell.

    ``preferred`` (or ``CATCREW_TERMINAL_SHELL``) picks ``powershell``/``pwsh``/``cmd`` on Windows and
    ``bash``/``zsh``/``sh`` (or a path) elsewhere.
    """
    win = (os.name == "nt") if windows is None else windows
    choice = (preferred or os.environ.get("CATCREW_TERMINAL_SHELL", "")).strip()
    kind = Path(choice).stem.lower() if choice else ""
    if win:
        if kind == "cmd":
            return [_which("cmd.exe") or "cmd.exe", "/d", "/s", "/c", f"chcp 65001>nul & {command}"]
        exe = choice if kind in {"powershell", "pwsh"} and choice else ""
        exe = exe or _which("powershell.exe") or _which("pwsh.exe") or _which("pwsh")
        if not exe:
            return ["cmd.exe", "/d", "/s", "/c", f"chcp 65001>nul & {command}"]
        # UTF-8 both ways so accents and box-drawing survive the pipe.
        prelude = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        # `powershell -Command` exits 1 for any failure, never the program's own code (a build that
        # exits 3 read as 1). Hand $LASTEXITCODE on; a failed cmdlet (no native code) stays 1. On a
        # new line, so a trailing `# comment` in the command cannot swallow it.
        epilogue = "\n$__ok = $?; if (-not $__ok) { if ($LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }"
        return [exe, "-NoLogo", "-NoProfile", "-Command", f"{prelude} {command}{epilogue}"]
    exe = ""
    if choice:
        exe = choice if os.path.isabs(choice) else _which(choice)
    if not exe:
        env_shell = os.environ.get("SHELL", "")
        if Path(env_shell).name in {"bash", "zsh"} and Path(env_shell).exists():
            exe = env_shell
    exe = exe or _which("bash") or _which("zsh") or _which("sh") or "/bin/sh"
    return [exe, "-c", command]


def shell_label(*, windows: bool | None = None, preferred: str = "") -> str:
    """Short name of the shell ``shell_argv`` would use (shown in the panel header)."""
    argv = shell_argv("", windows=windows, preferred=preferred)
    name = Path(argv[0]).stem.lower()
    return {"powershell": "PowerShell", "pwsh": "PowerShell", "cmd": "cmd"}.get(name, name or "sh")


def _terminal_env() -> dict[str, str]:
    """The app's environment minus the model-provider keys it holds, plus colour hints.

    The user's own variables (PATH, GITHUB_TOKEN...) stay: it is their terminal. Only the keys
    CatCrewAI itself was configured with are withheld, so a stray ``env`` does not print them.
    """
    env = dict(os.environ)
    try:
        from backend.services.api_access import CATALOG

        for spec in CATALOG:
            for name in spec.env_names:
                env.pop(name, None)
    except Exception:  # noqa: BLE001 - never let hygiene break the terminal
        pass
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("FORCE_COLOR", "1")
    env.setdefault("CLICOLOR_FORCE", "1")
    env.setdefault("TERM", "xterm-256color")
    return env


def is_bare_shell(command: str) -> str:
    """The shell's name when the whole command is just a shell (``cmd``, ``powershell``, ``bash`` ...).

    Started that way it would be a second shell reading our pipe: no prompt, nothing visible, and it
    never ends by itself. ``cmd /c dir`` or ``bash -c "..."`` (with arguments) are ordinary commands.
    """
    parts = command.strip().split()
    if len(parts) != 1:
        return ""
    name = Path(parts[0].strip("\"'")).stem.lower()
    return name if name in _SHELL_NAMES else ""


# ---- cd handling ------------------------------------------------------------------------------


def parse_cd(command: str) -> str | None:
    """The target of a bare ``cd`` command, ``""`` for a lone ``cd``, or None for anything else.

    Compound lines (``cd x && npm test``) belong to the shell and return None.
    """
    text = command.strip()
    if not text or any(op in text for op in ("&&", "||", ";", "|", "&", ">", "<", "\n")):
        return None
    try:
        parts = shlex.split(text, posix=os.name != "nt")
    except ValueError:
        return None
    if not parts or parts[0].lower() not in _CD_WORDS:
        return None
    args = [p for p in parts[1:] if p.lower() not in {"/d", "-l", "-p", "-literalpath", "-path"}]
    if len(args) > 1:
        return " ".join(args)  # unquoted path with spaces
    target = args[0] if args else ""
    return target.strip("\"'")


def resolve_cd(current: Path, target: str, home: Path | None = None) -> Path | None:
    """Where a ``cd`` would land, or None when it is not a directory."""
    home = home or Path.home()
    if not target or target == "~":
        dest = home
    elif target.startswith(("~/", "~\\")):
        dest = home / target[2:]
    else:
        dest = Path(target)
        if not dest.is_absolute():
            dest = current / dest
    try:
        dest = dest.resolve()
    except OSError:
        return None
    return dest if dest.is_dir() else None


# ---- session ----------------------------------------------------------------------------------


class TerminalSession:
    """One project's terminal: current directory, at most one running command."""

    def __init__(self, project_id: str, cwd: Path, on_event: OnEvent, *, shell: str = "") -> None:
        self.project_id = project_id
        self.cwd = cwd
        self.root = cwd
        self.shell = shell
        self._emit = on_event
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._closed = False
        self._interrupted = False

    # -- state --

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def closed(self) -> bool:
        return self._closed

    def _send(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            self._emit(event)
        except Exception:  # noqa: BLE001 - a dead socket must not kill a reader thread
            pass

    # -- commands --

    def run(self, command: str) -> None:
        """Start ``command``. Errors are reported as events, never raised."""
        command = (command or "").rstrip("\r\n")
        if not command.strip():
            self._send({"type": "exit", "code": 0, "cwd": str(self.cwd)})
            return
        if len(command) > MAX_COMMAND_CHARS:
            self._send({"type": "error", "message": "That command is too long."})
            return
        with self._lock:
            if self._closed:
                return
            if self.running:
                self._send({"type": "error", "message": "A command is still running. Send input, or interrupt it."})
                return
            target = parse_cd(command)
            if target is not None:
                self._change_dir(target)
                return
            nested = is_bare_shell(command)
            if nested:
                here = shell_label(preferred=self.shell)
                self._send(
                    {
                        "type": "output",
                        "stream": "stderr",
                        "data": (
                            f"This terminal already runs {here}, and every command gets its own shell, so a nested "
                            f"'{nested}' would just wait for input. Type commands directly, or run one in another "
                            f"shell with e.g. `{nested} {'/c' if nested == 'cmd' else '-c'} \"...\"`.\n"
                        ),
                    }
                )
                self._send({"type": "exit", "code": 0, "cwd": str(self.cwd)})
                return
            try:
                proc = self._spawn(command)
            except OSError as exc:
                self._send({"type": "output", "stream": "stderr", "data": f"Could not start the shell: {exc}\n"})
                self._send({"type": "exit", "code": 127, "cwd": str(self.cwd)})
                return
            self._proc = proc
            self._interrupted = False
        self._send({"type": "started"})
        threading.Thread(target=self._pump, args=(proc,), daemon=True, name="catcrew-terminal").start()

    def _change_dir(self, target: str) -> None:
        dest = resolve_cd(self.cwd, target)
        if dest is None:
            self._send({"type": "output", "stream": "stderr", "data": f"cd: no such directory: {target or '~'}\n"})
            self._send({"type": "exit", "code": 1, "cwd": str(self.cwd)})
            return
        self.cwd = dest
        self._send({"type": "exit", "code": 0, "cwd": str(dest)})

    def _spawn(self, command: str) -> subprocess.Popen:
        kwargs: dict[str, Any] = {
            "args": shell_argv(command, preferred=self.shell),
            "cwd": str(self.cwd),
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": _terminal_env(),
        }
        if os.name == "nt":
            kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(**kwargs)

    def _pump(self, proc: subprocess.Popen) -> None:
        budget = [MAX_OUTPUT_CHARS]
        noted = [False]
        note_lock = threading.Lock()

        def read(stream: Any, name: str) -> None:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                try:
                    raw = os.read(stream.fileno(), CHUNK)
                except (OSError, ValueError):
                    break
                if not raw:
                    break
                text = decoder.decode(raw)
                if not text:
                    continue
                with note_lock:
                    if budget[0] <= 0:
                        if not noted[0]:
                            noted[0] = True
                            self._send({"type": "output", "stream": "stderr", "data": "\n[output truncated]\n"})
                        continue
                    budget[0] -= len(text)
                self._send({"type": "output", "stream": name, "data": text})
            tail = decoder.decode(b"", final=True)
            if tail:
                self._send({"type": "output", "stream": name, "data": tail})

        readers = [
            threading.Thread(target=read, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=read, args=(proc.stderr, "stderr"), daemon=True),
        ]
        for thread in readers:
            thread.start()
        code = proc.wait()
        for thread in readers:
            thread.join(timeout=3)  # a grandchild holding the pipe open must not hang the session
        with self._lock:
            interrupted = self._interrupted
            if self._proc is proc:
                self._proc = None
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        self._send({"type": "exit", "code": code, "cwd": str(self.cwd), "interrupted": interrupted})

    # -- stdin / interrupt / close --

    def send_input(self, data: str) -> bool:
        """Forward text to the running command's stdin. False when nothing is running."""
        if not data or len(data) > MAX_INPUT_CHARS:
            return False
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or proc.stdin is None:
                return False
            try:
                proc.stdin.write(data.encode("utf-8", "replace"))
                proc.stdin.flush()
            except (OSError, ValueError):
                return False
        return True

    def close_stdin(self) -> None:
        with self._lock:
            proc = self._proc
            if proc is not None and proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

    def interrupt(self) -> bool:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return False
            self._interrupted = True
        _kill_tree(proc)
        return True

    def close(self) -> None:
        """Kill any running command and stop emitting. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            proc = self._proc
            self._closed = True
        _kill_tree(proc)


# ---- registry: one session per project, capped, closable as a whole ---------------------------


class TerminalService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, TerminalSession] = {}
        self._last_cwd: dict[str, Path] = {}

    def open(self, project_id: str, root: Path, on_event: OnEvent, *, shell: str = "") -> TerminalSession:
        """A fresh session for the project, replacing (and killing) any earlier one.

        Reopening a project resumes in the directory the last session ended in, when it still exists.
        """
        with self._lock:
            old = self._sessions.pop(project_id, None)
            if old is not None:
                self._last_cwd[project_id] = old.cwd
            start = self._last_cwd.get(project_id)
            if start is None or not start.is_dir():
                start = root
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = next(iter(self._sessions))
                self._sessions.pop(oldest).close()
            session = TerminalSession(project_id, start, on_event, shell=shell)
            session.root = root
            self._sessions[project_id] = session
        if old is not None:
            old.close()
        return session

    def release(self, session: TerminalSession) -> None:
        """Close ``session`` and forget it (only if it is still the project's current one)."""
        with self._lock:
            if self._sessions.get(session.project_id) is session:
                del self._sessions[session.project_id]
                self._last_cwd[session.project_id] = session.cwd
        session.close()

    def close_project(self, project_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(project_id, None)
            self._last_cwd.pop(project_id, None)
        if session is not None:
            session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    def active(self) -> int:
        with self._lock:
            return len(self._sessions)


_service: TerminalService | None = None
_service_lock = threading.Lock()


def get_service() -> TerminalService:
    global _service
    with _service_lock:
        if _service is None:
            _service = TerminalService()
        return _service


def close_all() -> None:
    if _service is not None:
        _service.close_all()


def project_root(folders: list[str], project_id: str) -> Path:
    """Where a project's terminal opens: its first folder, else a private scratch dir."""
    for folder in folders:
        path = Path(folder).expanduser()
        if path.is_dir():
            return path
    from backend.services.session_fs import scratch_root

    scratch = scratch_root() / f"terminal-{project_id}"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch
