"""Post-hoc session activity timeline dialog."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .trace import load_events, render_markdown


def format_timeline(session_id: str, *, title: str = "") -> str:
    """Human-readable timeline from the JSONL trace (or markdown fallback)."""
    events = load_events(session_id)
    if not events:
        return render_markdown(session_id, title=title)
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
        lines.append("")
    for event in events:
        kind = str(event.get("kind") or "")
        ts = str(event.get("ts") or "")[:19]
        if kind == "turn_start":
            lines.append(f"## Turn · {ts}")
            user = str(event.get("user") or "").strip()
            if user:
                lines.append(f"**User:** {user[:240]}")
            lines.append("")
        elif kind in {"tool_call", "tool"}:
            label = event.get("label") or event.get("name") or "tool"
            lines.append(f"- **{label}** `{ts}`")
            args = event.get("arguments")
            if args:
                lines.append(f"  - args: `{str(args)[:200]}`")
            result = event.get("result")
            if result:
                lines.append(f"  - result: `{str(result)[:240]}`")
        elif kind == "tool_result":
            label = event.get("label") or event.get("name") or "result"
            skipped = " (skipped)" if event.get("skipped") else ""
            lines.append(f"- ← **{label}**{skipped}")
            result = event.get("result")
            if result:
                lines.append(f"  - `{str(result)[:240]}`")
        elif kind == "activity":
            lines.append(
                f"- _{event.get('title') or 'activity'}_"
                f" {event.get('detail') or ''}".rstrip()
            )
        elif kind == "approval_required":
            lines.append(f"- **approval** {event.get('tools') or ''}")
        elif kind == "ask_user":
            lines.append(f"- **ask user:** {event.get('prompt') or ''}")
        elif kind == "turn_end":
            reply = str(event.get("reply") or event.get("error") or "").strip()
            if reply:
                lines.append(f"**End:** {reply[:300]}")
            lines.append("")
        elif kind == "error":
            lines.append(f"- **error:** {event.get('message') or ''}")
    return "\n".join(lines).strip() + "\n"


class ActivityTimelineDialog(QDialog):
    def __init__(self, session_id: str, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Session activity")
        self.resize(560, 520)
        self.setObjectName("page")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        head = QLabel(title or f"Session {session_id[:12]}")
        head.setObjectName("hostTitle")
        root.addWidget(head)
        hint = QLabel("Live tool timeline for this chat (from data/traces).")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setMarkdown(format_timeline(session_id, title=title))
        root.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)
