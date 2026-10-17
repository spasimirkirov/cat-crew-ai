"""Always-on-top acrylic overlay: Jinx as a neon particle cloud."""

from __future__ import annotations

import ctypes
import sys
from typing import Any

from PyQt6.QtCore import QElapsedTimer, QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .body.controller import FIGURE_SHAPES
from .body.render.qpainter import render_particles
from .config import AVATAR_WINDOW_SIZE
from .orb_particles import ParticleField
from .settings_store import audio_settings, character_window, look_settings, save_character_window
from .widgets import HudButton

_VISEME_OPEN = {
    "aa": 0.92,
    "oh": 0.78,
    "ou": 0.58,
    "ee": 0.38,
    "ih": 0.30,
}


def _mouth_open(shape: str | None) -> float:
    if not shape:
        return 0.08
    return _VISEME_OPEN.get(str(shape).lower(), 0.45)


class ParticleCanvas(QWidget):
    """Paints the particle field with additive neon trails."""

    dragged = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(140, 140)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.field = ParticleField(180)
        self.activity = "idle"
        self.caption = "Ready"
        self.muted = False
        self._visemes: list[dict[str, Any]] = []
        self._speech_t = 0.0
        self._drag: QPoint | None = None
        self._buf = QImage()
        self._fade_mask = QColor(0, 0, 0, 175)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._persist_pos)
        self._tick = QTimer(self)
        self._tick.setInterval(33)
        self._tick.timeout.connect(self._step)
        self._tick.start()

    def set_style(self, name: str) -> None:
        self.field.set_style(name)
        self.field.body.apply_style(look_settings())
        self._buf = QImage()
        if str(self.field.style) in FIGURE_SHAPES:
            self._fade_mask = QColor(0, 0, 0, 200)
        else:
            self._fade_mask = QColor(0, 0, 0, 175)

    def set_activity(self, name: str, caption: str | None = None) -> None:
        self.field.set_mode(name)
        self.activity = self.field.mode
        if caption is not None:
            self.caption = caption

    def set_expression(self, emotion: str, duration: float = 1.7) -> None:
        self.field.set_expression(emotion, duration)

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        self.field.set_muted(self.muted)
        self.activity = self.field.mode
        if self.muted:
            self.caption = "Microphone muted"
        elif self.activity == "idle":
            self.caption = "Ready"

    def set_level(self, value: float) -> None:
        self.field.set_level(value)

    def start_speech(self, events: list) -> None:
        self._visemes = [e for e in (events or []) if isinstance(e, dict)]
        self._speech_t = 0.0
        self.set_activity("speaking", "Speaking...")

    def set_speech_time(self, t: float) -> None:
        self._speech_t = max(0.0, float(t))
        self.field.set_viseme(_mouth_open(self.current_viseme()))

    def end_speech(self) -> None:
        self._visemes = []
        self._speech_t = 0.0
        self.field.set_viseme(0.08)
        if not self.muted:
            self.set_expression("happy", 1.15)

    def current_viseme(self) -> str | None:
        shape = None
        for event in self._visemes:
            if float(event.get("time", 0) or 0) <= self._speech_t:
                shape = event.get("shape")
        return shape

    def _step(self) -> None:
        if not self.isVisible():
            return
        ms = self._clock.restart()
        dt = 0.033 if ms <= 0 else min(0.05, max(0.008, ms / 1000.0))
        sleeping = self.activity == "sleeping"
        amp = 0.12 if sleeping else 0.35
        if self.activity == "speaking":
            amp = 0.7
        elif self.activity == "thinking":
            amp = 0.5
        self.field.body.set_heartbeat(64 if sleeping else 72, amp)
        self.field.update(dt)
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._tick.isActive():
            self._tick.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._tick.stop()
        super().hideEvent(event)

    def _persist_pos(self) -> None:
        win = self.window()
        save_character_window({"x": win.x(), "y": win.y()})

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._buf = QImage()

    def paintEvent(self, _event) -> None:  # noqa: N802
        w, h = max(self.width(), 1), max(self.height(), 1)
        if self._buf.isNull() or self._buf.width() != w or self._buf.height() != h:
            self._buf = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            self._buf.fill(0)

        painter = QPainter(self._buf)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.fillRect(0, 0, w, h, self._fade_mask)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        snap = self.field.snapshot()
        muted = self.muted or self.activity == "sleeping"
        render_particles(
            snap,
            painter,
            (w, h),
            muted=muted,
            hologram=float(self.field.body.style.hologram),
        )
        painter.end()

        screen = QPainter(self)
        screen.drawImage(0, 0, self._buf)
        screen.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        win = self.window()
        if self._drag is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            now = event.globalPosition().toPoint()
            delta = now - self._drag
            self._drag = now
            win.move(win.pos() + delta)
            self.dragged.emit()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._save_timer.start()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            toggle = getattr(win, "toggle_hud", None)
            if callable(toggle):
                toggle()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class OverlayHud(QWidget):
    """Double-click the orb to show Call, Mute, and Close. Hidden until then."""

    call_toggled = pyqtSignal(bool)
    mute_toggled = pyqtSignal(bool)
    close_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("overlayHud")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 8)
        row.setSpacing(14)
        row.addStretch(1)
        self.call_btn = HudButton("phone-slash", "Start a call", role="call")
        self.call_btn.clicked.connect(self._flip_call)
        self.mute_btn = HudButton("mic", "Mute microphone", role="mute")
        self.mute_btn.clicked.connect(self._flip_mute)
        self.close_btn = HudButton("close", "Hide overlay", role="close")
        self.close_btn.clicked.connect(self.close_requested)
        row.addWidget(self.call_btn)
        row.addWidget(self.mute_btn)
        row.addWidget(self.close_btn)
        row.addStretch(1)
        self._in_call = False
        self._live = True
        self.call_btn.set_in_call(False)
        self.mute_btn.set_live(True)

    def _flip_call(self, _checked: bool = False) -> None:
        self.set_in_call(not self._in_call)
        self.call_toggled.emit(self._in_call)

    def set_in_call(self, on: bool) -> None:
        self._in_call = bool(on)
        self.call_btn.set_in_call(self._in_call)

    def _flip_mute(self, _checked: bool = False) -> None:
        self.set_live(not self._live)
        self.mute_toggled.emit(not self._live)

    def set_live(self, live: bool) -> None:
        self._live = bool(live)
        self.mute_btn.set_live(self._live)


class AvatarWindow(QWidget):
    """Always-on-top acrylic overlay: particle cloud + controls."""

    mute_toggled = pyqtSignal(bool)
    call_toggled = pyqtSignal(bool)
    hide_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("jinxOverlay")
        self._transparent = False
        self._hide_border = False
        self._hud_open = False
        self.setStyleSheet(
            "#jinxOverlay { background: transparent; }"
            "#overlayHud { background: transparent; }"
            "#hudBtn { background: rgba(255,255,255,0.08); color: #f5f5f7; "
            "border: 1px solid rgba(255,255,255,0.14); border-radius: 22px; padding: 0; }"
            "#hudBtn:hover { background: rgba(255,255,255,0.16); }"
            "#hudBtn[role=\"mute\"][state=\"live\"] { "
            "background: rgba(40, 200, 64, 0.22); border-color: rgba(40, 200, 64, 0.55); }"
            "#hudBtn[role=\"mute\"][state=\"muted\"] { "
            "background: rgba(255, 95, 87, 0.22); border-color: rgba(255, 95, 87, 0.50); }"
            "#hudBtn[role=\"call\"][state=\"live\"] { "
            "background: rgba(40, 200, 64, 0.22); border-color: rgba(40, 200, 64, 0.55); }"
            "#hudBtn[role=\"call\"][state=\"idle\"] { "
            "background: rgba(255, 95, 87, 0.22); border-color: rgba(255, 95, 87, 0.50); }"
            "#hudBtn[role=\"close\"]:hover { "
            "background: rgba(255, 95, 87, 0.88); border-color: #ff5f57; }"
        )

        cfg = character_window()
        self.resize(
            int(cfg.get("width") or AVATAR_WINDOW_SIZE[0]),
            int(cfg.get("height") or AVATAR_WINDOW_SIZE[1]),
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)
        self.orb = ParticleCanvas(self)
        look = look_settings()
        self._transparent = bool(look.get("transparent_bg", False))
        self._hide_border = bool(look.get("hide_border", False))
        self.orb.set_style(str(look.get("orb_style") or "orb"))
        self.status = QLabel("Ready")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            "color: #a5f3fc; font-size: 11px; background: transparent;"
        )
        self.hud = OverlayHud(self)
        self.hud.mute_toggled.connect(self._on_hud_mute)
        self.hud.call_toggled.connect(self._on_hud_call)
        self.hud.close_requested.connect(self._hide_overlay)
        self.status.hide()
        self.hud.hide()
        layout.addWidget(self.orb, stretch=1)
        layout.addWidget(self.status)
        layout.addWidget(self.hud)

        muted = bool(audio_settings().get("mic_muted", False))
        self.set_muted(muted)

        if isinstance(cfg.get("x"), int) and isinstance(cfg.get("y"), int):
            self.move(int(cfg["x"]), int(cfg["y"]))
        else:
            self._move_to_corner()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        outline = QPen(QColor(34, 211, 238, 70), 1.15)
        if self._transparent:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self._hide_border:
                painter.end()
                return
            painter.setPen(outline)
            painter.drawRoundedRect(box, 16, 16)
            painter.end()
            return
        fill = QLinearGradient(box.topLeft(), box.bottomRight())
        fill.setColorAt(0.0, QColor(12, 18, 32, 196))
        fill.setColorAt(0.55, QColor(8, 12, 22, 186))
        fill.setColorAt(1.0, QColor(22, 12, 28, 196))
        painter.setBrush(fill)
        if self._hide_border:
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.setPen(QPen(QColor(34, 211, 238, 100), 1.3))
        painter.drawRoundedRect(box, 16, 16)
        sheen = QLinearGradient(box.topLeft(), box.bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
        sheen.setColorAt(0.18, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(sheen)
        painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 15, 15)
        painter.end()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.clearMask()
        _apply_acrylic(int(self.winId()), enabled=not self._transparent)

    def apply_window_config(self, cfg: dict | None = None) -> None:
        cfg = cfg or character_window()
        self.resize(int(cfg["width"]), int(cfg["height"]))
        if isinstance(cfg.get("x"), int) and isinstance(cfg.get("y"), int):
            self.move(int(cfg["x"]), int(cfg["y"]))
        self.clearMask()

    def apply_look(self, cfg: dict | None = None) -> None:
        cfg = cfg or look_settings()
        self._transparent = bool(cfg.get("transparent_bg", False))
        self._hide_border = bool(cfg.get("hide_border", False))
        self.orb.set_style(str(cfg.get("orb_style") or "orb"))
        self.orb.field.body.apply_style(cfg)
        self.update()
        if self.isVisible():
            _apply_acrylic(int(self.winId()), enabled=not self._transparent)

    def toggle_hud(self) -> None:
        self._hud_open = not self._hud_open
        self.hud.setVisible(self._hud_open)
        self.status.setVisible(self._hud_open)

    def _hide_overlay(self) -> None:
        self.hide()
        self.hide_requested.emit()

    def _move_to_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)

    def avoid_widget(self, widget) -> None:
        if widget is None or not widget.isVisible() or not self.isVisible():
            return
        og = self.frameGeometry()
        cg = widget.frameGeometry()
        if not og.intersects(cg):
            return
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = cg.right() + 16
        if x + self.width() <= area.right():
            self.move(x, max(area.top(), min(og.y(), area.bottom() - self.height())))
            return
        x = cg.left() - self.width() - 16
        self.move(max(area.left(), x), max(area.top(), min(og.y(), area.bottom() - self.height())))

    def _on_hud_mute(self, muted: bool) -> None:
        self.set_muted(muted)
        self.mute_toggled.emit(muted)

    def _on_hud_call(self, on: bool) -> None:
        self.set_in_call(on)
        self.call_toggled.emit(bool(on))

    def set_in_call(self, on: bool) -> None:
        self.hud.set_in_call(bool(on))
        if self.orb.muted:
            return
        if on:
            self.set_activity("listening", "In a call")
        elif self.orb.activity in ("idle", "listening", "muted"):
            self.set_activity("idle", "Ready")

    def set_muted(self, muted: bool) -> None:
        self.orb.set_muted(muted)
        self.hud.set_live(not muted)
        if muted:
            self.status.setText("Microphone muted")
        elif self.hud._in_call:
            self.status.setText("In a call")
        elif self.orb.activity in ("idle", "muted"):
            self.status.setText("Ready")

    def set_activity(self, name: str, caption: str | None = None) -> None:
        self.orb.set_activity(name, caption)
        if caption:
            self.status.setText(caption)

    def set_expression(self, emotion: str, duration: float = 1.7) -> None:
        self.orb.set_expression(emotion, duration)

    def set_level(self, value: float) -> None:
        self.orb.set_level(value)

    def thinking(self) -> None:
        self.set_activity("thinking", "Thinking...")

    def start_speech(self, events: list) -> None:
        self.orb.start_speech(events)
        self.status.setText("Speaking...")

    def set_speech_time(self, t: float) -> None:
        self.orb.set_speech_time(t)

    def end_speech(self) -> None:
        self.orb.end_speech()

    def speak(self, text: str) -> None:
        self.set_activity("speaking", (text or "Speaking...")[:48])

    def play_animation(self, name: str, loop: bool = True) -> None:  # noqa: ARG002
        key = (name or "").lower()
        if "think" in key:
            self.thinking()
            self.orb.field.play("think")
        elif "wave" in key:
            self.orb.field.play("wave", duration=1.8)
            if self.orb.field.style not in FIGURE_SHAPES:
                self.set_expression("happy", 1.2)
            self.set_activity(self.orb.activity if self.orb.activity != "sleeping" else "idle", "Hey.")
        elif "sleep" in key:
            self.set_activity("sleeping", "Sleeping")
        else:
            self.orb.field.play(key)
            self.set_activity("idle", name or "Ready")

    def morph_to(self, name: str, duration: float = 1.5) -> None:
        self.orb.field.morph_to(name, duration)
        self.orb._buf = QImage()

    def set_emotion(self, name: str, value: float) -> None:
        self.orb.field.body.set_emotion(name, value)

    def set_heartbeat(self, bpm: float, intensity: float = 0.35) -> None:
        self.orb.field.body.set_heartbeat(bpm, intensity)

    def speak_audio(self, audio_path: str) -> None:  # noqa: ARG002
        self.set_activity("speaking", "Speaking...")

    def stop(self) -> None:
        self.orb.end_speech()
        if not self.orb.muted:
            self.set_activity("idle", "Ready")


def _apply_acrylic(hwnd: int, *, enabled: bool = True) -> None:
    """Windows acrylic blur behind the overlay; painted tint still shows bounds."""
    if sys.platform != "win32" or not hwnd:
        return

    class AccentPolicy(ctypes.Structure):
        _fields_ = [
            ("AccentState", ctypes.c_int),
            ("AccentFlags", ctypes.c_int),
            ("GradientColor", ctypes.c_uint),
            ("AnimationId", ctypes.c_int),
        ]

    class WinCompAttrData(ctypes.Structure):
        _fields_ = [
            ("Attribute", ctypes.c_int),
            ("Data", ctypes.c_void_p),
            ("SizeOfData", ctypes.c_size_t),
        ]

    accent = AccentPolicy()
    accent.AccentState = 4 if enabled else 0
    accent.AccentFlags = 2
    # ABGR: alpha, blue, green, red -- dark navy glass.
    accent.GradientColor = 0xC814160C if enabled else 0x00000000
    data = WinCompAttrData()
    data.Attribute = 19  # WCA_ACCENT_POLICY
    data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(accent)
    try:
        ctypes.windll.user32.SetWindowCompositionAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.byref(data),
        )
    except Exception:  # noqa: BLE001
        pass
