"""Qt heartbeat: body ticks, rare inner thoughts, evening wrap-up."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from .here import clock_now
from .life import (
    decide,
    default_last_user,
    load_state,
    log_event,
    save_wrap,
    snapshot_line,
    thought_prompt,
    wrapup_prompt,
)
from .providers.base import BaseProvider
from .settings_store import life_settings


class LifeWorker(QThread):
    finished = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, provider: BaseProvider, kind: str, prompt: str) -> None:
        super().__init__()
        self.provider = provider
        self.kind = kind
        self.prompt = prompt

    def run(self) -> None:
        try:
            reply = self.provider.chat(
                [
                    {
                        "role": "system",
                        "content": "You are Jinx. Follow the user instructions exactly.",
                    },
                    {"role": "user", "content": self.prompt[:12000]},
                ],
                tools=None,
            )
            text = (reply.content or "").strip()
            self.finished.emit(self.kind, text)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class Heartbeat(QObject):
    status = pyqtSignal(str)
    wrap_ready = pyqtSignal(str)
    slept = pyqtSignal()
    woke = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._worker: LifeWorker | None = None
        self._busy = False
        self._mode = "developer"
        self._overlay = "idle"
        self._local: bool | None = None
        self._comfy: bool | None = None
        self._provider_fn = None

    def configure(self, provider_fn) -> None:
        self._provider_fn = provider_fn

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)

    def set_mode(self, mode: str) -> None:
        self._mode = str(mode or "developer")

    def set_overlay(self, name: str) -> None:
        self._overlay = str(name or "idle")

    def set_hosts(self, local: bool | None, comfy: bool | None) -> None:
        self._local = local
        self._comfy = comfy

    def start(self) -> None:
        cfg = life_settings()
        state = load_state()
        if not state.get("last_user"):
            default_last_user()
        if not cfg["enabled"]:
            self._timer.stop()
            return
        ms = int(cfg["interval_sec"]) * 1000
        if self._timer.interval() != ms or not self._timer.isActive():
            self._timer.start(ms)
        if state.get("phase") != "sleeping" and not state.get("last_tick"):
            log_event("start", "Alive on the desktop")
            self.status.emit("Alive")

    def stop(self) -> None:
        self._timer.stop()

    def restart(self) -> None:
        self._timer.stop()
        self.start()

    def note(self, kind: str, note: str = "") -> None:
        if not life_settings()["enabled"]:
            return
        log_event(kind, note)

    def touch_user(self) -> None:
        from .life import mark_user, load_state as _load

        was = _load().get("phase")
        mark_user()
        if was == "sleeping":
            self.woke.emit("User is here")
            self.status.emit("Awake")

    def snapshot(self) -> str:
        return snapshot_line(
            mode=self._mode,
            busy=self._busy,
            overlay=self._overlay,
            local=self._local,
            comfyui=self._comfy,
        )

    def _tick(self) -> None:
        cfg = life_settings()
        if not cfg["enabled"]:
            self._timer.stop()
            return
        if self._worker is not None and self._worker.isRunning():
            return
        now = clock_now()
        state = load_state()
        action = decide(
            now,
            enabled=True,
            phase=str(state.get("phase") or "awake"),
            day=str(state.get("day") or ""),
            last_user=str(state.get("last_user") or ""),
            last_wrapup_day=str(state.get("last_wrapup_day") or ""),
            busy=self._busy,
            sleep_hour=int(cfg["sleep_hour"]),
            wake_hour=int(cfg["wake_hour"]),
            idle_sleep_min=int(cfg["idle_sleep_min"]),
            mind_every=int(cfg["mind_every"]),
            ticks=int(state.get("ticks") or 0),
        )
        snap = self.snapshot()
        if action == "skip_busy":
            return
        if action == "skip_sleep":
            return
        if action == "wake":
            log_event("wake", "Morning — new day")
            log_event("heartbeat", snap)
            self.woke.emit("Morning")
            self.status.emit("Awake")
            return
        if action == "log":
            log_event("heartbeat", snap)
            return
        if action in {"wrap", "thought"}:
            self._run_mind(action, snap)

    def _run_mind(self, kind: str, snap: str) -> None:
        if self._provider_fn is None:
            if kind == "wrap":
                save_wrap(load_state().get("day") or "", "Quiet day. No model was free to wrap up.")
                self.slept.emit()
                self.status.emit("Sleeping")
            else:
                log_event("heartbeat", snap)
            return
        state = load_state()
        day = str(state.get("day") or "")
        if kind == "wrap":
            prompt = wrapup_prompt(day)
            log_event("heartbeat", snap)
        else:
            log_event("heartbeat", snap)
            prompt = thought_prompt(snap, day)
        try:
            provider = self._provider_fn()
        except Exception as exc:  # noqa: BLE001
            self.status.emit(str(exc)[:80])
            if kind == "wrap":
                save_wrap(day, "Wrap-up skipped — model unavailable.")
                self.slept.emit()
            return
        worker = LifeWorker(provider, kind, prompt)
        self._worker = worker
        worker.finished.connect(self._on_mind)
        worker.failed.connect(lambda err, k=kind: self._on_mind_fail(k, err))
        worker.finished.connect(lambda *_: self._clear_worker(worker))
        worker.failed.connect(lambda *_: self._clear_worker(worker))
        worker.start()
        self.status.emit("Wrapping up the day…" if kind == "wrap" else "Heartbeat")

    def _on_mind(self, kind: str, text: str) -> None:
        blob = (text or "").strip()
        if kind == "thought":
            if blob and blob.upper() != "HEARTBEAT_OK":
                log_event("thought", blob)
            return
        save_wrap(load_state().get("day") or "", blob or "Quiet day.")
        self.wrap_ready.emit(blob)
        self.slept.emit()
        self.status.emit("Sleeping")

    def _on_mind_fail(self, kind: str, error: str) -> None:
        self.status.emit(str(error)[:80])
        if kind == "wrap":
            save_wrap(load_state().get("day") or "", "Wrap-up failed. Going to sleep anyway.")
            self.slept.emit()

    def _clear_worker(self, worker: LifeWorker) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
