"""ChatGPT-style transcript: user right, Jinx left, labels, timestamp footer."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QFont, QPixmap, QTextDocument, QWheelEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .activity import format_work_duration, repair_utf8_text
from .icon import create_activity_icon
from .widgets import FlowLayout

_DUP_LINK = re.compile(r"\[(https?://[^\s\]]+)\]\(\1/?\)", re.I)
_MD_LINK = re.compile(r"\[[^\]]+\]\([^)]+\)")
_BARE_URL = re.compile(r"https?://[^\s<>)\]]+", re.I)
_FENCE = re.compile(r"```.*?```", re.S)
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _label_link(url: str) -> str:
    host = urlparse(url).netloc or url
    host = host.removeprefix("www.")
    return f"[{host}]({url})"


def _linkify(chunk: str) -> str:
    chunk = _DUP_LINK.sub(lambda m: _label_link(m.group(1)), chunk)
    out: list[str] = []
    pos = 0
    for match in _MD_LINK.finditer(chunk):
        out.append(_BARE_URL.sub(lambda m: _label_link(m.group(0)), chunk[pos:match.start()]))
        out.append(match.group(0))
        pos = match.end()
    out.append(_BARE_URL.sub(lambda m: _label_link(m.group(0)), chunk[pos:]))
    return "".join(out)


def extract_images(text: str) -> tuple[str, list[Path]]:
    """Pull markdown images out so the bubble can show them as photos."""
    paths: list[Path] = []

    def keep(match: re.Match) -> str:
        raw = (match.group(2) or "").strip().strip("\"'")
        if raw.startswith("file:"):
            parsed = urlparse(raw)
            raw = unquote(parsed.path or "")
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                raw = f"//{parsed.netloc}{raw}"
        if len(raw) >= 3 and raw[0] in "/\\" and raw[2] == ":":
            raw = raw[1:]
        path = Path(raw)
        if path.is_file():
            paths.append(path)
        return ""

    cleaned = _MD_IMAGE.sub(keep, text or "")
    return cleaned.strip(), paths


def prepare_markdown(text: str) -> str:
    """Make duplicate [url](url) links readable; autolink leftover URLs."""
    raw = repair_utf8_text(text or "")
    parts: list[str] = []
    pos = 0
    for match in _FENCE.finditer(raw):
        parts.append(_linkify(raw[pos:match.start()]))
        parts.append(match.group(0))
        pos = match.end()
    parts.append(_linkify(raw[pos:]))
    return "".join(parts)


_CARD_PAD_X = 28
_CARD_MIN_INNER = 72
_UNSET = object()


def parse_when(raw: object) -> datetime | None:
    """ISO stamp from the session file → local time for the bubble clock."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone()


def bubble_cap(viewport_width: int) -> int:
    """Chat cards cap at Bootstrap col-6 (half the transcript)."""
    return max(220, int(max(0, viewport_width) * 0.5))


def measure_card_text(
    text: str,
    inner_cap: int,
    *,
    markdown: bool = True,
    font: QFont | None = None,
) -> tuple[int, int]:
    """Return inner (width, height) so the card hugs its text."""
    inner_cap = max(_CARD_MIN_INNER, inner_cap)
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    if font is not None:
        doc.setDefaultFont(_point_font(font))
    raw = text or ""
    if markdown:
        doc.setMarkdown(prepare_markdown(raw))
    else:
        doc.setPlainText(raw)
    doc.setTextWidth(-1)
    natural = max(_CARD_MIN_INNER, int(doc.idealWidth()) + 2)
    inner_w = min(inner_cap, natural)
    doc.setTextWidth(inner_w)
    inner_h = max(16, int(doc.size().height()) + 2)
    return inner_w, inner_h


def markdown_to_html(text: str) -> str:
    doc = QTextDocument()
    doc.setMarkdown(prepare_markdown(text or ""))
    return doc.toHtml()


def _point_font(font: QFont) -> QFont:
    """QTextDocument markdown calls setPointSize; QSS px fonts report -1."""
    copy = QFont(font)
    if copy.pointSize() > 0:
        return copy
    px = copy.pixelSize()
    if px > 0:
        copy.setPointSize(max(9, round(px * 72 / 96)))
    else:
        copy.setPointSize(13)
    return copy


class _BubbleBody(QLabel):
    """Growing text + links. No inner scrollbar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("bubbleBody")
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setFont(_point_font(self.font()))

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()


class ActivityCard(QFrame):
    """Transcript disclosure: 'Worked for 10m 32s' + chevron, not a status card."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("activityCard")
        self.wrap: QWidget | None = None
        self._title = "Working"
        self._detail = ""
        self._tick = 0
        self._live = True
        self._open = False
        self._elapsed_s = 0.0
        self._steps: list[dict[str, str]] = []
        self._step_open = -1
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.title = QLabel("Working…")
        self.title.setObjectName("activityTitle")
        self.chevron = QPushButton()
        self.chevron.setObjectName("activityChevron")
        self.chevron.setFlat(True)
        self.chevron.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chevron.setFixedSize(22, 22)
        self.chevron.setIconSize(QSize(12, 12))
        self.chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chevron.clicked.connect(self._toggle)

        self.steps_host = QWidget()
        self.steps_col = QVBoxLayout(self.steps_host)
        self.steps_col.setContentsMargins(2, 4, 8, 0)
        self.steps_col.setSpacing(1)
        self.steps_host.setVisible(False)

        self.preview = QLabel("")
        self.preview.setObjectName("muted")
        self.preview.setWordWrap(True)
        self.preview.setVisible(False)
        self.preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        col = QVBoxLayout(self)
        col.setContentsMargins(4, 2, 4, 4)
        col.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.title, stretch=1)
        row.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignRight)
        col.addLayout(row)
        col.addWidget(self.steps_host)
        col.addWidget(self.preview)
        self._paint_chevron()

        self._pulse = QTimer(self)
        self._pulse.setInterval(480)
        self._pulse.timeout.connect(self._pulse_tick)
        self._pulse.start()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child is not None and (
                child is self.chevron
                or self.chevron.isAncestorOf(child)
                or child is self.preview
                or self.preview.isAncestorOf(child)
                or (
                    self._open
                    and (child is self.steps_host or self.steps_host.isAncestorOf(child))
                )
            ):
                super().mousePressEvent(event)
                return
            self._toggle()
            event.accept()
            return
        super().mousePressEvent(event)

    def _toggle(self) -> None:
        self._open = not self._open
        self._paint_chevron()
        self._rebuild_steps()

    def _paint_chevron(self) -> None:
        kind = "chevron-up" if self._open else "chevron-down"
        self.chevron.setIcon(create_activity_icon(kind, 12, "#8e8e93"))
        self.chevron.setToolTip("Hide what ran" if self._open else "Show what ran")

    def _header_text(self) -> str:
        if self._live:
            line = self._title or "Working"
            if self._detail:
                line = f"{line} — {self._detail}"
            return line
        return f"Worked for {format_work_duration(self._elapsed_s)}"

    def set_status(self, title: str, detail: str = "") -> None:
        self._title = title or "Working"
        self._detail = (detail or "").strip()
        line = self._title
        if self._detail:
            line = f"{self._title} — {self._detail}"
        if not self._steps or self._steps[-1].get("line") != line:
            self._steps.append({"line": line, "preview": ""})
            self._steps = self._steps[-24:]
            if self._open:
                self._rebuild_steps()
        self._tick = 0
        self._pulse_tick()

    def set_tool_preview(self, name: str, args_preview: str = "", result_preview: str = "") -> None:
        blob = ""
        if args_preview:
            blob += f"args: {args_preview.strip()[:400]}"
        if result_preview:
            if blob:
                blob += "\n"
            blob += f"result: {result_preview.strip()[:600]}"
        if not blob:
            return
        needle = (name or "").replace("_", " ")
        for item in reversed(self._steps):
            if needle and needle.lower() in item.get("line", "").lower():
                item["preview"] = blob
                break
        else:
            self._steps.append({"line": name or "tool", "preview": blob})
            self._steps = self._steps[-24:]
        if self._open:
            self._rebuild_steps()

    def finish(self, elapsed_s: float) -> dict:
        self._live = False
        self._elapsed_s = max(0.0, float(elapsed_s or 0))
        self._pulse.stop()
        self.title.setText(self._header_text())
        self._open = False
        self._paint_chevron()
        self._rebuild_steps()
        return self.snapshot()

    def restore(self, data: dict) -> None:
        self._live = False
        self._pulse.stop()
        self._elapsed_s = float(data.get("elapsed_s") or 0)
        steps = data.get("steps") or []
        self._steps = []
        for item in steps:
            if isinstance(item, dict) and item.get("line"):
                self._steps.append(
                    {
                        "line": str(item.get("line") or ""),
                        "preview": str(item.get("preview") or ""),
                    }
                )
        self._open = False
        self.title.setText(self._header_text())
        self._paint_chevron()
        self._rebuild_steps()

    def snapshot(self) -> dict:
        return {
            "elapsed_s": round(float(self._elapsed_s), 1),
            "steps": [
                {"line": item.get("line", ""), "preview": item.get("preview", "")}
                for item in self._steps
                if item.get("line")
            ],
        }

    def has_steps(self) -> bool:
        return any(item.get("line") for item in self._steps)

    def _rebuild_steps(self) -> None:
        while self.steps_col.count():
            item = self.steps_col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._open:
            self.steps_host.setVisible(False)
            self.preview.setVisible(False)
            return
        for index, item in enumerate(self._steps):
            btn = QPushButton(item.get("line", ""))
            btn.setObjectName("activityStep")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFlat(True)
            btn.clicked.connect(lambda _=False, i=index: self._toggle_step(i))
            self.steps_col.addWidget(btn)
        self.steps_host.setVisible(bool(self._steps))
        self._show_step_preview()

    def _toggle_step(self, index: int) -> None:
        if index < 0 or index >= len(self._steps):
            return
        self._step_open = -1 if self._step_open == index else index
        self._show_step_preview()

    def _show_step_preview(self) -> None:
        if not self._open or self._step_open < 0 or self._step_open >= len(self._steps):
            self.preview.setVisible(False)
            return
        text = (self._steps[self._step_open].get("preview") or "").strip()
        self.preview.setText(text or "(no details captured)")
        self.preview.setVisible(True)

    def _pulse_tick(self) -> None:
        if not self._live:
            return
        self._tick = (self._tick + 1) % 4
        dots = "." * (self._tick or 1)
        self.title.setText(f"{self._header_text().rstrip('.')}{dots}")

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()


class ChatBubble(QFrame):
    def __init__(
        self,
        role: str,
        text: str,
        when: datetime | None = None,
        parent=None,
        *,
        speaker: str = "",
    ) -> None:
        super().__init__(parent)
        self.role = role
        self.wrap: QWidget | None = None
        self.when = when
        self._raw = text or ""
        self._markdown = True
        if role == "user":
            self.setObjectName("userBubble")
            who = "You"
        elif role == "assistant":
            self.setObjectName("jinxBubble")
            who = (speaker or "").strip() or "Jinx AI"
        else:
            self.setObjectName("sysBubble")
            who = "System"
        self._cap = bubble_cap(520)
        self._fitting = False
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)

        align = Qt.AlignmentFlag.AlignRight if role == "user" else Qt.AlignmentFlag.AlignLeft
        self.tag = QLabel(who)
        self.tag.setObjectName("bubbleTag")
        self.tag.setAlignment(align)

        self.body = _BubbleBody()

        self.stamp = QLabel("")
        self.stamp.setObjectName("bubbleTime")
        self.stamp.setAlignment(align)
        self._paint_stamp()

        col = QVBoxLayout(self)
        col.setContentsMargins(14, 10, 14, 8)
        col.setSpacing(4)
        col.addWidget(self.tag)
        col.addWidget(self.body)
        self.photos = QWidget()
        self.photos.setObjectName("bubblePhotos")
        self.photos.setVisible(False)
        self.photos_layout = FlowLayout(self.photos, spacing=8)
        col.addWidget(self.photos)
        col.addWidget(self.stamp)
        self._body_text = ""
        self.set_text(text, markdown=True)

    def _paint_stamp(self) -> None:
        when = self.when
        if when is None:
            self.stamp.clear()
            self.stamp.setToolTip("")
            self.stamp.hide()
            return
        local = when.astimezone() if when.tzinfo else when
        self.stamp.setText(local.strftime("%H:%M"))
        self.stamp.setToolTip(local.strftime("%H:%M:%S  %d %b %Y"))
        self.stamp.show()

    def set_text(self, text: str, *, markdown: bool = True, relayout: bool = True) -> None:
        self._raw = repair_utf8_text(text or "")
        self._markdown = markdown
        body, images = extract_images(self._raw) if markdown else (self._raw, [])
        self._body_text = body
        self.body.setFont(_point_font(self.body.font()))
        if markdown:
            self.body.setTextFormat(Qt.TextFormat.MarkdownText)
            self.body.setText(prepare_markdown(body))
        else:
            self.body.setTextFormat(Qt.TextFormat.PlainText)
            self.body.setText(body)
        self.body.setVisible(bool((body or "").strip()))
        self._set_photos(images)
        if relayout:
            self._fit()
            QTimer.singleShot(0, self._fit)

    def _set_photos(self, images: list[Path]) -> None:
        while self.photos_layout.count():
            item = self.photos_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        if not images:
            self.photos.setVisible(False)
            return
        cap = min(420, max(160, self._cap - 32))
        thumb = min(148, cap) if len(images) > 1 else cap
        shown = 0
        for path in images[:6]:
            pix = QPixmap(str(path))
            if pix.isNull():
                continue
            if pix.width() > thumb:
                pix = pix.scaledToWidth(thumb, Qt.TransformationMode.SmoothTransformation)
            label = QLabel()
            label.setObjectName("bubblePhoto")
            label.setPixmap(pix)
            label.setFixedSize(pix.size())
            label.setToolTip(path.name)
            self.photos_layout.addWidget(label)
            shown += 1
        self.photos.setVisible(shown > 0)

    def set_cap(self, cap: int) -> None:
        self._cap = max(220, int(cap))
        self.setMaximumWidth(self._cap)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()

    def _fit(self) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            cap = max(220, int(self._cap or 0))
            inner_cap = max(_CARD_MIN_INNER, cap - _CARD_PAD_X)
            body = self._body_text if self._markdown else self._raw
            photo_w = 0
            if self.photos.isVisible():
                many = self.photos_layout.count() > 1
                if many:
                    want = inner_cap
                else:
                    item = self.photos_layout.itemAt(0)
                    widget = item.widget() if item is not None else None
                    hint = widget.width() if widget is not None else 160
                    want = min(inner_cap, max(80, hint))
                self.photos.setFixedWidth(max(80, want))
                self.photos.setFixedHeight(
                    max(40, self.photos_layout.heightForWidth(self.photos.width()))
                )
                photo_w = self.photos.width()
            if not (body or "").strip():
                inner_w, inner_h = max(_CARD_MIN_INNER, photo_w or _CARD_MIN_INNER), 0
                self.body.setFixedSize(0, 0)
            else:
                inner_w, inner_h = measure_card_text(
                    body,
                    inner_cap,
                    markdown=self._markdown,
                    font=self.body.font(),
                )
                self.body.setFixedSize(inner_w, inner_h)
            card_w = min(cap, max(inner_w + _CARD_PAD_X, (photo_w + _CARD_PAD_X) if photo_w else 0))
            # Live tokens arrive a few characters at a time. Keep a readable
            # width so the first "Sure" chunk does not become a 50px sliver.
            if not self._markdown:
                card_w = min(cap, max(card_w, 240))
            # setFixedWidth also clamps maximumWidth — lift min/max first so a
            # later, longer stream can grow instead of staying punished left.
            self.setMinimumWidth(0)
            self.setMaximumWidth(cap)
            self.setFixedWidth(card_w)
            self.setMinimumWidth(0)
            self.setMaximumWidth(cap)
            self.updateGeometry()
        finally:
            self._fitting = False


class ChatTranscript(QScrollArea):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("chatTranscript")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("chatInner")
        self._col = QVBoxLayout(inner)
        self._col.setContentsMargins(10, 12, 10, 16)
        self._col.setSpacing(14)
        self._col.addStretch(1)
        self.setWidget(inner)
        self._live: ChatBubble | None = None
        self._activity: ActivityCard | None = None
        self._live_fits = 0
        self._assistant_name = "Jinx AI"

    def set_assistant_name(self, name: str) -> None:
        self._assistant_name = (name or "").strip() or "Jinx AI"

    def clear(self) -> None:
        self._live = None
        self._activity = None
        while self._col.count() > 1:
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def add(
        self,
        role: str,
        text: str,
        *,
        live: bool = False,
        when: datetime | None | object = _UNSET,
        speaker: str = "",
    ) -> ChatBubble:
        stamp: datetime | None
        if when is _UNSET:
            stamp = datetime.now().astimezone()
        elif isinstance(when, datetime):
            stamp = when
        else:
            stamp = parse_when(when)
        label = speaker if role == "assistant" else ""
        if role == "assistant" and not (label or "").strip():
            label = self._assistant_name
        bubble = ChatBubble(role, text, when=stamp, speaker=label)
        cap = self._bubble_cap()
        bubble.set_cap(cap)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        if role == "user":
            row.addStretch(1)
            row.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        else:
            row.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            row.addStretch(1)
        wrap = QWidget()
        wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        wrap.setLayout(row)
        bubble.wrap = wrap
        self._col.insertWidget(self._col.count() - 1, wrap)
        if live:
            self._live = bubble
        self._scroll_end()
        return bubble

    def update_live(self, text: str) -> None:
        if self._live is None:
            self.add("assistant", text, live=True)
            self._live.set_text(text, markdown=False, relayout=True)
            return
        self._live.set_text(text, markdown=False, relayout=True)
        self._scroll_end()

    def finish_live(self, text: str) -> None:
        if self._live is None:
            self.add("assistant", text)
            return
        self._live.set_text(text, markdown=True, relayout=True)
        self._live = None
        self._scroll_end()

    def discard_live(self) -> None:
        live = self._live
        self._live = None
        if live is None or live.wrap is None:
            return
        wrap = live.wrap
        self._col.removeWidget(wrap)
        wrap.deleteLater()

    def set_activity(self, title: str, detail: str = "") -> None:
        if self._live is not None:
            return
        if self._activity is None:
            card = ActivityCard()
            cap = min(self._bubble_cap(), 520)
            card.setMaximumWidth(cap)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            row.addStretch(1)
            wrap = QWidget()
            wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            wrap.setLayout(row)
            card.wrap = wrap
            self._col.insertWidget(self._col.count() - 1, wrap)
            self._activity = card
        self._activity.set_status(title, detail)
        self._scroll_end()

    def set_tool_preview(
        self, name: str, args_preview: str = "", result_preview: str = ""
    ) -> None:
        if self._activity is None:
            return
        self._activity.set_tool_preview(name, args_preview, result_preview)
        self._scroll_end()

    def finish_activity(self, elapsed_s: float) -> dict:
        card = self._activity
        self._activity = None
        if card is None:
            return {}
        if not card.has_steps():
            wrap = card.wrap
            if wrap is not None:
                self._col.removeWidget(wrap)
                wrap.deleteLater()
            return {}
        snap = card.finish(elapsed_s)
        self._scroll_end()
        return snap

    def add_frozen_activity(self, data: dict) -> None:
        steps = (data or {}).get("steps") or []
        if not steps:
            return
        card = ActivityCard()
        card.restore(dict(data or {}))
        cap = min(self._bubble_cap(), 520)
        card.setMaximumWidth(cap)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        wrap = QWidget()
        wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        wrap.setLayout(row)
        card.wrap = wrap
        self._col.insertWidget(self._col.count() - 1, wrap)

    def clear_activity(self) -> None:
        card = self._activity
        self._activity = None
        if card is None or card.wrap is None:
            return
        wrap = card.wrap
        self._col.removeWidget(wrap)
        wrap.deleteLater()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cap = self._bubble_cap()
        for index in range(self._col.count() - 1):
            wrap = self._col.itemAt(index).widget()
            if wrap is None:
                continue
            bubble = wrap.findChild(ChatBubble)
            if bubble is not None:
                bubble.set_cap(cap)
                bubble._fit()

    def _bubble_cap(self) -> int:
        return bubble_cap(self.viewport().width())

    def _scroll_end(self) -> None:
        bar = self.verticalScrollBar()

        def go() -> None:
            bar.setValue(bar.maximum())

        QTimer.singleShot(0, go)
        QTimer.singleShot(40, go)
        QTimer.singleShot(160, go)
