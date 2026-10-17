"""Keep the terminal readable: drop third-party spam, keep Jinx logs."""

from __future__ import annotations

import logging
import os
import sys
import warnings

_DROP = (
    "failed to load spacy",
    "fastembed not installed",
    "loading weights:",
    "estimating duration from bitrate",
    "input #0, mp3",
    "[mp3 @",
    "[h264_mf @",
    "[hevc_mf @",
    "mft name:",
    "get_sentence_embedding_dimension",
    "using qt multimedia with ffmpeg",
    "llm extraction failed",
)


class _QuietStream:
    def __init__(self, inner) -> None:
        self._inner = inner
        self._buf = ""

    def write(self, data) -> int:
        if not isinstance(data, str):
            data = str(data)
        if not data:
            return 0
        if "\r" in data and ("it/s" in data or "%" in data):
            return len(data)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip() and _keep(line):
                self._inner.write(line + "\n")
        return len(data)

    def flush(self) -> None:
        leftover = self._buf
        self._buf = ""
        if leftover.strip() and _keep(leftover):
            self._inner.write(leftover)
        try:
            self._inner.flush()
        except Exception:
            pass

    def reconfigure(self, **kwargs):
        fn = getattr(self._inner, "reconfigure", None)
        if callable(fn):
            return fn(**kwargs)
        return None

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _keep(line: str) -> bool:
    low = line.lower()
    return not any(token in low for token in _DROP)


def install() -> None:
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault(
        "QT_LOGGING_RULES",
        "qt.multimedia.ffmpeg=false;qt.multimedia.ffmpeg.*=false",
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*get_sentence_embedding_dimension.*",
    )
    warnings.filterwarnings("ignore", category=FutureWarning, module=r"mem0\..*")
    logging.getLogger("mem0").setLevel(logging.CRITICAL)
    if not isinstance(sys.stdout, _QuietStream):
        sys.stdout = _QuietStream(sys.stdout)  # type: ignore[assignment]
    if not isinstance(sys.stderr, _QuietStream):
        sys.stderr = _QuietStream(sys.stderr)  # type: ignore[assignment]
