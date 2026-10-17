"""AI Models manager: local llama-server, HF GGUF browser, Cloud API, ComfyUI."""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .api_access_page import ApiAccessPage
from .comfyui import (
    comfy_display_origin,
    launch_comfyui,
    owned_by_jinx,
    probe_comfyui,
    stop_comfyui,
)
from .fa import IconToggle, icon_button
from .icon import create_activity_icon
from .llama_server import owned_by_jinx as llama_owned, stop_server
from .lm_runtime import (
    HostSnapshot,
    LmModel,
    delete_model,
    dequeue_download,
    enqueue_download,
    format_capabilities,
    format_size,
    host_label,
    inject_pulled_model,
    launch_host,
    load_model,
    model_names_match,
    probe_all,
    probe_host,
    pull_model_stream,
    snapshot_has_model,
    stop_local,
    unload_model,
)
from .local_models import models_dir, move_models_dir
from .settings_store import (
    brains_settings,
    list_role_models,
    resolve_role_brain,
    save_brains_settings,
    save_role_brain,
    saved_role_brain,
)
from .widgets import FlowLayout, PageHeader, Pill, SwitchRow, chip_button


class _Job(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _StreamJob(QThread):
    progress = pyqtSignal(object)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._abort = threading.Event()

    def cancel(self) -> None:
        self._abort.set()

    def aborted(self) -> bool:
        return self._abort.is_set()

    def run(self) -> None:
        try:
            msg = self._fn(self.progress.emit, self._abort.is_set)
            if not self._abort.is_set():
                self.done.emit(str(msg or "Done"))
        except Exception as exc:  # noqa: BLE001
            if self._abort.is_set():
                self.failed.emit("Cancelled")
            else:
                self.failed.emit(str(exc))


class LmPanel(QWidget):
    """Provider manager for local and cloud models. Lives on the Models activity tab."""

    use_chat = pyqtSignal(str, str)
    use_image = pyqtSignal(str, str)
    host_enabled = pyqtSignal(str, bool)
    status = pyqtSignal(str)
    snapshots_ready = pyqtSignal(object)
    api_changed = pyqtSignal()
    roles_changed = pyqtSignal()
    pull_changed = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._snaps: dict[str, HostSnapshot] = {}
        self._probe: _Job | None = None
        self._probe_again = False
        self._action: _Job | None = None
        self._recs_job: _Job | None = None
        self._streams: dict[str, _StreamJob] = {}
        self._host_sync: dict[str, _Job] = {}
        self._host_sync_again: dict[str, bool] = {}
        self._browser_files: list = []
        self._browser_repo = ""
        self._hub_all: list = []
        self._hub_page = 0
        self._hub_gpu = True
        self._hub_vision = False
        self._hub_tools = True
        self._hub_rec = True
        self._pulling: dict[str, str] = {"local": ""}
        self._pull_queue: dict[str, list[str]] = {"local": []}
        self._pull_payload: dict = {}
        self._live_bars: dict[str, QProgressBar] = {}
        self._live_ticks: dict[str, QLabel] = {}
        self._installed_keys: set[str] | None = None
        self._interrupted_keys: set[str] | None = None
        self._pending_models: dict[str, set[str]] = {"local": set()}
        self._host_ui: dict[str, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 20, 16)
        root.setSpacing(10)
        root.addWidget(
            PageHeader("AI Models"),
        )
        self.chat_banner = QLabel()
        self.chat_banner.setObjectName("muted")
        self.chat_banner.setWordWrap(True)
        root.addWidget(self.chat_banner)
        self.image_banner = QLabel()
        self.image_banner.setObjectName("muted")
        self.image_banner.setWordWrap(True)
        root.addWidget(self.image_banner)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(188)
        nav_l = QVBoxLayout(nav)
        nav_l.setContentsMargins(10, 8, 10, 12)
        nav_l.setSpacing(4)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav: dict[str, QPushButton] = {}
        roles_heading = QLabel("Roles")
        roles_heading.setObjectName("sectionLabel")
        nav_l.addWidget(roles_heading)
        for key, label, tip, icon in (
            ("general", "General", "Default model for each job", "sliders"),
        ):
            btn = self._nav_btn(key, label, tip, icon)
            nav_l.addWidget(btn)
        providers_heading = QLabel("Providers")
        providers_heading.setObjectName("sectionLabel")
        nav_l.addWidget(providers_heading)
        for key, label, tip, icon in (
            ("local", "Local", "Installed GGUFs and llama-server", "brain"),
            ("browser", "HF Hub", "Hugging Face Hub GGUFs to run locally", "download"),
            ("downloads", "Downloads", "GGUF download history", "folder-open"),
            ("cloud", "Cloud API", "DeepSeek, Google AI Studio, Groq", "cloud"),
            ("comfyui", "ComfyUI", "Hidden image server", "image"),
        ):
            btn = self._nav_btn(key, label, tip, icon)
            nav_l.addWidget(btn)
        nav_l.addStretch(1)
        body.addWidget(nav)

        self._stack = QStackedWidget()
        self._pages = {
            "general": self._build_general_page(),
            "local": self._build_host_page("local"),
            "browser": self._build_browser_page(),
            "downloads": self._build_downloads_page(),
            "cloud": self._build_cloud_page(),
            "comfyui": self._build_comfy_page(),
        }
        for key in ("general", "local", "browser", "downloads", "cloud", "comfyui"):
            self._stack.addWidget(self._pages[key])
        body.addWidget(self._stack, stretch=1)
        root.addLayout(body, stretch=1)

        self._timer = QTimer(self)
        self._timer.setInterval(8000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._paint_banners()
        for kind in ("local", "deepseek", "gemini", "groq", "comfyui"):
            self._paint_host(kind, HostSnapshot(kind=kind))
        self._paint_roles()
        self._show_provider("general")
        self._recover_downloads()
        QTimer.singleShot(200, self.refresh)

    def set_polling(self, active: bool) -> None:
        self._timer.setInterval(3000 if active else 20000)
        if active:
            self.refresh()

    def current_snapshots(self) -> dict[str, HostSnapshot]:
        return dict(self._snaps)

    def _nav_btn(self, key: str, label: str, tip: str, icon: str) -> QPushButton:
        btn = QPushButton(f"  {label}")
        btn.setCheckable(True)
        btn.setObjectName("settingsNavBtn")
        btn.setToolTip(tip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(create_activity_icon(icon, 16, "#c7c7cc"))
        btn.setIconSize(QSize(16, 16))
        btn.clicked.connect(lambda _=False, k=key: self._show_provider(k))
        self._nav_group.addButton(btn)
        self._nav[key] = btn
        return btn

    def _show_provider(self, key: str) -> None:
        order = ("general", "local", "browser", "downloads", "cloud", "comfyui")
        if key not in self._pages:
            key = "general"
        self._stack.setCurrentIndex(order.index(key))
        for name, btn in self._nav.items():
            btn.blockSignals(True)
            btn.setChecked(name == key)
            btn.blockSignals(False)
        if key == "downloads":
            self._paint_downloads()
        self.refresh()

    def show_downloads(self) -> None:
        self._paint_downloads()
        self._show_provider("downloads")

    def pull_snapshot(self) -> dict:
        from .downloads import recent_downloads, with_live_status
        from .local_models import models_dir

        current = self._pulling.get("local") or ""
        queued = list(self._pull_queue.get("local") or [])
        payload = dict(self._pull_payload or {})
        return {
            "current": current,
            "queued": queued,
            "percent": int(payload.get("percent") or 0),
            "text": str(payload.get("text") or ""),
            "indeterminate": bool(payload.get("indeterminate")),
            "folder": str(models_dir()),
            "active": bool(current or queued),
            "recent": with_live_status(recent_downloads(5), current, queued),
        }

    def _persist_job(self) -> None:
        from .downloads import save_job

        save_job(self._pulling.get("local") or "", list(self._pull_queue.get("local") or []))

    def _recover_downloads(self) -> None:
        from .downloads import recover_interrupted
        from .local_models import models_dir

        recover_interrupted(models_dir())
        self._interrupted_keys = None
        self._installed_keys = None

    def _emit_pull(self) -> None:
        self._persist_job()
        self.pull_changed.emit(self.pull_snapshot())

    def refresh(self) -> None:
        if self._probe is not None and self._probe.isRunning():
            self._probe_again = True
            return
        self._probe_again = False

        def work() -> dict:
            snaps = probe_all()
            snaps["comfyui"] = probe_comfyui()
            return snaps

        job = _Job(work, self)
        self._probe = job
        job.done.connect(self._on_snapshots)
        job.failed.connect(self._on_probe_failed)
        job.finished.connect(lambda: self._clear_probe(job))
        job.start()

    def _clear_probe(self, job: _Job) -> None:
        if self._probe is job:
            self._probe = None
        job.deleteLater()
        if self._probe_again:
            self._probe_again = False
            self.refresh()

    def _on_probe_failed(self, err: str) -> None:
        self._show_hub_error(err)

    def _on_snapshots(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self._snaps = {str(k): v for k, v in payload.items() if isinstance(v, HostSnapshot)}
        for kind in ("local",):
            snap = self._snaps.get(kind)
            if snap is not None:
                self._merge_pending(kind, snap)
        for kind in ("local", "deepseek", "gemini", "groq", "comfyui"):
            self._paint_host(kind, self._snaps.get(kind) or HostSnapshot(kind=kind))
        self._paint_banners()
        self._paint_cloud_models()
        self._paint_roles()
        self.snapshots_ready.emit(self._snaps)
        self._sync_installed_pills()

    def _merge_pending(self, kind: str, snap: HostSnapshot) -> None:
        pending = self._pending_models.get(kind) or set()
        if not pending:
            return
        still: set[str] = set()
        for name in pending:
            if snapshot_has_model(snap, name):
                continue
            inject_pulled_model(snap, name)
            still.add(name)
        self._pending_models[kind] = still

    def _sync_host(self, kind: str) -> None:
        host = "local" if kind != "comfyui" else "comfyui"
        job = self._host_sync.get(host)
        if job is not None and job.isRunning():
            self._host_sync_again[host] = True
            return
        self._host_sync_again[host] = False

        def work() -> HostSnapshot:
            return probe_host(host)

        job = _Job(work, self)
        self._host_sync[host] = job
        job.done.connect(lambda snap, h=host: self._on_host_snap(h, snap))
        job.failed.connect(self._on_probe_failed)
        job.finished.connect(lambda j=job, h=host: self._clear_host_sync(h, j))
        job.start()

    def _clear_host_sync(self, kind: str, job: _Job) -> None:
        if self._host_sync.get(kind) is job:
            self._host_sync.pop(kind, None)
        job.deleteLater()
        if self._host_sync_again.get(kind):
            self._host_sync_again[kind] = False
            self._sync_host(kind)

    def _on_host_snap(self, kind: str, payload: object) -> None:
        if not isinstance(payload, HostSnapshot):
            return
        self._merge_pending(kind, payload)
        self._snaps[kind] = payload
        self._paint_host(kind, payload)
        self._paint_banners()
        self._paint_roles()
        self.snapshots_ready.emit(self._snaps)
        if kind == "local":
            self._sync_installed_pills()

    def _paint_banners(self) -> None:
        chat_host, chat_model = resolve_role_brain("companion")
        snap = self._snaps.get(chat_host)
        caps = []
        loaded = False
        if snap is not None:
            for model in snap.models:
                if model.name == chat_model:
                    caps = model.capabilities
                    loaded = model.loaded
                    break
        bits = format_capabilities(caps)
        state = "loaded" if loaded else "selected"
        if chat_model:
            extra = f" · {bits}" if bits else ""
            line = f"Chat: {host_label(chat_host)} / {chat_model} ({state}{extra})"
            coding_host, coding_model = resolve_role_brain("coding")
            if (coding_host, coding_model) != (chat_host, chat_model):
                line += f"  ·  Coding: {host_label(coding_host)} / {coding_model}"
            self.chat_banner.setText(line)
        else:
            self.chat_banner.setText("Chat: pick a model on General, or Use on a provider row.")
        image_host, image_model = resolve_role_brain("image")
        if image_model:
            self.image_banner.setText(
                f"Image slot: {host_label(image_host or 'comfyui')} / {image_model} "
                "(ComfyUI — chat LLM stays loaded)."
            )
        else:
            self.image_banner.setText(
                "Image slot: empty. Pick a ComfyUI checkpoint. "
                "We never unload the chat model to draw."
            )

    def _search_browser(self, query: str = "") -> None:
        if self._recs_job is not None and self._recs_job.isRunning():
            return
        text = (query or (self._browser_query.text() if hasattr(self, "_browser_query") else "")).strip()
        rec = bool(self._hub_rec)
        self.status.emit("Searching Hugging Face Hub…")

        def work() -> list:
            from .gguf_browser import search_hub

            return search_hub(text, recommended=rec)

        job = _Job(work, self)
        self._recs_job = job
        job.done.connect(self._on_browser_families)
        job.failed.connect(self._on_probe_failed)
        job.finished.connect(lambda: self._clear_recs_job(job))
        job.start()

    def _on_hub_filter(self, key: str, on: bool, btn: QPushButton) -> None:
        if key == "gpu":
            self._hub_gpu = bool(on)
        elif key == "vision":
            self._hub_vision = bool(on)
        elif key == "tools":
            self._hub_tools = bool(on)
        elif key == "rec":
            self._hub_rec = bool(on)
            self._sync_hub_rec_note()
        btn.setObjectName("chipPrimary" if on else "chipBtn")
        btn.style().unpolish(btn)
        btn.style().polish(btn)
        self._hub_page = 0
        if key == "rec":
            self._search_browser()
            return
        self._paint_hub_cards()

    def _visible_hub_cards(self) -> list:
        from .gguf_browser import filter_cards

        return filter_cards(
            list(self._hub_all or []),
            gpu=self._hub_gpu,
            vision=self._hub_vision,
            tools=self._hub_tools,
            recommended=self._hub_rec,
        )

    def _sync_hub_rec_note(self) -> None:
        note = getattr(self, "_hub_rec_note", None)
        if note is None:
            return
        note.setVisible(bool(self._hub_rec))

    def _clear_recs_job(self, job: _Job) -> None:
        if self._recs_job is job:
            self._recs_job = None
        job.deleteLater()

    def _on_browser_families(self, payload: object) -> None:
        from .gguf_browser import HubCard

        rows = [item for item in (payload or []) if isinstance(item, HubCard)]
        self._hub_all = rows
        self._hub_page = 0
        self._paint_hub_cards()
        visible = self._visible_hub_cards()
        self.status.emit(f"{len(visible)} Hugging Face GGUFs match the filters ({len(rows)} fetched)")

    def _open_family(self, repo: str) -> None:
        self._browser_repo = repo

        def work() -> list:
            from .gguf_browser import list_repo_files

            return list_repo_files(repo)

        job = _Job(work, self)
        job.done.connect(lambda files, r=repo: self._on_browser_files(r, files))
        job.failed.connect(self._on_probe_failed)
        job.start()

    def _on_browser_files(self, repo: str, payload: object) -> None:
        from .gguf_browser import HfFile, pick_quant
        from .hardware import probe_hardware

        files = [item for item in (payload or []) if isinstance(item, HfFile)]
        try:
            vram = float(probe_hardware().vram_gb or 0)
        except Exception:  # noqa: BLE001
            vram = 0
        pick_quant(files, vram_gb=vram)
        self._browser_files = files
        self._paint_browser_files(repo, files)

    def _local_has(self, name: str) -> bool:
        from .lm_runtime import model_key

        want = model_key(name)
        if not want:
            return False
        if self._installed_keys is None:
            self._installed_keys = self._scan_installed_keys()
        return want in self._installed_keys

    def _interrupted_has(self, name: str) -> bool:
        from .lm_runtime import model_key

        want = model_key(name)
        if not want:
            return False
        if self._is_pulling("local", name) or self._is_queued("local", name):
            return False
        if self._interrupted_keys is None:
            self._interrupted_keys = self._scan_interrupted_keys()
        return want in self._interrupted_keys

    def _sync_installed_pills(self) -> None:
        old = self._installed_keys
        old_i = self._interrupted_keys
        self._installed_keys = self._scan_installed_keys()
        self._interrupted_keys = self._scan_interrupted_keys()
        if old != self._installed_keys or old_i != self._interrupted_keys:
            self._refresh_browser_pills()

    def _scan_installed_keys(self) -> set[str]:
        from .lm_runtime import model_key
        from .local_models import scan_gguf

        keys: set[str] = set()
        for item in self._pending_models.get("local") or []:
            key = model_key(item)
            if key:
                keys.add(key)
        try:
            for item in scan_gguf():
                key = model_key(item.name)
                if key:
                    keys.add(key)
        except Exception:  # noqa: BLE001
            pass
        return keys

    def _scan_interrupted_keys(self) -> set[str]:
        from .downloads import RETRY_STATUSES, load_history
        from .lm_runtime import model_key
        from .local_models import incomplete_gguf_names

        keys: set[str] = set()
        for item in load_history():
            if str(item.get("status") or "") in RETRY_STATUSES:
                key = model_key(str(item.get("name") or ""))
                if key:
                    keys.add(key)
        try:
            for name in incomplete_gguf_names():
                key = model_key(name)
                if key:
                    keys.add(key)
        except Exception:  # noqa: BLE001
            pass
        return keys - self._scan_installed_keys()

    def _reset_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _build_general_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 8, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(
            PageHeader(
                "Roles",
                "Each job can use a different brain. Installed local models and cloud "
                "hosts with an API key appear here. Coding and Memory can follow Chat.",
                compact=True,
            )
        )
        self._role_combos: dict[str, QComboBox] = {}
        roles = (
            (
                "companion",
                "Chat",
                "Assistant replies and the evening wrap-up.",
            ),
            (
                "coding",
                "Coding",
                "Developer agent: tools, files, shell, clarify, commit. "
                "Leave on Same as Chat unless you want a coder model.",
            ),
            (
                "memory",
                "Memory",
                "mem0 fact extraction. Local llama-server only. Same as Chat skips "
                "extraction when Chat is a cloud host.",
            ),
            (
                "image",
                "Images",
                "ComfyUI checkpoint for generate_image. The chat LLM stays loaded.",
            ),
        )
        for role, title, blurb in roles:
            layout.addWidget(self._role_card(role, title, blurb))
        layout.addStretch(1)
        return page

    def _role_card(self, role: str, title: str, blurb: str) -> QFrame:
        card = QFrame()
        card.setObjectName("settingsCard")
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(6)
        heading = QLabel(title)
        heading.setObjectName("modelName")
        col.addWidget(heading)
        hint = QLabel(blurb)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        col.addWidget(hint)
        combo = QComboBox()
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(24)
        combo.setMaxVisibleItems(18)
        combo.activated.connect(lambda _i, r=role: self._on_role_picked(r))
        self._role_combos[role] = combo
        col.addWidget(combo)
        return card

    def _models_by_host(self) -> dict[str, list[str]]:
        return {
            kind: [model.name for model in snap.models]
            for kind, snap in self._snaps.items()
        }

    def _paint_roles(self) -> None:
        combos = getattr(self, "_role_combos", None)
        if not combos:
            return
        models = self._models_by_host()
        for role, combo in combos.items():
            view = combo.view()
            if view is not None and view.isVisible():
                continue
            self._fill_role_combo(combo, role, models)

    def _fill_role_combo(
        self,
        combo: QComboBox,
        role: str,
        models: dict[str, list[str]],
    ) -> None:
        inherit = role in {"coding", "memory"}
        saved_host, saved_model = saved_role_brain(role)
        choices = list_role_models(models, role)
        combo.blockSignals(True)
        combo.clear()
        if inherit:
            chat_host, chat_model = resolve_role_brain("companion")
            inherit_label = "Same as Chat"
            if chat_model:
                inherit_label = (
                    f"Same as Chat — {host_label(chat_host)}  ·  {chat_model}"
                )
            combo.addItem(inherit_label, "\t")
        last_host = None
        for host, name in choices:
            if last_host is not None and host != last_host:
                combo.insertSeparator(combo.count())
            last_host = host
            combo.addItem(f"{host_label(host)}  ·  {name}", f"{host}\t{name}")
        if not choices and not inherit:
            combo.addItem("No models yet — pull one or add an API key", "")
        target = "\t" if inherit and not saved_host else f"{saved_host}\t{saved_model}"
        if role in {"companion", "image"} and not saved_model:
            if choices:
                combo.insertItem(0, "Pick a model", "")
            target = ""
        idx = combo.findData(target)
        if idx < 0 and saved_host and saved_model:
            combo.addItem(
                f"{host_label(saved_host)}  ·  {saved_model}  (unavailable)",
                f"{saved_host}\t{saved_model}",
            )
            idx = combo.count() - 1
        if idx < 0:
            idx = 0
        if combo.count():
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _on_role_picked(self, role: str) -> None:
        combo = (getattr(self, "_role_combos", None) or {}).get(role)
        if combo is None:
            return
        raw = combo.currentData()
        if not isinstance(raw, str):
            return
        host, _, model = raw.partition("\t")
        if role == "companion" and (not host or not model):
            return
        save_role_brain(role, host, model)
        if role == "companion" and host and model:
            self.use_chat.emit(host, model)
        elif role == "image":
            self.use_image.emit(host or "comfyui", model)
        self.roles_changed.emit()
        self._paint_banners()
        self._paint_roles()
        for kind in ("local", "comfyui"):
            snap = self._snaps.get(kind)
            if snap is not None:
                self._paint_host(kind, snap)
        self._paint_cloud_models()
        if host and model:
            self.status.emit(f"{role.capitalize()}: {host_label(host)} / {model}")
        elif role in {"coding", "memory"}:
            self.status.emit(f"{role.capitalize()} follows Chat.")

    def _build_host_page(self, kind: str) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 8, 8, 8)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Local")
        title.setObjectName("hostTitle")
        header.addWidget(title)
        status = QLabel()
        status.setObjectName("muted")
        status.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(status, stretch=1)
        toggle = IconToggle(
            "power",
            "power",
            "Local brains on — Jinx may use GGUFs",
            "Local brains off",
            size=28,
            checked=True,
        )
        toggle.toggled.connect(lambda on, host=kind: self._on_enabled(host, on))
        header.addWidget(toggle)
        launch = chip_button("Launch", lambda host=kind: self._launch(host), primary=True)
        header.addWidget(launch)
        stop = chip_button("Stop", self._stop_local, danger=True, tip="Stop llama-server")
        header.addWidget(stop)
        header.addWidget(chip_button("Refresh", lambda host=kind: self._sync_host(host), tip="Probe again"))
        layout.addLayout(header)

        folder_row = QHBoxLayout()
        folder_field = QLineEdit()
        folder_field.setText(str(models_dir()))
        folder_field.setPlaceholderText(r"C:\Jinx-AI\models")
        folder_field.editingFinished.connect(self._commit_models_dir)
        folder_row.addWidget(folder_field, stretch=1)
        folder_row.addWidget(chip_button("Browse", self._browse_models_dir, tip="Pick a folder for GGUFs"))
        layout.addLayout(folder_row)

        strip = QFrame()
        strip.setObjectName("pullStrip")
        strip.setVisible(False)
        strip_l = QVBoxLayout(strip)
        strip_l.setContentsMargins(12, 8, 12, 8)
        strip_l.setSpacing(6)
        pull_title = QLabel()
        pull_title.setObjectName("modelName")
        strip_l.addWidget(pull_title)
        bar = QProgressBar()
        bar.setObjectName("pullBar")
        bar.setTextVisible(False)
        bar.setRange(0, 100)
        strip_l.addWidget(bar)
        pull_meta = QLabel()
        pull_meta.setObjectName("muted")
        pull_meta.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(pull_meta, stretch=1)
        cancel = chip_button(
            "Cancel",
            lambda host=kind: self._cancel_pull(host),
            danger=True,
            tip="Stop this download",
        )
        row.addWidget(cancel)
        strip_l.addLayout(row)
        queue_host = QWidget()
        queue_host.setVisible(False)
        queue_layout = FlowLayout(queue_host, spacing=6)
        strip_l.addWidget(queue_host)
        layout.addWidget(strip)

        hint = QLabel("Installed GGUFs in the folder above. Pull new ones from Hugging Face Hub.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("page")
        models_l = QVBoxLayout(inner)
        models_l.setContentsMargins(0, 0, 8, 8)
        models_l.setSpacing(4)
        models_l.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        self._host_ui[kind] = {
            "status": status,
            "toggle": toggle,
            "launch": launch,
            "stop": stop,
            "strip": strip,
            "pull_title": pull_title,
            "bar": bar,
            "pull_meta": pull_meta,
            "folder": folder_field,
            "models": models_l,
            "queue_host": queue_host,
            "queue_layout": queue_layout,
        }
        return page

    def _build_browser_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 8, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(
            PageHeader(
                "Hugging Face Hub",
                "Browse GGUFs to run locally on llama-server. Cards show VRAM for the "
                "quant that fits this GPU. Recommended starts on (Jinx's trusted instruct "
                "picks). Gated repos need a Hugging Face token in Settings → API Access.",
                compact=True,
            )
        )
        from .hardware import probe_hardware

        try:
            hw = probe_hardware()
            hw_line = hw.prompt_line()
        except Exception:  # noqa: BLE001
            hw_line = "This PC: GPU unknown"
        self._hub_hw = QLabel(hw_line)
        self._hub_hw.setObjectName("muted")
        self._hub_hw.setWordWrap(True)
        layout.addWidget(self._hub_hw)
        search = QHBoxLayout()
        self._browser_query = QLineEdit()
        self._browser_query.setPlaceholderText("Search — gemma, qwen2.5-vl, ministral")
        self._browser_query.returnPressed.connect(lambda: self._search_browser())
        search.addWidget(self._browser_query, stretch=1)
        search.addWidget(chip_button("Search", lambda: self._search_browser(), primary=True))
        layout.addLayout(search)
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self._hub_filter_btns: dict[str, QPushButton] = {}
        for key, label, tip, on, icon in (
            ("rec", "Recommended", "Jinx's trusted instruct GGUFs for this GPU", True, "star"),
            ("gpu", "Fully GPU load", "Recommended quant fits ~70% of this GPU", True, "gpu"),
            ("vision", "Has Vision", "mmproj or a vision / VL tag", False, "look"),
            ("tools", "Has Tools Calling", "Instruct / tool-calling families", True, "wrench"),
        ):
            btn = QPushButton(f"  {label}")
            btn.setCheckable(True)
            btn.setChecked(on)
            btn.setObjectName("chipPrimary" if on else "chipBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setIcon(create_activity_icon(icon, 14, "#e8e8ed"))
            btn.setIconSize(QSize(14, 14))
            btn.toggled.connect(lambda on, k=key, b=btn: self._on_hub_filter(k, on, b))
            self._hub_filter_btns[key] = btn
            filters.addWidget(btn)
        filters.addStretch(1)
        layout.addLayout(filters)
        from .gguf_browser import RECOMMENDED_BLURB

        self._hub_rec_note = QLabel(RECOMMENDED_BLURB)
        self._hub_rec_note.setObjectName("muted")
        self._hub_rec_note.setWordWrap(True)
        layout.addWidget(self._hub_rec_note)
        self._sync_hub_rec_note()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("page")
        results = QVBoxLayout(inner)
        results.setContentsMargins(0, 0, 8, 8)
        results.setSpacing(8)
        results.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)
        self._browser_results = results
        QTimer.singleShot(400, lambda: self._search_browser(""))
        return page

    def _build_downloads_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 8, 8, 8)
        layout.setSpacing(10)
        layout.addWidget(
            PageHeader(
                "Downloads",
                "Every GGUF pull Jinx finished, failed, or cancelled. "
                "Open containing folder jumps to the Local models directory.",
                compact=True,
            )
        )
        layout.addWidget(
            chip_button(
                "Open containing folder",
                self._open_models_folder,
                tip="Open the GGUF folder in File Explorer",
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("page")
        results = QVBoxLayout(inner)
        results.setContentsMargins(0, 0, 8, 8)
        results.setSpacing(6)
        results.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)
        self._download_results = results
        return page

    def _paint_downloads(self) -> None:
        from .downloads import display_name, format_when, load_history, with_live_status

        layout = getattr(self, "_download_results", None)
        if layout is None:
            return
        self._reset_layout(layout)
        rows = with_live_status(
            load_history(),
            self._pulling.get("local") or "",
            list(self._pull_queue.get("local") or []),
        )
        if not rows:
            empty = QLabel("No downloads yet. Pull a GGUF from Hugging Face Hub.")
            empty.setObjectName("muted")
            layout.addWidget(empty)
            layout.addStretch(1)
            return
        for item in rows:
            row = QFrame()
            row.setObjectName("modelRow")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(10, 6, 8, 6)
            lay.setSpacing(8)
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(1)
            spec = str(item.get("name") or "")
            name = QLabel(display_name(spec))
            name.setObjectName("modelName")
            name.setWordWrap(True)
            col.addWidget(name)
            when = format_when(str(item.get("finished_at") or ""))
            status = str(item.get("status") or "done")
            err = str(item.get("error") or "").strip()
            bits = [part for part in (when, status, err) if part]
            meta = QLabel(" · ".join(bits) or "done")
            meta.setObjectName("muted")
            meta.setWordWrap(True)
            col.addWidget(meta)
            lay.addLayout(col, stretch=1)
            if status in {"interrupted", "cancelled", "failed"}:
                lay.addWidget(
                    icon_button(
                        "redo",
                        "Retry download",
                        lambda n=spec: self._start_pull("local", n),
                        size=28,
                    )
                )
            layout.addWidget(row)
        layout.addStretch(1)

    def _open_models_folder(self) -> None:
        from .context_menu import reveal_in_explorer

        reveal_in_explorer(models_dir())

    def _browse_models_dir(self) -> None:
        current = str(models_dir())
        picked = QFileDialog.getExistingDirectory(self, "GGUF folder", current)
        if not picked:
            return
        ui = self._host_ui.get("local") or {}
        field = ui.get("folder")
        if field is not None:
            field.setText(picked)
        self._commit_models_dir()

    def _commit_models_dir(self) -> None:
        ui = self._host_ui.get("local") or {}
        field = ui.get("folder")
        raw = str(field.text() if field is not None else "").strip() or str(models_dir())
        old = models_dir()
        from pathlib import Path

        new = Path(raw).expanduser()
        if old.resolve() == new.resolve():
            save_brains_settings({"models_dir": str(new)})
            return

        def work() -> str:
            move_models_dir(old, new, stop=stop_server)
            save_brains_settings({"models_dir": str(new)})
            try:
                launch_host("local")
            except Exception as exc:  # noqa: BLE001
                return f"Moved models to {new}. Launch failed: {exc}"
            return f"Moved models to {new}"

        job = _Job(work, self)
        job.done.connect(lambda msg: self.status.emit(str(msg)))
        job.done.connect(lambda: self._sync_host("local"))
        job.failed.connect(self._on_action_failed)
        job.start()

    def _stop_local(self) -> None:
        def work() -> str:
            return stop_local()

        job = _Job(work, self)
        job.done.connect(lambda msg: self.status.emit(str(msg)))
        job.done.connect(lambda: self._sync_host("local"))
        job.failed.connect(self._on_action_failed)
        job.start()

    def _build_cloud_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 8, 8, 8)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("page")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 12)
        layout.setSpacing(10)
        layout.addWidget(
            PageHeader(
                "Cloud API",
                "Paste a key, leave Use on, then pick a model. Nothing launches locally.",
                compact=True,
            )
        )
        self.cloud_keys = ApiAccessPage(
            kinds=("llm",), show_header=False, compact=True, scroll=False
        )
        self.cloud_keys.changed.connect(self.api_changed.emit)
        self.cloud_keys.changed.connect(self.refresh)
        layout.addWidget(self.cloud_keys)
        self._cloud_models = QVBoxLayout()
        layout.addLayout(self._cloud_models)
        layout.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, stretch=1)
        return page

    def _build_comfy_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 8, 8, 8)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("ComfyUI")
        title.setObjectName("hostTitle")
        header.addWidget(title)
        status = QLabel()
        status.setObjectName("muted")
        status.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(status, stretch=1)
        toggle = IconToggle(
            "image",
            "image",
            "ComfyUI on — Jinx may start it for images",
            "ComfyUI off",
            size=28,
            checked=True,
        )
        toggle.toggled.connect(lambda on: self._on_enabled("comfyui", on))
        header.addWidget(toggle)
        launch = chip_button(
            "Launch",
            lambda: self._launch("comfyui"),
            primary=True,
            tip="Start hidden ComfyUI (no Desktop window)",
        )
        header.addWidget(launch)
        stop = chip_button("Stop", self._stop_comfy, danger=True, tip="Stop the hidden server Jinx started")
        header.addWidget(stop)
        header.addWidget(chip_button("Refresh", self.refresh))
        layout.addLayout(header)

        brains = brains_settings()
        card = QFrame()
        card.setObjectName("settingsCard")
        form = QVBoxLayout(card)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)
        hint = QLabel(
            "Jinx talks to a hidden Python server. She does not open Comfy Desktop. "
            "These values are what she uses when she starts, stops, and draws."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addWidget(hint)
        self.comfy_autostop = SwitchRow(
            "Stop after each chat turn",
            "On: free VRAM when the turn ends. Off: leave the hidden server running.",
            checked=bool(brains.get("comfyui_autostop", True)),
        )
        self.comfy_autostop.toggled.connect(
            lambda on: save_brains_settings({"comfyui_autostop": bool(on)})
        )
        form.addWidget(self.comfy_autostop)
        self.comfy_url = QLineEdit()
        self.comfy_url.setPlaceholderText("http://127.0.0.1:8188 — empty = auto-detect")
        self.comfy_url.setText(str(brains.get("comfyui_url") or ""))
        self.comfy_url.editingFinished.connect(self._save_comfy_fields)
        form.addWidget(_labeled("URL", self.comfy_url))
        self.comfy_root = QLineEdit()
        self.comfy_root.setPlaceholderText("Optional install folder — empty = auto")
        self.comfy_root.setText(str(brains.get("comfyui_root") or ""))
        self.comfy_root.editingFinished.connect(self._save_comfy_fields)
        form.addWidget(_labeled("Install folder", self.comfy_root))
        sizes = QHBoxLayout()
        self.comfy_width = QSpinBox()
        self.comfy_width.setRange(0, 2048)
        self.comfy_width.setSingleStep(64)
        self.comfy_width.setSpecialValueText("auto")
        self.comfy_width.setValue(int(brains.get("comfyui_width") or 0))
        self.comfy_width.valueChanged.connect(self._save_comfy_fields)
        self.comfy_height = QSpinBox()
        self.comfy_height.setRange(0, 2048)
        self.comfy_height.setSingleStep(64)
        self.comfy_height.setSpecialValueText("auto")
        self.comfy_height.setValue(int(brains.get("comfyui_height") or 0))
        self.comfy_height.valueChanged.connect(self._save_comfy_fields)
        self.comfy_steps = QSpinBox()
        self.comfy_steps.setRange(0, 80)
        self.comfy_steps.setSpecialValueText("auto")
        self.comfy_steps.setValue(int(brains.get("comfyui_steps") or 0))
        self.comfy_steps.valueChanged.connect(self._save_comfy_fields)
        sizes.addWidget(_labeled("Width", self.comfy_width))
        sizes.addWidget(_labeled("Height", self.comfy_height))
        sizes.addWidget(_labeled("Steps", self.comfy_steps))
        form.addLayout(sizes)
        layout.addWidget(card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("page")
        models_l = QVBoxLayout(inner)
        models_l.setContentsMargins(0, 0, 8, 8)
        models_l.setSpacing(4)
        models_l.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        self._host_ui["comfyui"] = {
            "status": status,
            "toggle": toggle,
            "launch": launch,
            "stop": stop,
            "models": models_l,
        }
        return page

    def _save_comfy_fields(self) -> None:
        from .comfyui import _forget_origin

        save_brains_settings(
            {
                "comfyui_url": self.comfy_url.text().strip(),
                "comfyui_root": self.comfy_root.text().strip(),
                "comfyui_width": int(self.comfy_width.value()),
                "comfyui_height": int(self.comfy_height.value()),
                "comfyui_steps": int(self.comfy_steps.value()),
            }
        )
        _forget_origin()

    def _paint_hub_cards(self) -> None:
        from .gguf_browser import PAGE_SIZE, paginate

        self._live_bars = {}
        self._live_ticks = {}

        layout = getattr(self, "_browser_results", None)
        if layout is None:
            return
        self._browser_repo = ""
        self._browser_files = []
        self._reset_layout(layout)
        if not self._hub_all:
            empty = QLabel("No GGUF repos from this search. Try gemma, qwen2.5-vl, or ministral.")
            empty.setObjectName("muted")
            layout.addWidget(empty)
            layout.addStretch(1)
            return
        visible = self._visible_hub_cards()
        if not visible:
            empty = QLabel(
                "No GGUFs match the filters. Turn off Recommended, Fully GPU load, Has Vision, "
                "or Has Tools Calling to see more of this search."
            )
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            layout.addStretch(1)
            return
        page_rows, page, total = paginate(visible, self._hub_page, size=PAGE_SIZE)
        self._hub_page = page
        grid_host = QWidget()
        grid = FlowLayout(grid_host, spacing=8, columns=2)
        for item in page_rows:
            grid.addWidget(self._hub_card(item))
        layout.addWidget(grid_host)
        pager = QHBoxLayout()
        prev = chip_button("Previous", lambda: self._hub_turn(-1), tip="Previous page")
        nxt = chip_button("Next", lambda: self._hub_turn(1), tip="Next page")
        prev.setEnabled(page > 0)
        nxt.setEnabled(page < total - 1)
        mark = QLabel(f"Page {page + 1} of {total} · {len(visible)} models")
        mark.setObjectName("muted")
        pager.addWidget(prev)
        pager.addWidget(mark, stretch=1)
        pager.addWidget(nxt)
        wrap = QWidget()
        wrap.setLayout(pager)
        layout.addWidget(wrap)
        layout.addStretch(1)

    def _hub_turn(self, delta: int) -> None:
        self._hub_page = max(0, self._hub_page + int(delta))
        self._paint_hub_cards()

    def _hub_card(self, item) -> QFrame:
        from html import escape

        from .gguf_browser import is_recommended, repo_page
        from .lm_runtime import format_size

        card = QFrame()
        card.setObjectName("settingsCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(6)
        url = repo_page(item.repo)
        title = QLabel(f'<a href="{escape(url, quote=True)}">{escape(item.repo)}</a>')
        title.setObjectName("modelName")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setOpenExternalLinks(True)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        title.setWordWrap(True)
        title.setToolTip(url)
        col.addWidget(title)
        meta_bits = [item.author] if item.author else []
        if item.downloads:
            meta_bits.append(f"{item.downloads:,} downloads")
        if item.likes:
            meta_bits.append(f"{item.likes:,} likes")
        if item.pipeline:
            meta_bits.append(item.pipeline)
        meta = QLabel(" · ".join(meta_bits) or "GGUF")
        meta.setObjectName("muted")
        meta.setWordWrap(True)
        col.addWidget(meta)
        blurb = str(item.blurb or "").strip()
        if blurb and blurb.lower() not in {item.repo.lower(), item.title.lower(), "gguf"}:
            desc = QLabel(blurb)
            desc.setObjectName("muted")
            desc.setWordWrap(True)
            col.addWidget(desc)
        rec = item.rec
        quant = rec.quant if rec is not None else ""
        size = format_size(rec.size_bytes) if rec is not None else ""
        if item.vram_gb:
            vram = (
                f"~{item.vram_gb:.1f} GB VRAM for {quant or 'recommended quant'}"
                + (f" ({size} file)" if size else "")
            )
            vram += " · fits this GPU" if item.fits_gpu else " · larger than 70% of this GPU"
        else:
            vram = "VRAM unknown until a GGUF file lists a size"
        need = QLabel(vram)
        need.setObjectName("muted")
        need.setWordWrap(True)
        col.addWidget(need)
        if item.quants:
            q = QLabel("Quants: " + ", ".join(item.quants[:8]))
            q.setObjectName("muted")
            q.setWordWrap(True)
            col.addWidget(q)
        pills = QHBoxLayout()
        pills.setSpacing(6)
        if item.trusted:
            pills.addWidget(Pill("Trusted", "loaded"))
        if is_recommended(item):
            pills.addWidget(Pill("Recommended", "chat"))
        if item.gated:
            pills.addWidget(Pill("Gated", "cap"))
        if item.has_vision:
            pills.addWidget(Pill("Vision", "chat"))
        if item.has_tools:
            pills.addWidget(Pill("Tools", "cap"))
        if item.fits_gpu:
            pills.addWidget(Pill("GPU load", "loaded"))
        pills.addStretch(1)
        col.addLayout(pills)
        if item.gated:
            hint = QLabel("Needs a Hugging Face token, then accept the license on the Hub.")
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            col.addWidget(hint)
        rec_name = rec.filename if rec is not None else ""
        spec = f"{item.repo}#{rec_name}" if rec_name else item.repo
        installed = bool(rec_name) and self._local_has(Path(rec_name).stem)
        pulling = self._is_pulling("local", spec)
        if pulling:
            payload = dict(self._pull_payload or {})
            bar = QProgressBar()
            bar.setObjectName("pullBar")
            bar.setTextVisible(False)
            if payload.get("indeterminate"):
                bar.setRange(0, 0)
            else:
                bar.setRange(0, 100)
                bar.setValue(int(payload.get("percent") or 0))
            col.addWidget(bar)
            tick = QLabel(str(payload.get("text") or "Downloading…"))
            tick.setObjectName("muted")
            tick.setWordWrap(True)
            col.addWidget(tick)
            self._live_bars[spec] = bar
            self._live_ticks[spec] = tick
        queued = self._is_queued("local", spec)
        interrupted = self._interrupted_has(spec)
        col.addStretch(1)
        actions = QHBoxLayout()
        if installed:
            actions.addWidget(Pill("Installed", "loaded"))
            actions.addWidget(
                chip_button(
                    "Use",
                    lambda n=Path(rec_name).stem: self._pick_chat("local", n),
                    primary=True,
                )
            )
        elif pulling:
            actions.addWidget(Pill("Pulling", "cap"))
            actions.addWidget(
                icon_button(
                    "close",
                    "Cancel download",
                    lambda: self._cancel_pull("local"),
                    danger=True,
                    size=28,
                )
            )
        elif queued:
            actions.addWidget(Pill("Queued", "cap"))
            actions.addWidget(
                chip_button(
                    "Remove",
                    lambda n=spec: self._dequeue("local", n),
                    tip="Remove from the download queue",
                )
            )
        elif interrupted:
            actions.addWidget(Pill("Interrupted", "cap"))
            actions.addWidget(
                icon_button(
                    "redo",
                    "Retry download",
                    lambda s=spec: self._start_pull("local", s),
                    size=28,
                )
            )
        else:
            actions.addWidget(
                chip_button(
                    f"Pull {quant}" if quant else "Pull",
                    lambda s=spec: self._start_pull("local", s),
                    primary=True,
                    tip="Download the recommended quant into the Local folder",
                )
            )
        actions.addWidget(
            chip_button(
                "All quants",
                lambda repo=item.repo: self._open_family(repo),
                tip="List every GGUF in this repo",
            )
        )
        actions.addWidget(
            chip_button(
                "Open Hub",
                lambda repo=item.repo: self._open_hub_repo(repo),
                tip="Open this repo on huggingface.co",
            )
        )
        if item.gated:
            actions.addWidget(
                chip_button(
                    "Accept license",
                    lambda repo=item.repo: self._open_hub_repo(repo),
                    tip="Open the Hugging Face page and accept the license, then pull again",
                )
            )
        actions.addStretch(1)
        col.addLayout(actions)
        return card

    def _paint_browser_files(self, repo: str, files: list) -> None:
        layout = getattr(self, "_browser_results", None)
        if layout is None:
            return
        self._reset_layout(layout)
        back = QHBoxLayout()
        title = QLabel(repo)
        title.setObjectName("hostTitle")
        back.addWidget(title, stretch=1)
        back.addWidget(
            chip_button(
                "Open Hub",
                lambda r=repo: self._open_hub_repo(r),
                tip="Open this repo on huggingface.co",
            )
        )
        gated = any(item.repo == repo and item.gated for item in (self._hub_all or []))
        if gated:
            back.addWidget(
                chip_button(
                    "Accept license",
                    lambda r=repo: self._open_hub_repo(r),
                    tip="Open the Hugging Face page and accept the license",
                )
            )
        back.addWidget(chip_button("Back", lambda: self._paint_hub_cards(), tip="Back to Hub cards"))
        wrap = QWidget()
        wrap.setLayout(back)
        layout.addWidget(wrap)
        chat = [item for item in files if not item.is_mmproj]
        if not chat:
            empty = QLabel("No GGUF weights in this repo.")
            empty.setObjectName("muted")
            layout.addWidget(empty)
            layout.addStretch(1)
            return
        for item in chat:
            row = QFrame()
            row.setObjectName("modelRow")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(10, 5, 8, 5)
            lay.setSpacing(6)
            label = QLabel(item.filename)
            label.setObjectName("modelName")
            lay.addWidget(label, stretch=1)
            if item.quant:
                q = QLabel(item.quant)
                q.setObjectName("muted")
                lay.addWidget(q)
            size = format_size(item.size_bytes)
            if size:
                meta = QLabel(size)
                meta.setObjectName("muted")
                lay.addWidget(meta)
            if item.recommended:
                lay.addWidget(Pill("Fits VRAM", "loaded"))
            key = f"{repo}#{item.filename}"
            installed = self._local_has(item.filename.replace(".gguf", ""))
            pulling = self._is_pulling("local", key)
            queued = self._is_queued("local", key)
            interrupted = self._interrupted_has(key)
            if installed:
                stem = Path(item.filename).stem
                lay.addWidget(Pill("Installed", "loaded"))
                lay.addWidget(
                    chip_button(
                        "Use",
                        lambda n=stem: self._pick_chat("local", n),
                        primary=True,
                    )
                )
            elif pulling:
                lay.addWidget(Pill("Pulling", "cap"))
                lay.addWidget(
                    icon_button(
                        "close",
                        "Cancel download",
                        lambda: self._cancel_pull("local"),
                        danger=True,
                        size=24,
                    )
                )
            elif queued:
                lay.addWidget(Pill("Queued", "cap"))
                lay.addWidget(
                    chip_button(
                        "Remove",
                        lambda n=key: self._dequeue("local", n),
                        tip="Remove from the download queue",
                    )
                )
            elif interrupted:
                lay.addWidget(Pill("Interrupted", "cap"))
                lay.addWidget(
                    icon_button(
                        "redo",
                        "Retry download",
                        lambda s=key: self._start_pull("local", s),
                        size=24,
                    )
                )
            else:
                spec = f"{repo}#{item.filename}"
                lay.addWidget(
                    chip_button(
                        "Pull",
                        lambda s=spec: self._start_pull("local", s),
                        primary=True,
                        tip="Download into the Local folder",
                    )
                )
            layout.addWidget(row)
        layout.addStretch(1)

    def _refresh_browser_pills(self) -> None:
        repo = getattr(self, "_browser_repo", "")
        files = getattr(self, "_browser_files", None) or []
        if repo and files:
            self._paint_browser_files(repo, files)
            return
        if self._hub_all:
            self._paint_hub_cards()

    def _paint_host(self, kind: str, snap: HostSnapshot) -> None:
        from .api_access import is_cloud_host

        if is_cloud_host(kind):
            return
        ui = self._host_ui.get(kind)
        if ui is None:
            return
        brains = brains_settings()
        enabled_key = "comfyui_enabled" if kind == "comfyui" else f"{kind}_enabled"
        enabled = bool(brains.get(enabled_key, True))
        toggle: IconToggle = ui["toggle"]
        toggle.blockSignals(True)
        toggle.setChecked(enabled)
        toggle.blockSignals(False)
        status: QLabel = ui["status"]
        if snap.online:
            count = len(snap.models)
            if kind == "comfyui":
                status.setText(f"{_dot(True)} {count} checkpoints · {comfy_display_origin()}")
            else:
                loaded = ", ".join(snap.loaded_names) or "none loaded"
                status.setText(f"{_dot(True)} {count} · {loaded}")
        else:
            err = (snap.error or "offline").splitlines()[0][:56]
            status.setText(f"{_dot(False)} {err}")
        launch = ui.get("launch")
        if launch is not None:
            launch.setVisible(not snap.online)
        stop = ui.get("stop")
        if stop is not None:
            owned = owned_by_jinx() if kind == "comfyui" else llama_owned()
            stop.setVisible(bool(snap.online and owned))

        models: QVBoxLayout = ui["models"]
        self._reset_layout(models)
        if not snap.models:
            empty = QLabel(
                "No checkpoints yet. Put .safetensors in the Comfy models folder."
                if kind == "comfyui"
                else "No GGUFs yet. Pull one from Hugging Face Hub."
            )
            empty.setObjectName("muted")
            models.addWidget(empty)
        for model in snap.models:
            models.addWidget(self._model_row(kind, model, brains))
        models.addStretch(1)

    def _paint_cloud_models(self) -> None:
        from .api_access import CLOUD_HOSTS, has_api_key

        layout = self._cloud_models
        self._reset_layout(layout)
        brains = brains_settings()
        any_key = False
        for kind in CLOUD_HOSTS:
            if not has_api_key(kind):
                continue
            any_key = True
            snap = self._snaps.get(kind) or HostSnapshot(kind=kind)
            box = QFrame()
            box.setObjectName("hostCard")
            col = QVBoxLayout(box)
            col.setContentsMargins(12, 10, 12, 10)
            col.setSpacing(6)
            head = QHBoxLayout()
            title = QLabel(host_label(kind))
            title.setObjectName("hostTitle")
            head.addWidget(title)
            state = QLabel(
                f"{_dot(True)} {len(snap.models)} models"
                if snap.online
                else f"{_dot(False)} {(snap.error or 'offline')[:48]}"
            )
            state.setObjectName("muted")
            state.setTextFormat(Qt.TextFormat.RichText)
            head.addWidget(state, stretch=1)
            col.addLayout(head)
            if not snap.models:
                empty = QLabel("Key saved. Models appear when the API answers.")
                empty.setObjectName("muted")
                col.addWidget(empty)
            for model in snap.models:
                col.addWidget(self._model_row(kind, model, brains))
            layout.addWidget(box)
        if not any_key:
            empty = QLabel("Add a DeepSeek or Google AI Studio key above to list cloud models.")
            empty.setObjectName("muted")
            layout.addWidget(empty)

    def _model_row(self, kind: str, model: LmModel, brains: dict) -> QFrame:
        from .api_access import is_cloud_host

        row = QFrame()
        row.setObjectName("modelRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 5, 8, 5)
        lay.setSpacing(6)
        name = QLabel(model.name)
        name.setObjectName("modelName")
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(name, stretch=1)
        size = format_size(model.size_bytes)
        if size:
            meta = QLabel(size)
            meta.setObjectName("muted")
            lay.addWidget(meta)
        caps = format_capabilities(model.capabilities)
        if caps:
            cap = QLabel(caps)
            cap.setObjectName("muted")
            lay.addWidget(cap)
        if model.loaded:
            lay.addWidget(Pill("Loaded", "loaded"))
        if brains.get("chat_host") == kind and brains.get("chat_model") == model.name:
            lay.addWidget(Pill("Chat", "chat"))
        if brains.get("coding_host") == kind and brains.get("coding_model") == model.name:
            lay.addWidget(Pill("Code", "chat"))
        if brains.get("memory_host") == kind and brains.get("memory_model") == model.name:
            lay.addWidget(Pill("Memory", "cap"))
        if brains.get("image_host") == kind and brains.get("image_model") == model.name:
            lay.addWidget(Pill("Image", "cap"))
        if kind == "comfyui":
            lay.addWidget(
                chip_button(
                    "Images",
                    lambda host=kind, n=model.name: self._pick_image(host, n),
                    primary=True,
                    tip="Use this checkpoint for generate_image",
                )
            )
            return row
        if not is_cloud_host(kind):
            if model.loaded:
                lay.addWidget(
                    chip_button(
                        "Unload",
                        lambda host=kind, n=model.name: self._run_action("unload", host, n),
                        tip="Unload this model from VRAM",
                    )
                )
            else:
                lay.addWidget(
                    chip_button(
                        "Load",
                        lambda host=kind, n=model.name: self._run_action("load", host, n),
                        tip="Load this model into VRAM",
                    )
                )
            lay.addWidget(
                chip_button(
                    "Delete",
                    lambda host=kind, n=model.name: self._confirm_delete(host, n),
                    danger=True,
                    tip="Remove this model from the provider",
                )
            )
        lay.addWidget(
            chip_button(
                "Use",
                lambda host=kind, n=model.name: self._pick_chat(host, n),
                primary=True,
                tip="Use this model in chat",
            )
        )
        return row

    def _on_enabled(self, kind: str, on: bool) -> None:
        from .api_access import is_cloud_host, save_api_access

        if is_cloud_host(kind):
            save_api_access({f"{kind}_enabled": bool(on)})
        else:
            key = "comfyui_enabled" if kind == "comfyui" else f"{kind}_enabled"
            save_brains_settings({key: bool(on)})
        self.host_enabled.emit(kind, bool(on))
        self._paint_banners()
        self._paint_roles()

    def _pick_chat(self, kind: str, name: str) -> None:
        save_brains_settings({"chat_host": kind, "chat_model": name})
        self.use_chat.emit(kind, name)
        self._paint_banners()
        self._paint_roles()
        snap = self._snaps.get(kind)
        if snap is not None:
            self._paint_host(kind, snap)
        self._paint_cloud_models()

    def _pick_image(self, kind: str, name: str) -> None:
        save_brains_settings({"image_host": kind, "image_model": name})
        self.use_image.emit(kind, name)
        self._paint_banners()
        self._paint_roles()
        self.status.emit(f"Image slot: {name} via ComfyUI. Chat model stays loaded.")
        snap = self._snaps.get(kind)
        if snap is not None:
            self._paint_host(kind, snap)

    def _launch(self, kind: str) -> None:
        from .api_access import is_cloud_host

        if is_cloud_host(kind):
            self.status.emit("Cloud hosts do not launch. Add a key on Cloud API.")
            return
        if self._action is not None and self._action.isRunning():
            self.status.emit("Wait — still talking to the local server.")
            return
        label = "ComfyUI" if kind == "comfyui" else "llama-server"
        self.status.emit(f"Starting {label}…")

        def work() -> str:
            if kind == "comfyui":
                return launch_comfyui()
            return launch_host(kind)

        job = _Job(work, self)
        self._action = job
        job.done.connect(lambda msg, host=kind: self._on_launch_done(host, str(msg)))
        job.failed.connect(self._on_action_failed)
        job.finished.connect(lambda: self._clear_action(job))
        job.start()

    def _on_launch_done(self, kind: str, msg: str) -> None:
        self.status.emit(msg + " — checking again in a few seconds.")
        delay = 12000 if kind == "comfyui" else 2500
        QTimer.singleShot(delay, self.refresh)
        if kind == "comfyui":
            QTimer.singleShot(30000, self.refresh)

    def _stop_comfy(self) -> None:
        try:
            msg = stop_comfyui()
        except Exception as exc:  # noqa: BLE001
            self.status.emit(str(exc))
            return
        self.status.emit(msg)
        QTimer.singleShot(400, self.refresh)

    def _pull_typed(self, kind: str) -> None:
        ui = self._host_ui.get(kind) or {}
        field: QLineEdit | None = ui.get("field")
        name = field.text().strip() if field is not None else ""
        if not name:
            self.status.emit("Type a model name first.")
            return
        if field is not None:
            field.clear()
        self._start_pull(kind, name)

    def _is_pulling(self, kind: str, name: str) -> bool:
        return model_names_match(self._pulling.get(kind, ""), name)

    def _is_queued(self, kind: str, name: str) -> bool:
        return any(model_names_match(item, name) for item in self._pull_queue.get(kind) or [])

    def _start_pull(self, kind: str, name: str) -> None:
        host = "local"
        del kind
        current = self._pulling.get(host, "")
        queued = list(self._pull_queue.get(host) or [])
        self._installed_keys = None
        self._interrupted_keys = None
        from .downloads import discard_incomplete, looks_like_hf_spec, record_download, resolve_download_spec
        from .local_models import models_dir

        name = resolve_download_spec(name)
        discard_incomplete(name)
        if not looks_like_hf_spec(name):
            err = (
                f"Don't know which Hugging Face repo {name} came from. "
                "Open AI Models → Hugging Face Hub and Pull it again."
            )
            record_download(name, status="failed", folder=str(models_dir()), error=err)
            self.status.emit(err)
            self._emit_pull()
            self._paint_downloads()
            return
        if self._local_has(name):
            from .lm_runtime import gguf_stem

            self.status.emit(f"{gguf_stem(name) or name} is already in the Local folder.")
            self._refresh_browser_pills()
            return
        status, queued = enqueue_download(current, queued, name)
        if status == "empty":
            self.status.emit("Type a model name first.")
            return
        if status == "duplicate":
            label = "pulling" if self._is_pulling(host, name) else "queued"
            self.status.emit(f"{name} is already {label}.")
            return
        if status == "queued":
            from .downloads import record_download
            from .local_models import models_dir

            self._pull_queue[host] = queued
            record_download(name, status="queued", folder=str(models_dir()))
            waiting = len(queued)
            self.status.emit(f"Queued {name} — {waiting} waiting after {current}.")
            self._paint_queue(host)
            self._refresh_browser_pills()
            self._emit_pull()
            return
        self._run_pull(host, name.strip())

    def _run_pull(self, kind: str, name: str) -> None:
        from .downloads import record_download
        from .local_models import models_dir

        self._pulling[kind] = name
        self._interrupted_keys = None
        record_download(name, status="downloading", folder=str(models_dir()))
        self._show_pull(kind, name, {"text": "Starting…", "percent": 0, "indeterminate": True})
        self._paint_queue(kind)
        self.status.emit(f"Pulling {name} — this can take a while.")
        self._refresh_browser_pills()

        def work(on_progress, abort) -> str:
            return pull_model_stream(name, on_progress=on_progress, abort=abort)

        job = _StreamJob(work, self)
        self._streams[kind] = job
        job.progress.connect(lambda payload, host=kind, n=name: self._show_pull(host, n, payload))
        job.done.connect(lambda msg, host=kind, n=name: self._on_pull_done(host, n, str(msg)))
        job.failed.connect(lambda err, host=kind, n=name: self._on_pull_failed(host, n, str(err)))
        job.finished.connect(lambda j=job, host=kind: self._clear_stream(host, j))
        job.start()

    def _kick_queue(self, kind: str) -> None:
        from .downloads import looks_like_hf_spec, record_download, resolve_download_spec
        from .local_models import models_dir

        queued = list(self._pull_queue.get(kind) or [])
        if not queued:
            self._paint_queue(kind)
            return
        nxt = resolve_download_spec(queued.pop(0))
        self._pull_queue[kind] = queued
        if not looks_like_hf_spec(nxt):
            err = (
                f"Don't know which Hugging Face repo {nxt} came from. "
                "Open AI Models → Hugging Face Hub and Pull it again."
            )
            record_download(nxt, status="failed", folder=str(models_dir()), error=err)
            self.status.emit(err)
            self._kick_queue(kind)
            self._emit_pull()
            self._paint_downloads()
            return
        self._run_pull(kind, nxt)

    def _dequeue(self, kind: str, name: str) -> None:
        host = "local"
        del kind
        before = list(self._pull_queue.get(host) or [])
        after = dequeue_download(before, name)
        self._pull_queue[host] = after
        if after != before:
            self.status.emit(f"Removed {name} from the queue.")
        self._paint_queue(host)
        self._refresh_browser_pills()
        self._emit_pull()

    def _show_pull(self, kind: str, name: str, payload: object) -> None:
        ui = self._host_ui.get(kind)
        data = payload if isinstance(payload, dict) else {}
        self._pull_payload = {"name": name, **data}
        self._emit_pull()
        self._touch_live_progress()
        if ui is None:
            return
        strip: QFrame = ui["strip"]
        strip.setVisible(True)
        waiting = len(self._pull_queue.get(kind) or [])
        extra = f" · {waiting} waiting" if waiting else ""
        ui["pull_title"].setText(f"Pulling {name}{extra}")
        bar: QProgressBar = ui["bar"]
        if data.get("indeterminate"):
            bar.setRange(0, 0)
        else:
            bar.setRange(0, 100)
            bar.setValue(int(data.get("percent") or 0))
        ui["pull_meta"].setText(str(data.get("text") or "Working…"))

    def _touch_live_progress(self) -> None:
        current = self._pulling.get("local") or ""
        payload = dict(self._pull_payload or {})
        for spec, bar in list(self._live_bars.items()):
            if not model_names_match(spec, current):
                continue
            try:
                if payload.get("indeterminate"):
                    bar.setRange(0, 0)
                else:
                    bar.setRange(0, 100)
                    bar.setValue(int(payload.get("percent") or 0))
                tick = self._live_ticks.get(spec)
                if tick is not None:
                    tick.setText(str(payload.get("text") or "Downloading…"))
            except RuntimeError:
                self._live_bars.pop(spec, None)
                self._live_ticks.pop(spec, None)

    def _paint_queue(self, kind: str) -> None:
        ui = self._host_ui.get(kind) or {}
        host: QWidget | None = ui.get("queue_host")
        layout: FlowLayout | None = ui.get("queue_layout")
        strip = ui.get("strip")
        queued = list(self._pull_queue.get(kind) or [])
        pulling = bool(self._pulling.get(kind))
        if strip is not None:
            strip.setVisible(pulling or bool(queued))
        title = ui.get("pull_title")
        current = self._pulling.get(kind) or ""
        if title is not None and current:
            extra = f" · {len(queued)} waiting" if queued else ""
            title.setText(f"Pulling {current}{extra}")
        if host is None or layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        if not queued:
            host.setVisible(False)
            return
        label = QLabel("Queued")
        label.setObjectName("muted")
        layout.addWidget(label)
        for name in queued:
            layout.addWidget(
                chip_button(
                    name,
                    lambda n=name, host=kind: self._dequeue(host, n),
                    tip="Remove from the download queue",
                )
            )
        host.setVisible(True)

    def _cancel_pull(self, kind: str) -> None:
        job = self._streams.get(kind)
        if job is not None:
            job.cancel()
            self.status.emit("Cancelling pull…")

    def _clear_stream(self, kind: str, job: _StreamJob) -> None:
        if self._streams.get(kind) is job:
            self._streams.pop(kind, None)
        job.deleteLater()

    def _hide_pull(self, kind: str) -> None:
        if self._pulling.get(kind) or self._pull_queue.get(kind):
            self._paint_queue(kind)
            return
        ui = self._host_ui.get(kind)
        if ui is not None:
            ui["strip"].setVisible(False)

    def _mark_pulled(self, kind: str, name: str) -> None:
        self._pending_models.setdefault(kind, set()).add(name)
        self._installed_keys = None
        snap = self._snaps.get(kind) or HostSnapshot(kind=kind, online=True)
        inject_pulled_model(snap, name)
        self._snaps[kind] = snap
        self._paint_host(kind, snap)
        self._refresh_browser_pills()
        self._paint_banners()

    def _on_pull_done(self, kind: str, name: str, msg: str) -> None:
        from .downloads import record_download

        if self._pulling.get(kind) and not model_names_match(self._pulling.get(kind, ""), name):
            return
        record_download(name, status="done", folder=str(models_dir()))
        self._pulling[kind] = ""
        self._pull_payload = {}
        self._interrupted_keys = None
        self._installed_keys = None
        self._mark_pulled(kind, name)
        self.status.emit(msg)
        self._sync_host(kind)
        QTimer.singleShot(250, lambda host=kind: self._sync_host(host))
        QTimer.singleShot(900, lambda host=kind: self._sync_host(host))
        self._kick_queue(kind)
        self._emit_pull()
        self._paint_downloads()
        if not self._pulling.get(kind):
            self._hide_pull(kind)

    def _on_pull_failed(self, kind: str, _name: str, err: str) -> None:
        from .downloads import record_download

        status = "cancelled" if "cancel" in (err or "").lower() else "failed"
        record_download(_name, status=status, folder=str(models_dir()), error=err)
        if self._pulling.get(kind) and not model_names_match(self._pulling.get(kind, ""), _name):
            self._emit_pull()
            self._paint_downloads()
            return
        self._pulling[kind] = ""
        self._pull_payload = {}
        self._interrupted_keys = None
        self._show_hub_error(err)
        self._refresh_browser_pills()
        self._kick_queue(kind)
        self._emit_pull()
        self._paint_downloads()
        if not self._pulling.get(kind):
            self._hide_pull(kind)

    def _open_hub_repo(self, repo: str) -> None:
        from .gguf_browser import repo_page

        QDesktopServices.openUrl(QUrl(repo_page(repo)))

    def _show_hub_error(self, err: str) -> None:
        self.status.emit(err)
        from .gguf_browser import hub_error_url

        url = hub_error_url(err)
        blob = (err or "").lower()
        if not url or "license" not in blob:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Hugging Face access")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            "This model is gated. Accept the license on Hugging Face, then pull again."
        )
        box.setInformativeText(err)
        open_btn = box.addButton("Accept license", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(url))

    def _confirm_delete(self, kind: str, name: str) -> None:
        answer = QMessageBox.question(
            self,
            "Delete model",
            f"Remove {name} from {host_label(kind)}? This deletes the local files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_action("delete", kind, name)

    def _run_action(self, action: str, kind: str, name: str) -> None:
        if self._action is not None and self._action.isRunning():
            self.status.emit("Wait — still talking to the local server.")
            return
        verbs = {"pull": "Pulling", "load": "Loading", "unload": "Unloading", "delete": "Deleting"}
        verb = verbs.get(action, "Working")
        extra = " — this can take a while." if action in {"pull", "delete"} else "…"
        self.status.emit(f"{verb} {name}{extra}")

        def work() -> str:
            if action == "load":
                return load_model(kind, name)
            if action == "delete":
                return delete_model(kind, name)
            return unload_model(kind, name)

        job = _Job(work, self)
        self._action = job
        job.done.connect(lambda msg, act=action, host=kind, n=name: self._on_action_done(act, host, n, str(msg)))
        job.failed.connect(self._on_action_failed)
        job.finished.connect(lambda: self._clear_action(job))
        job.start()

    def _clear_action(self, job: _Job) -> None:
        if self._action is job:
            self._action = None
        job.deleteLater()

    def _on_action_done(self, action: str, kind: str, name: str, msg: str) -> None:
        self.status.emit(msg)
        if action == "delete" and kind == "local":
            self._pending_models.setdefault(kind, set()).discard(name)
            snap = self._snaps.get(kind)
            if snap is not None:
                snap.models = [item for item in snap.models if not model_names_match(item.name, name)]
                self._paint_host(kind, snap)
                self._refresh_browser_pills()
        if kind == "local":
            self._sync_host(kind)
            QTimer.singleShot(250, lambda host=kind: self._sync_host(host))
            return
        self.refresh()

    def _on_action_failed(self, err: str) -> None:
        self._show_hub_error(err)


def _dot(ok: bool) -> str:
    color = "#34d399" if ok else "#f87171"
    return f'<span style="color:{color};">●</span>'


def _labeled(title: str, widget: QWidget) -> QWidget:
    wrap = QWidget()
    col = QVBoxLayout(wrap)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(4)
    label = QLabel(title)
    label.setObjectName("muted")
    col.addWidget(label)
    col.addWidget(widget)
    wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return wrap
