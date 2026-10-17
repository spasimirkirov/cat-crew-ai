"""VS Code-style terminal: stream shell output for the user and the agent."""

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

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .fa import icon_button
from .icon import create_activity_icon
from .workspace import current_workspace, terminal_folders

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


class TerminalView(QPlainTextEdit):
    """History plus a live prompt line — type here, not in a second input."""

    submitted = pyqtSignal(str)
    interrupt = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("terminalView")
        self.setUndoRedoEnabled(False)
        self._interactive = False
        self._busy = False
        self._prompt_pos = 0

    def set_interactive(self, on: bool) -> None:
        self._interactive = bool(on)
        self.setReadOnly(not self._interactive)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)

    def mark_prompt(self) -> None:
        self.moveCursor(QTextCursor.MoveOperation.End)
        self._prompt_pos = self.textCursor().position()

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        if not self._interactive or self._busy:
            return
        self.moveCursor(QTextCursor.MoveOperation.End)
        text = (source.text() or "").replace("\r\n", "\n").replace("\r", "\n")
        self.insertPlainText(text.replace("\n", " "))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._interactive:
            super().keyPressEvent(event)
            return
        key = event.key()
        mods = event.modifiers()
        cursor = self.textCursor()
        copy = event.matches(QKeySequence.StandardKey.Copy)
        if self._busy:
            if copy and cursor.hasSelection():
                super().keyPressEvent(event)
                return
            if key == Qt.Key.Key_C and mods == Qt.KeyboardModifier.ControlModifier:
                self.interrupt.emit()
                return
            if key in {
                Qt.Key.Key_Left,
                Qt.Key.Key_Right,
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
                Qt.Key.Key_PageUp,
                Qt.Key.Key_PageDown,
                Qt.Key.Key_Home,
                Qt.Key.Key_End,
            }:
                super().keyPressEvent(event)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cmd = self.toPlainText()[self._prompt_pos :]
            self.moveCursor(QTextCursor.MoveOperation.End)
            self.insertPlainText("\n")
            self.submitted.emit(cmd.rstrip("\n"))
            return
        if key == Qt.Key.Key_Backspace and cursor.position() <= self._prompt_pos:
            return
        if key == Qt.Key.Key_Left and cursor.position() <= self._prompt_pos:
            return
        if key == Qt.Key.Key_Home:
            cursor.setPosition(self._prompt_pos)
            self.setTextCursor(cursor)
            return
        if (
            not copy
            and not event.matches(QKeySequence.StandardKey.SelectAll)
            and cursor.position() < self._prompt_pos
            and not cursor.hasSelection()
        ):
            self.moveCursor(QTextCursor.MoveOperation.End)
        super().keyPressEvent(event)


class TerminalPane(QWidget):
    """Bottom panel: one tab per shell, typed in the view itself."""

    last_closed = pyqtSignal()
    session_opened = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("terminalPane")
        self._views: dict[str, TerminalView] = {}
        self._picked_cwd: Path | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("terminalHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(8, 2, 6, 2)
        row.setSpacing(6)
        glyph = QLabel()
        glyph.setPixmap(create_activity_icon("terminal", 14, "#8e8e93").pixmap(14, 14))
        row.addWidget(glyph)
        self.cwd_combo = QComboBox()
        self.cwd_combo.setObjectName("terminalCwdPick")
        self.cwd_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cwd_combo.setMaxVisibleItems(12)
        self.cwd_combo.currentIndexChanged.connect(self._on_cwd_combo)
        row.addWidget(self.cwd_combo)
        self.cwd_label = QLabel()
        self.cwd_label.setObjectName("terminalCwd")
        self.cwd_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.cwd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.cwd_label, stretch=1)
        self._plus_btn = icon_button("fa5s.plus", "New terminal", self._on_new_terminal, size=24)
        row.addWidget(self._plus_btn)
        row.addWidget(
            icon_button(
                "fa5s.times",
                "Close this terminal",
                self.close_current,
                danger=True,
                size=24,
            )
        )
        layout.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._refresh_cwd_label)
        layout.addWidget(self.tabs, stretch=1)

        self._hub_fn = self._on_hub
        get_hub().subscribe(self._hub_fn)
        self._fill_folder_picker()

    def closeEvent(self, event) -> None:  # noqa: N802
        get_hub().unsubscribe(self._hub_fn)
        super().closeEvent(event)

    def sync_workspace(self) -> None:
        """Open or retarget idle tabs after the session workspace is applied."""
        self._fill_folder_picker()
        wanted = self._selected_cwd()
        if self.tabs.count() == 0:
            return
        roots = {path.resolve() for path in terminal_folders()}
        for sid, view in list(self._views.items()):
            session = get_hub().get(sid)
            if session is None or not session.interactive:
                continue
            if session.running or session.command:
                continue
            current = Path(session.cwd).resolve()
            if current in roots:
                continue
            session.cwd = str(wanted)
            view.setPlainText(f"{wanted}> ")
            view.mark_prompt()
            index = self._index_of(sid)
            if index >= 0:
                self.tabs.setTabText(index, _shell_title(wanted)[:32])
                self.tabs.setTabToolTip(index, f"{sid}\n{wanted}")
        self._refresh_cwd_label()

    def _fill_folder_picker(self) -> None:
        folders = terminal_folders()
        self.cwd_combo.blockSignals(True)
        self.cwd_combo.clear()
        if len(folders) > 1:
            keep = str(self._picked_cwd.resolve()) if self._picked_cwd is not None else ""
            for folder in folders:
                self.cwd_combo.addItem(folder.name or str(folder), str(folder))
                self.cwd_combo.setItemData(
                    self.cwd_combo.count() - 1,
                    str(folder),
                    Qt.ItemDataRole.ToolTipRole,
                )
            idx = self.cwd_combo.findData(keep) if keep else -1
            if idx < 0:
                idx = 0
            self.cwd_combo.setCurrentIndex(idx)
            data = self.cwd_combo.currentData()
            if data:
                self._picked_cwd = Path(str(data))
            self.cwd_combo.setVisible(True)
            self.cwd_label.setVisible(False)
        else:
            self.cwd_combo.setVisible(False)
            self.cwd_label.setVisible(True)
        self.cwd_combo.blockSignals(False)
        self._refresh_cwd_label()

    def _on_cwd_combo(self, _index: int = 0) -> None:
        data = self.cwd_combo.currentData()
        if data:
            self._on_cwd_picked(Path(str(data)))

    def _on_cwd_picked(self, cwd: Path) -> None:
        self._picked_cwd = Path(cwd)

    def _selected_cwd(self) -> Path:
        folders = terminal_folders()
        data = self.cwd_combo.currentData() if self.cwd_combo.isVisible() else None
        if data:
            return Path(str(data))
        if self._picked_cwd is not None:
            picked = self._picked_cwd.resolve()
            if picked in {folder.resolve() for folder in folders}:
                return self._picked_cwd
        return folders[0] if folders else current_workspace()

    def _refresh_cwd_label(self, _index: int = 0) -> None:
        sid = self._current_sid()
        session = get_hub().get(sid) if sid else None
        cwd = session.cwd if session is not None else str(self._selected_cwd())
        self.cwd_label.setText(cwd)
        self.cwd_label.setToolTip(cwd)

    def _on_hub(self, event: str, payload: dict[str, Any]) -> None:
        QTimer.singleShot(0, lambda e=event, p=dict(payload): self._apply(e, p))

    def _apply(self, event: str, payload: dict[str, Any]) -> None:
        sid = str(payload.get("sid") or "")
        session = get_hub().get(sid)
        if event == "created":
            self._ensure_tab(sid, str(payload.get("title") or "Terminal"))
            self.session_opened.emit()
        elif event == "chunk":
            view = self._views.get(sid)
            if view is None:
                self._ensure_tab(sid, sid)
                view = self._views.get(sid)
            if view is not None:
                view.moveCursor(QTextCursor.MoveOperation.End)
                view.insertPlainText(str(payload.get("text") or ""))
                view.moveCursor(QTextCursor.MoveOperation.End)
                if (
                    isinstance(view, TerminalView)
                    and not view._busy
                    and session is not None
                    and session.interactive
                    and not session.running
                ):
                    view.mark_prompt()
        elif event == "closed":
            view = self._views.get(sid)
            if isinstance(view, TerminalView):
                view.set_busy(False)
            index = self._index_of(sid)
            if index < 0:
                return
            if session is not None and session.interactive:
                self.tabs.setTabText(index, _shell_title(session.cwd)[:32])
                return
            code = payload.get("exit_code")
            title = self.tabs.tabText(index).split(" · ")[0]
            suffix = " · done" if code in (0, None) else f" · exit {code}"
            self.tabs.setTabText(index, title[:28] + suffix)

    def _ensure_tab(self, sid: str, title: str) -> None:
        if sid in self._views:
            index = self._index_of(sid)
            if index >= 0:
                self.tabs.setCurrentIndex(index)
            return
        session = get_hub().get(sid)
        view = TerminalView()
        view.set_interactive(bool(session and session.interactive))
        view.submitted.connect(lambda cmd, s=sid: self._on_submit(s, cmd))
        view.interrupt.connect(lambda s=sid: get_hub().kill(s))
        font = QFont("Cascadia Mono")
        if not font.exactMatch():
            font = QFont("Consolas")
        font.setPointSize(10)
        view.setFont(font)
        self._views[sid] = view
        index = self.tabs.addTab(view, title[:32])
        tip = f"{sid}\n{session.cwd}" if session is not None else sid
        self.tabs.setTabToolTip(index, tip)
        self.tabs.setCurrentIndex(index)
        self._refresh_cwd_label()

    def _index_of(self, sid: str) -> int:
        for index in range(self.tabs.count()):
            if self.tabs.widget(index) is self._views.get(sid):
                return index
        return -1

    def _current_sid(self) -> str:
        index = self.tabs.currentIndex()
        if index < 0:
            return ""
        view = self.tabs.widget(index)
        for sid, widget in self._views.items():
            if widget is view:
                return sid
        return ""

    def _on_new_terminal(self) -> None:
        self._spawn_shell(self._selected_cwd())

    def _spawn_shell(self, cwd: Path) -> None:
        cwd = Path(cwd).expanduser().resolve()
        session = get_hub().create(
            _shell_title(cwd),
            str(cwd),
            "",
            background=True,
            interactive=True,
        )
        get_hub().write(session.sid, f"{cwd}> ")
        self._ensure_tab(session.sid, _shell_title(cwd))
        view = self._views.get(session.sid)
        if view is not None:
            view.setFocus()

    def _new_blank(self) -> None:
        self._spawn_shell(self._selected_cwd())

    def ensure_session(self) -> None:
        if self.tabs.count() == 0:
            self._new_blank()
        view = self.tabs.currentWidget()
        if view is not None:
            view.setFocus()

    def close_current(self) -> None:
        index = self.tabs.currentIndex()
        if index >= 0:
            self._close_tab(index)

    def _close_tab(self, index: int) -> None:
        view = self.tabs.widget(index)
        sid = ""
        for key, widget in list(self._views.items()):
            if widget is view:
                sid = key
                del self._views[key]
                break
        if sid:
            get_hub().kill(sid)
        self.tabs.removeTab(index)
        if view is not None:
            view.deleteLater()
        self._refresh_cwd_label()
        if self.tabs.count() == 0:
            self.last_closed.emit()

    def _on_submit(self, sid: str, command: str) -> None:
        cmd = command.strip()
        session = get_hub().get(sid)
        view = self._views.get(sid)
        if session is None:
            return
        if not cmd:
            get_hub().write(session.sid, f"{session.cwd}> ")
            return
        if session.running:
            return
        if isinstance(view, TerminalView):
            view.set_busy(True)
        index = self._index_of(sid)
        if index >= 0:
            self.tabs.setTabText(index, _title_for(cmd)[:32])
        from .tools import _command_env

        def work() -> None:
            run_in_session(session, cmd, env=_command_env())

        threading.Thread(target=work, daemon=True).start()
