"""Settings → API Access: cheap/free API keys and per-key enable toggles."""

from __future__ import annotations

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .api_access import (
    CATALOG,
    ApiSpec,
    api_access_settings,
    resolved_key,
    save_api_access,
)
from .widgets import PageHeader, SwitchRow, chip_button


class ApiAccessPage(QWidget):
    changed = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        kinds: tuple[str, ...] | None = None,
        show_header: bool = True,
        compact: bool = False,
        scroll: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._kinds = kinds
        self._fields: dict[str, QLineEdit] = {}
        self._switches: dict[str, SwitchRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0 if compact else 24, 0 if compact else 20, 0 if compact else 28, 0 if compact else 16)
        root.setSpacing(10)
        if show_header:
            root.addWidget(
                PageHeader(
                    "API Access",
                    "Optional keys for free or very cheap APIs. DuckDuckGo search needs "
                    "no key. Chat keys for DeepSeek / Gemini / Groq live under "
                    "AI Models → Cloud API. Hugging Face is for gated GGUF downloads. "
                    "Keys stay on this PC.",
                )
            )

        inner = QWidget()
        inner.setObjectName("page")
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0 if not scroll else 8, 0 if compact else 12)
        col.setSpacing(14)

        want_llm = kinds is None or "llm" in kinds
        want_search = kinds is None or "search" in kinds
        if want_llm:
            if not compact:
                col.addWidget(self._section("Chat APIs"))
            for spec in CATALOG:
                if spec.kind == "llm":
                    col.addWidget(self._card(spec))
        if want_search:
            col.addWidget(self._section("Search"))
            note = QLabel(
                "DuckDuckGo is built in. Brave is optional extra search. "
                "A Hugging Face token is only needed for gated GGUFs."
                if kinds == ("search",)
                else "DuckDuckGo is built in. Brave is optional extra search. "
                "A Hugging Face token unlocks gated GGUF downloads."
            )
            note.setObjectName("muted")
            note.setWordWrap(True)
            col.addWidget(note)
            for spec in CATALOG:
                if spec.kind == "search":
                    col.addWidget(self._card(spec))

        if scroll:
            col.addStretch(1)
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(inner)
            root.addWidget(area, stretch=1)
        else:
            root.addWidget(inner)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.reload()

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _card(self, spec: ApiSpec) -> QFrame:
        card = QFrame()
        card.setObjectName("settingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title = QLabel(spec.title)
        title.setObjectName("hostTitle")
        layout.addWidget(title)
        sub = QLabel(spec.subtitle)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        unlock = QLabel(f"Unlocks: {spec.unlocks}")
        unlock.setObjectName("muted")
        unlock.setWordWrap(True)
        layout.addWidget(unlock)

        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("API key")
        field.setClearButtonEnabled(True)
        field.editingFinished.connect(lambda sid=spec.id: self._save_key(sid))
        self._fields[spec.id] = field

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(field, stretch=1)
        row.addWidget(
            chip_button(
                "Show",
                lambda sid=spec.id: self._toggle_echo(sid),
                tip="Show or hide the key",
            )
        )
        row.addWidget(
            chip_button(
                "Get key",
                lambda url=spec.signup: QDesktopServices.openUrl(QUrl(url)),
                tip=f"Open {spec.title} in the browser",
            )
        )
        layout.addLayout(row)

        switch = SwitchRow("Use this", "Off keeps the key but Jinx will not call this API.")
        switch.toggled.connect(lambda on, sid=spec.id: self._save_enabled(sid, on))
        self._switches[spec.id] = switch
        layout.addWidget(switch)
        return card

    def reload(self) -> None:
        cfg = api_access_settings()
        for spec in CATALOG:
            field = self._fields.get(spec.id)
            if field is not None:
                field.blockSignals(True)
                field.setText(resolved_key(spec.id) or str(cfg.get(spec.key_field) or ""))
                field.blockSignals(False)
            switch = self._switches.get(spec.id)
            if switch is not None:
                switch.blockSignals(True)
                switch.setChecked(bool(cfg.get(spec.enabled_field, True)))
                switch.blockSignals(False)

    def _toggle_echo(self, kind: str) -> None:
        field = self._fields.get(kind)
        if field is None:
            return
        hidden = field.echoMode() == QLineEdit.EchoMode.Password
        field.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)

    def _save_key(self, kind: str) -> None:
        spec = next((item for item in CATALOG if item.id == kind), None)
        field = self._fields.get(kind)
        if spec is None or field is None:
            return
        save_api_access({spec.key_field: field.text().strip()})
        self.changed.emit()

    def _save_enabled(self, kind: str, on: bool) -> None:
        spec = next((item for item in CATALOG if item.id == kind), None)
        if spec is None:
            return
        save_api_access({spec.enabled_field: bool(on)})
        self.changed.emit()
