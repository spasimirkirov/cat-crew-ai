"""Main app: system tray, chat window, lightweight orb overlay."""

from __future__ import annotations

import os
import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .chat_window import ChatWindow
from .config import AVATAR_ENABLED, WINDOW_TITLE
from .icon import create_jinx_icon
from .settings_store import character_window, save_character_window


def shutdown_owned_runtimes() -> None:
    """Stop Jinx-owned GPU children so Exit frees VRAM (llama-server + Comfy)."""
    try:
        from .llama_server import stop_server

        msg = stop_server()
        if msg:
            print(f"[app] {msg}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[app] llama-server stop failed: {exc}", flush=True)
    try:
        from .comfyui import stop_comfyui

        msg = stop_comfyui()
        if msg:
            print(f"[app] {msg}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[app] ComfyUI stop failed: {exc}", flush=True)


class JinxApp:
    """Holds the tray icon and opens the main window."""

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.chat_window: ChatWindow | None = None
        self.avatar = None

        self.tray = QSystemTrayIcon(create_jinx_icon(), self.app)
        self.tray.setToolTip(WINDOW_TITLE)
        self.tray_menu = self._build_menu()
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        print("[app] Tray icon shown.", flush=True)
        self._overlay_scheduled = False

    def _create_avatar(self):
        if not AVATAR_ENABLED:
            return None
        print("[avatar] Creating orb...", flush=True)
        try:
            from .avatar_window import AvatarWindow

            avatar = AvatarWindow()
            avatar.hide_requested.connect(lambda: self.toggle_avatar(False))
            print("[avatar] Orb created.", flush=True)
            return avatar
        except Exception as exc:  # noqa: BLE001
            print(f"[avatar] Failed to start: {exc}", flush=True)
            return None

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        open_action = menu.addAction("Open Jinx")
        open_action.triggered.connect(self.open_chat)

        self.avatar_action = menu.addAction("Show orb")
        self.avatar_action.setCheckable(True)
        self.avatar_action.setChecked(False)
        self.avatar_action.setEnabled(AVATAR_ENABLED)
        self.avatar_action.triggered.connect(self.toggle_avatar)

        menu.addSeparator()

        quit_action = menu.addAction("Exit")
        quit_action.triggered.connect(self.quit)

        return menu

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self.open_chat()

    def open_chat(self) -> None:
        print("[app] Opening window...", flush=True)
        try:
            if self.chat_window is None:
                self.chat_window = ChatWindow(avatar=self.avatar, host=self)
            self.chat_window.showMaximized()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
            print("[app] Window open.", flush=True)
            if not self._overlay_scheduled:
                self._overlay_scheduled = True
                QTimer.singleShot(400, self._maybe_show_overlay)
        except Exception as exc:  # noqa: BLE001
            print(f"[app] Failed to open: {exc}", flush=True)

    def _maybe_show_overlay(self) -> None:
        if AVATAR_ENABLED and character_window().get("visible", True):
            self.toggle_avatar(True)

    def apply_look(self, cfg: dict | None = None) -> None:
        from .settings_store import look_settings

        cfg = cfg or look_settings()
        if self.avatar is not None:
            self.avatar.apply_look(cfg)

    def apply_character_window(self, cfg: dict | None = None) -> None:
        cfg = cfg or character_window()
        if self.avatar is not None:
            self.avatar.apply_window_config(cfg)
        if self.chat_window is not None:
            self.chat_window.sync_character_controls(cfg)

    def toggle_avatar(self, checked: bool) -> None:
        if not AVATAR_ENABLED:
            return
        if checked:
            if self.avatar is None:
                self.avatar = self._create_avatar()
            if self.avatar is not None:
                self.avatar.show()
                self.avatar.raise_()
                if self.chat_window is not None:
                    self.chat_window.bind_avatar(self.avatar)
                    self.chat_window.sync_avatar_checked(True)
                    QTimer.singleShot(0, lambda: self.avatar.avoid_widget(self.chat_window))
                save_character_window({"visible": True})
        else:
            if self.avatar is not None:
                self.avatar.hide()
            if self.chat_window is not None:
                self.chat_window.sync_avatar_checked(False)
            save_character_window({"visible": False})
        visible = bool(checked and self.avatar is not None and self.avatar.isVisible())
        self.avatar_action.setChecked(visible)

    def quit(self) -> None:
        print("[app] Exit.", flush=True)
        if self.chat_window is not None:
            try:
                win = self.chat_window
                if getattr(win, "_status_timer", None) is not None:
                    win._status_timer.stop()
                if getattr(win, "lm_panel", None) is not None:
                    win.lm_panel._timer.stop()
                    persist = getattr(win.lm_panel, "_persist_job", None)
                    if callable(persist):
                        persist()
                if getattr(win, "life", None) is not None:
                    win.life.stop()
                if win.stt is not None:
                    win.stt.stop()
                if win.voice is not None:
                    win.voice.stop()
                win.close()
            except Exception:  # noqa: BLE001
                pass
        shutdown_owned_runtimes()
        if self.avatar is not None:
            try:
                self.avatar.close()
            except Exception:  # noqa: BLE001
                pass
        self.tray.hide()
        QTimer.singleShot(200, self.app.quit)


def _enable_ctrl_c(app: QApplication, on_interrupt) -> QTimer:
    def _handle_sigint(*_args) -> None:
        print("\n[app] Ctrl+C — stopping.", flush=True)
        on_interrupt()

    signal.signal(signal.SIGINT, _handle_sigint)
    heartbeat = QTimer(app)
    heartbeat.setInterval(200)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start()
    return heartbeat


def run(smoke: bool = False, debug_avatar: bool = False) -> int:
    from .single_instance import acquire, focus_existing

    if not acquire():
        print(
            "[app] Jinx is already running. Use the tray or the open window.",
            flush=True,
        )
        focus_existing()
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)
    app.setWindowIcon(create_jinx_icon())
    app.setQuitOnLastWindowClosed(False)
    # Belt: if anything calls app.quit() without JinxApp.quit, still free VRAM.
    app.aboutToQuit.connect(shutdown_owned_runtimes)

    jinx = JinxApp(app)
    heartbeat = _enable_ctrl_c(app, jinx.quit)
    QTimer.singleShot(0, jinx.open_chat)

    if smoke:
        print("[app] smoke-test: quitting in 3s.", flush=True)
        QTimer.singleShot(3000, jinx.quit)

    if debug_avatar:
        from .config import BASE_DIR

        def _open_character() -> None:
            if jinx.chat_window is None:
                QTimer.singleShot(200, _open_character)
                return
            jinx.toggle_avatar(True)
            print("[debug] overlay orb...", flush=True)

            def _grab() -> None:
                view = jinx.avatar
                out = BASE_DIR / "data" / "avatar_debug.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                if view is not None:
                    pix = view.grab()
                    pix.save(str(out))
                    print(f"[debug] screenshot {out} {pix.width()}x{pix.height()}", flush=True)
                else:
                    print("[debug] no orb overlay", flush=True)
                QTimer.singleShot(400, app.quit)

            QTimer.singleShot(1200, _grab)

        QTimer.singleShot(400, _open_character)

    print("[app] Event loop started. Ctrl+C stops the app.", flush=True)
    code = app.exec()
    heartbeat.stop()
    # PyQt6 on Windows can abort in native plugin teardown after exec() returns
    # (STATUS_STACK_BUFFER_OVERRUN). Leave the process here instead.
    os._exit(int(code or 0))
