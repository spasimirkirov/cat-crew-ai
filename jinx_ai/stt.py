"""Continuous local STT. Listening starts when the user joins a call."""

from __future__ import annotations

import array
import math
import os
import re
import struct
import threading
import time

from PyQt6.QtCore import QByteArray, QIODevice, QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from .config import WHISPER_DIR, WHISPER_MODEL
from .settings_store import audio_settings

_RATE = 16000
_FRAME_MS = 30
_FRAME_BYTES = int(_RATE * _FRAME_MS / 1000) * 2
_START_FRAMES = 4
_END_SILENCE_MS = 800
_MIN_SPEECH_MS = 450
_MAX_SPEECH_MS = 14000
_PRE_ROLL_MS = 280
_PRE_ROLL_BYTES = int(_RATE * _PRE_ROLL_MS / 1000) * 2

_model = None
_model_error = ""
_model_lock = threading.Lock()


def _clear_hf_offline() -> dict[str, str]:
    saved = {}
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_IMPLICIT_TOKEN"):
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "0"
    return saved


def _restore_env(saved: dict[str, str]) -> None:
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    saved.pop("HF_HUB_OFFLINE", None)
    saved.pop("TRANSFORMERS_OFFLINE", None)
    os.environ.update(saved)


def get_whisper_model():
    global _model, _model_error
    with _model_lock:
        if _model is not None:
            return _model
        saved = _clear_hf_offline()
        try:
            from faster_whisper import WhisperModel

            WHISPER_DIR.mkdir(parents=True, exist_ok=True)
            _model = WhisperModel(
                WHISPER_MODEL,
                device="cpu",
                compute_type="int8",
                download_root=str(WHISPER_DIR),
            )
            _model_error = ""
            return _model
        except Exception as exc:  # noqa: BLE001
            _model_error = str(exc)
            raise
        finally:
            _restore_env(saved)


def _input_device(name: str):
    devices = list(QMediaDevices.audioInputs())
    if name:
        for device in devices:
            if device.description() == name:
                return device
    return QMediaDevices.defaultAudioInput()


def _rms(pcm: bytes) -> float:
    n = len(pcm) // 2
    if n <= 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    return math.sqrt(sum(s * s for s in samples) / n) / 32768.0


def _to_16k_mono(raw: bytes, fmt: QAudioFormat) -> bytes:
    if not raw:
        return b""
    sample_fmt = fmt.sampleFormat()
    channels = max(1, fmt.channelCount())
    rate = fmt.sampleRate() or _RATE
    if sample_fmt == QAudioFormat.SampleFormat.Float:
        n = len(raw) // 4
        floats = struct.unpack(f"<{n}f", raw[: n * 4])
        samples = array.array("h", (max(-32768, min(32767, int(x * 32767))) for x in floats))
    else:
        n = len(raw) // 2
        samples = array.array("h")
        samples.frombytes(raw[: n * 2])
    if channels > 1:
        samples = array.array("h", (samples[i] for i in range(0, len(samples), channels)))
    if rate != _RATE and rate > 0 and samples:
        n_out = max(1, int(round(len(samples) * _RATE / rate)))
        out = array.array("h", [0] * n_out)
        last = len(samples) - 1
        for i in range(n_out):
            src = i * (len(samples) - 1) / max(n_out - 1, 1)
            j = int(src)
            f = src - j
            a = samples[min(j, last)]
            b = samples[min(j + 1, last)]
            out[i] = int(a + (b - a) * f)
        samples = out
    return samples.tobytes()


def _join_segments(segments) -> str:
    return " ".join(seg.text.strip() for seg in segments if getattr(seg, "text", "")).strip()


def _transcribe(model, audio) -> tuple[str, object]:
    kwargs = {
        "language": "en",
        "vad_filter": True,
        "beam_size": 5,
        "without_timestamps": True,
        "condition_on_previous_text": False,
        "initial_prompt": "Spoken English, task for a desktop assistant.",
    }
    segments, info = model.transcribe(audio, **kwargs)
    return _join_segments(segments), info


_HALLUCINATION = re.compile(
    r"^\s*(how are you( doing)?[!.?]*(\s*good (morning|afternoon|evening)[!.?]*)?"
    r"|good (morning|afternoon|evening)[!.?]*"
    r"|thanks for watching"
    r"|thank you for watching"
    r"|subscribe( to (my|the) channel)?"
    r"|i('m| am) (an? )?(ai|assistant|language model)"
    r")\s*$",
    re.I,
)


def looks_like_whisper_hallucination(text: str) -> bool:
    """Whisper often invents greetings when the language guess is wrong."""
    raw = " ".join((text or "").split())
    if not raw:
        return False
    if _HALLUCINATION.match(raw):
        return True
    lower = raw.lower()
    return lower in {
        "how are you?",
        "how are you doing?",
        "how are you doing? good morning.",
        "good morning.",
        "good morning",
        "hello.",
        "hello",
    }


def transcribe_utterance(audio, prefer: str | None = None) -> str:
    """English-only Whisper. Empty or hallucinated transcripts are dropped."""
    del prefer
    model = get_whisper_model()
    text, _info = _transcribe(model, audio)
    text = _fix_names(" ".join((text or "").split()))
    if not text:
        return ""
    if looks_like_whisper_hallucination(text):
        print(f"[stt] ignored hallucination: {text!r}", flush=True)
        return ""
    print(f"[stt] {text!r}", flush=True)
    return text


def _fix_names(text: str) -> str:
    cleaned = re.sub(r"\bJinks\b", "Jinx", text or "", flags=re.I)
    cleaned = re.sub(r"\bJy?nx\b", "Jinx", cleaned, flags=re.I)
    return cleaned


class _WarmupWorker(QThread):
    ready = pyqtSignal()
    failed = pyqtSignal(str)

    def run(self) -> None:
        try:
            get_whisper_model()
            self.ready.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _TranscribeWorker(QThread):
    finished_text = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, pcm: bytes) -> None:
        super().__init__()
        self._pcm = pcm

    def run(self) -> None:
        try:
            import numpy as np

            audio = np.frombuffer(self._pcm, dtype=np.int16).astype(np.float32) / 32768.0
            text = transcribe_utterance(audio)
            self.finished_text.emit(text)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class SpeechToText(QObject):
    """Mic is live only while a call is on. Leave the call to hang up."""

    transcribed = pyqtSignal(str)
    failed = pyqtSignal(str)
    recording_changed = pyqtSignal(bool)
    status = pyqtSignal(str)
    wake_heard = pyqtSignal()
    slept = pyqtSignal(str)
    call_changed = pyqtSignal(bool)
    muted_changed = pyqtSignal(bool)
    level = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._source: QAudioSource | None = None
        self._io: QIODevice | None = None
        self._fmt: QAudioFormat | None = None
        self._listening = False
        self._paused = False
        self._speaking = False
        self._raw = QByteArray()
        self._pcm = bytearray()
        self._pre = bytearray()
        self._speech = bytearray()
        self._loud_frames = 0
        self._silent_ms = 0
        self._speech_ms = 0
        self._noise = 0.01
        self._worker: _TranscribeWorker | None = None
        self._warmup: _WarmupWorker | None = None
        self._model_ready = False
        self._tick = QTimer(self)
        self._tick.setInterval(40)
        self._tick.timeout.connect(self._pump)
        self._in_call = False
        self._armed = False
        self._level_skip = 0
        self._echo_until = 0.0
        self._warmup = _WarmupWorker()
        self._warmup.ready.connect(self._on_model_ready)
        self._warmup.failed.connect(self._on_model_failed)
        self._warmup.start()

    @property
    def recording(self) -> bool:
        return self._listening

    @property
    def listening(self) -> bool:
        return self._listening

    @property
    def awake(self) -> bool:
        return self._in_call and self._armed

    @property
    def in_call(self) -> bool:
        return self._in_call

    def _idle_status(self) -> str:
        if not self._listening:
            return "Ready"
        if self._paused:
            return "Waiting for reply…"
        if self._in_call:
            return "In a call"
        return "Ready"

    def toggle(self) -> None:
        if self._in_call:
            self.leave_call()
        else:
            self.join_call()

    def join_call(self) -> None:
        if self._in_call and self._listening:
            return
        self._in_call = True
        self._armed = True
        if not self._listening:
            self.start()
        if not self._listening:
            self._in_call = False
            self._armed = False
            return
        muted = bool(audio_settings().get("mic_muted", False))
        if muted:
            self.set_paused(True)
            self.status.emit("In a call (muted)")
        else:
            self.status.emit("In a call")
        print("[stt] In a call.", flush=True)
        self.call_changed.emit(True)
        self.wake_heard.emit()

    def leave_call(self) -> None:
        was = self._in_call
        self._in_call = False
        self._armed = False
        if self._listening:
            self.stop()
        else:
            self.status.emit("Ready")
        if was:
            print("[stt] Left the call.", flush=True)
            self.call_changed.emit(False)
            self.slept.emit("hangup")

    def set_muted(self, muted: bool) -> None:
        if muted:
            if self._in_call:
                self.set_paused(True)
                self.status.emit("In a call (muted)")
            elif self._listening:
                self.stop()
            self.muted_changed.emit(True)
            return
        if self._in_call:
            self.set_paused(False)
            self.status.emit("In a call")
        self.muted_changed.emit(False)

    def start(self) -> None:
        if self._listening:
            return
        self._listening = True
        self._paused = False
        self.recording_changed.emit(True)
        self.status.emit(self._idle_status())
        print("[stt] Microphone on.", flush=True)
        self._open_mic()
        self._tick.start()

    def stop(self) -> None:
        self._listening = False
        self._paused = False
        self._tick.stop()
        self._close_mic()
        self._reset_vad()
        self.recording_changed.emit(False)
        self.status.emit("Ready")
        print("[stt] Microphone off.", flush=True)

    def set_paused(self, paused: bool) -> None:
        if not self._listening:
            return
        self._paused = bool(paused)
        if self._paused:
            self._close_mic()
            self._reset_vad()
            self.status.emit("Waiting for reply…")
        else:
            self.status.emit(self._idle_status())
            self._echo_until = time.monotonic() + 1.6
            self._open_mic()

    def _on_model_ready(self) -> None:
        self._model_ready = True
        print("[stt] Whisper is ready.", flush=True)
        if self._listening and not self._paused:
            self.status.emit(self._idle_status())

    def _on_model_failed(self, err: str) -> None:
        print(f"[stt] Whisper failed to load: {err}", flush=True)
        if not self._listening:
            return
        self.stop()
        self.failed.emit("Whisper failed to load. Check the network for the first download.")

    def _open_mic(self) -> None:
        if self._source is not None:
            return
        device = _input_device(audio_settings()["input_device"])
        if device is None or device.isNull():
            self.failed.emit("No microphone.")
            self.stop()
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        self._fmt = fmt
        self._raw = QByteArray()
        self._source = QAudioSource(device, fmt, self)
        self._io = self._source.start()
        if self._io is None:
            self.failed.emit("Microphone did not start.")
            self.stop()
            return
        self._io.readyRead.connect(self._on_audio)

    def _close_mic(self) -> None:
        if self._io is not None:
            try:
                self._io.readyRead.disconnect(self._on_audio)
            except TypeError:
                pass
        if self._source is not None:
            self._source.stop()
            self._source = None
        self._io = None
        self._raw = QByteArray()

    def _on_audio(self) -> None:
        if self._io is None or self._paused:
            return
        self._raw.append(self._io.readAll())

    def _reset_vad(self) -> None:
        self._pcm = bytearray()
        self._pre = bytearray()
        self._speech = bytearray()
        self._speaking = False
        self._loud_frames = 0
        self._silent_ms = 0
        self._speech_ms = 0

    def _pump(self) -> None:
        if not self._listening or self._paused or self._fmt is None:
            return
        if self._io is not None:
            extra = self._io.readAll()
            if extra:
                self._raw.append(extra)
        raw = bytes(self._raw)
        self._raw = QByteArray()
        if raw:
            self._pcm.extend(_to_16k_mono(raw, self._fmt))
        while len(self._pcm) >= _FRAME_BYTES:
            frame = bytes(self._pcm[:_FRAME_BYTES])
            del self._pcm[:_FRAME_BYTES]
            self._on_frame(frame)

    def _on_frame(self, frame: bytes) -> None:
        rms = _rms(frame)
        self._level_skip += 1
        if self._level_skip >= 4:
            self._level_skip = 0
            self.level.emit(min(1.0, rms * 9.0))
        if not self._speaking:
            self._noise = self._noise * 0.95 + rms * 0.05
        threshold = max(0.018, self._noise * 3.4)
        if rms >= threshold:
            self._loud_frames += 1
            self._silent_ms = 0
        else:
            self._loud_frames = 0
            self._silent_ms += _FRAME_MS

        if not self._speaking:
            self._pre.extend(frame)
            if len(self._pre) > _PRE_ROLL_BYTES:
                del self._pre[: len(self._pre) - _PRE_ROLL_BYTES]
            if self._loud_frames >= _START_FRAMES:
                self._speaking = True
                self._speech = bytearray(self._pre)
                self._speech_ms = int(len(self._speech) / 2 / _RATE * 1000)
                self._pre.clear()
                self.status.emit("Hearing you…")
            return

        self._speech.extend(frame)
        self._speech_ms += _FRAME_MS
        if self._silent_ms >= _END_SILENCE_MS or self._speech_ms >= _MAX_SPEECH_MS:
            pcm = bytes(self._speech)
            self._speaking = False
            self._speech.clear()
            self._pre.clear()
            self._loud_frames = 0
            self._silent_ms = 0
            duration = len(pcm) / 2 / _RATE * 1000
            if duration < _MIN_SPEECH_MS:
                self.status.emit(self._idle_status())
                return
            self._submit(pcm)

    def _submit(self, pcm: bytes) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._model_ready and _model is None:
            self.status.emit("Loading Whisper…")
        else:
            self.status.emit("Transcribing…")
        print("[stt] Utterance → Whisper", flush=True)
        self._worker = _TranscribeWorker(pcm)
        self._worker.finished_text.connect(self._on_text)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_text(self, text: str) -> None:
        if not self._listening:
            return
        clean = " ".join((text or "").split())
        if len(clean) < 2:
            if not self._paused:
                self.status.emit(self._idle_status())
            return
        if time.monotonic() < self._echo_until:
            print(f"[stt] ignored (echo): {clean!r}", flush=True)
            if not self._paused:
                self.status.emit(self._idle_status())
            return
        if not self._in_call:
            print(f"[stt] ignored (not in a call): {clean!r}", flush=True)
            if not self._paused:
                self.status.emit(self._idle_status())
            return
        print(f"[stt] command: {clean!r}", flush=True)
        if not self._paused:
            self.status.emit("In a call")
        self.transcribed.emit(clean)

    def _on_fail(self, err: str) -> None:
        print(f"[stt] Error: {err}", flush=True)
        self.failed.emit("Could not recognize speech.")
        if self._listening and not self._paused:
            self.status.emit(self._idle_status())
