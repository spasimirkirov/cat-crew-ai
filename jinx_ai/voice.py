"""Female neural TTS for Jinx — English (Edge Jenny, SAPI fallback)."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

from .config import TTS_CACHE_DIR, TTS_VOICE_EN
from .settings_store import audio_settings

_VISEME = {
    "a": "aa", "ã": "aa",
    "e": "ee", "é": "ee",
    "i": "ih", "y": "ih",
    "o": "oh",
    "u": "ou",
    "m": None, "b": None, "p": None,
}


_SPEAK_BLOCK = re.compile(
    r"\[speak\]\s*(.*?)\s*\[/speak\]",
    re.I | re.S,
)
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0000FE00-\U0000FE0F"
    "\U0001F3FB-\U0001F3FF"
    "\u200d"
    "\u2640-\u2642"
    "\u2600-\u2B55"
    "]+",
)


def strip_for_speech(text: str) -> str:
    """Keep chat emojis on screen; never send them to TTS."""
    cleaned = _EMOJI_RE.sub(" ", text or "")
    return " ".join(cleaned.split())


def strip_speak_tags(text: str) -> tuple[str, str]:
    """Remove every ``[speak]…[/speak]`` from chat text.

    Returns ``(chat_without_tags, spoken_from_first_block)``.
    Speak tags must never appear in the chat bubble — even when TTS is off.
    """
    raw = text or ""
    blocks = [
        match.group(1).strip()
        for match in _SPEAK_BLOCK.finditer(raw)
        if match.group(1).strip()
    ]
    spoken = strip_for_speech(" ".join(blocks[0].split())) if blocks else ""
    cleaned = _SPEAK_BLOCK.sub("", raw)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, spoken


def spoken_vs_chat(reply: str, *, tools_used: bool) -> tuple[str, str]:
    """Split a reply into (chat text, spoken text).

    Chat never includes ``[speak]`` tags. TTS uses the speak block when
    present; otherwise normal chat / short tool summary.
    """
    from .activity import visible_chat_reply

    without_tags, tagged_spoken = strip_speak_tags((reply or "").strip())
    display = visible_chat_reply(without_tags, tools_used=tools_used)
    if not (display or "").strip() and tagged_spoken:
        display = tagged_spoken
    if tagged_spoken:
        return display, tagged_spoken
    if not tools_used:
        return display, strip_for_speech(display)
    return display, strip_for_speech(_voice_summary(display))


def _voice_summary(text: str, max_chars: int = 260) -> str:
    cleaned = re.sub(r"```.*?```", " ", text, flags=re.S)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = _SPEAK_BLOCK.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "Done. Details are in the chat."
    parts = re.split(r"(?<=[.!?…])\s+", cleaned)
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        trial = " ".join(out + [part]).strip()
        if out and len(trial) > max_chars:
            break
        out.append(part)
        if len(out) >= 2 or len(trial) >= 140:
            break
    spoken = " ".join(out).strip()
    if len(spoken) > max_chars:
        spoken = spoken[: max_chars - 1].rstrip() + "…"
    return spoken or cleaned[:max_chars]

def _pick_neural_voice(_text: str) -> str:
    return TTS_VOICE_EN


def _letters_to_visemes(word: str) -> list[str | None]:
    out: list[str | None] = []
    prev = object()
    for ch in (word or "").lower():
        if ch in _VISEME:
            shape = _VISEME[ch]
        elif ch.isalpha():
            shape = "aa"
        else:
            continue
        if shape != prev:
            out.append(shape)
            prev = shape
    return out or [None]


def visemes_for_word(word: str, t0: float, duration: float) -> list[dict]:
    shapes = _letters_to_visemes(word)
    dur = max(duration, 0.05)
    step = dur / max(len(shapes), 1)
    return [{"time": t0 + i * step, "shape": sh} for i, sh in enumerate(shapes)]


def visemes_from_text(text: str, duration: float) -> list[dict]:
    words = [w for w in re.split(r"\s+", text or "") if w]
    if not words:
        return [{"time": 0.0, "shape": None}]
    dur = max(duration, 0.4)
    step = dur / len(words)
    events: list[dict] = []
    for i, word in enumerate(words):
        events.extend(visemes_for_word(word, i * step, step * 0.85))
    events.append({"time": dur, "shape": None})
    return events


def _device_by_name(name: str):
    if not name:
        return None
    for device in QMediaDevices.audioOutputs():
        if device.description() == name:
            return device
    return None


class _EdgeTtsWorker(QThread):
    ready = pyqtSignal(str, list)
    failed = pyqtSignal(str)

    def __init__(self, text: str, voice: str, dest: Path) -> None:
        super().__init__()
        self._text = text
        self._voice = voice
        self._dest = dest

    def run(self) -> None:
        try:
            import edge_tts

            async def _synth() -> list[dict]:
                communicate = edge_tts.Communicate(self._text, self._voice)
                audio = b""
                visemes: list[dict] = []
                async for chunk in communicate.stream():
                    kind = chunk.get("type")
                    if kind == "audio":
                        audio += chunk["data"]
                    elif kind == "WordBoundary":
                        t0 = float(chunk.get("offset", 0)) / 10_000_000.0
                        dur = float(chunk.get("duration", 0)) / 10_000_000.0
                        word = str(chunk.get("text") or "")
                        visemes.extend(visemes_for_word(word, t0, dur))
                self._dest.write_bytes(audio)
                if visemes:
                    last = visemes[-1]["time"] + 0.08
                    visemes.append({"time": last, "shape": None})
                return visemes

            visemes = asyncio.run(_synth())
            if not visemes:
                visemes = visemes_from_text(self._text, 2.0)
            self.ready.emit(str(self._dest), visemes)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class JinxVoice(QObject):
    """Speak Jinx replies. Runs on the main Qt thread."""

    speech_started = pyqtSignal(list)
    speech_progress = pyqtSignal(float)
    speech_finished = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._player.setAudioOutput(self._output)
        self._player.positionChanged.connect(self._on_position)
        self._player.mediaStatusChanged.connect(self._on_media)
        self._player.playbackStateChanged.connect(self._on_playback)
        self._player.errorOccurred.connect(self._on_player_error)
        self._worker: _EdgeTtsWorker | None = None
        self._sapi = None
        self._gen = 0
        self._playing = False
        self._pending_visemes: list | None = None
        self._last_progress = 0.0
        self._sapi_t0 = 0.0
        self._sapi_dur = 0.0
        self._sapi_timer = QTimer(self)
        self._sapi_timer.setInterval(50)
        self._sapi_timer.timeout.connect(self._on_sapi_tick)
        self._init_sapi()
        self.apply_audio_settings()
        print("[voice] TTS: Jenny (EN), SAPI fallback", flush=True)

    def _init_sapi(self) -> None:
        try:
            from PyQt6.QtCore import QLocale
            from PyQt6.QtTextToSpeech import QTextToSpeech

            tts = QTextToSpeech()
            voices = list(tts.availableVoices())
            preferred = None
            for voice in voices:
                try:
                    lang = voice.locale().language()
                except Exception:  # noqa: BLE001
                    continue
                name = (voice.name() or "").lower()
                female = any(tag in name for tag in ("zira", "jenny", "aria", "female"))
                if lang == QLocale.Language.English and female:
                    preferred = voice
                    break
                if lang == QLocale.Language.English and preferred is None:
                    preferred = voice
            if preferred is not None:
                tts.setVoice(preferred)
            self._sapi = tts
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] SAPI unavailable: {exc}", flush=True)

    @property
    def available(self) -> bool:
        return True

    def apply_audio_settings(self) -> None:
        cfg = audio_settings()
        self._output.setVolume(cfg["output_volume"] / 100.0)
        device = _device_by_name(cfg["output_device"])
        if device is not None:
            self._output.setDevice(device)
        if self._sapi is not None:
            try:
                self._sapi.setVolume(cfg["output_volume"] / 100.0)
            except Exception:  # noqa: BLE001
                pass

    def speak(self, text: str) -> None:
        if not text:
            return
        clean = strip_for_speech(text)
        if len(clean) > 900:
            clean = clean[:900] + "..."
        self.stop()
        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = TTS_CACHE_DIR / "jinx-speak.mp3"
        voice = _pick_neural_voice(clean)
        self._gen += 1
        token = self._gen
        self._worker = _EdgeTtsWorker(clean, voice, dest)
        self._worker.ready.connect(lambda path, vis, n=token: self._play_file(path, vis, n))
        self._worker.failed.connect(lambda err, n=token: self._sapi_say(clean, err, n))
        self._worker.start()

    def _play_file(self, path: str, visemes: list, token: int) -> None:
        if token != self._gen:
            return
        self.apply_audio_settings()
        self._sapi_timer.stop()
        self._pending_visemes = visemes or []
        self._player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self._player.play()

    def _on_playback(self, state) -> None:
        if state != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._pending_visemes is None:
            return
        visemes = self._pending_visemes
        self._pending_visemes = None
        self._playing = True
        self.speech_started.emit(visemes)

    def _on_position(self, ms: int) -> None:
        if not self._playing:
            return
        now = time.monotonic()
        if now - self._last_progress < 0.04:
            return
        self._last_progress = now
        self.speech_progress.emit(ms / 1000.0)

    def _on_media(self, status) -> None:
        if not self._playing:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._playing = False
            self.speech_finished.emit()

    def _on_player_error(self, *_args) -> None:
        if not self._playing and self._pending_visemes is None:
            return
        print("[voice] TTS playback error.", flush=True)
        self._pending_visemes = None
        self._playing = False
        self.speech_finished.emit()

    def _on_sapi_tick(self) -> None:
        t = time.monotonic() - self._sapi_t0
        self.speech_progress.emit(t)
        if t >= self._sapi_dur:
            self._sapi_timer.stop()
            self._playing = False
            self.speech_finished.emit()

    def _sapi_say(self, text: str, err: str, token: int) -> None:
        if token != self._gen:
            return
        print(f"[voice] Edge TTS failed ({err}) — SAPI", flush=True)
        if self._sapi is None:
            self.speech_finished.emit()
            return
        duration = max(0.8, min(30.0, len(text) * 0.07))
        self._playing = True
        self._sapi_t0 = time.monotonic()
        self._sapi_dur = duration
        self.speech_started.emit(visemes_from_text(text, duration))
        self._sapi_timer.start()
        try:
            self._sapi.stop()
            self._sapi.say(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] SAPI error: {exc}", flush=True)
            self._sapi_timer.stop()
            self._playing = False
            self.speech_finished.emit()

    def stop(self) -> None:
        self._gen += 1
        self._playing = False
        self._pending_visemes = None
        self._sapi_timer.stop()
        try:
            self._player.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._sapi is not None:
            try:
                self._sapi.stop()
            except Exception:  # noqa: BLE001
                pass
