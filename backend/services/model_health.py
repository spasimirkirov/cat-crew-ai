"""Are the models the user picked still there?

On start-up (and on demand) every host that a role depends on is asked for its live
model list, and each role's saved model is checked against it. A role is a problem
when its model

* is **missing**: the host answered and the model is not in its list (retired,
  deprecated, renamed, deleted),
* sits on a host that is **offline** (no network, a bad key, a host that is down),
* needs an API key that is **no_key**, or
* is on a host that is **disabled**.

Roles that inherit Chat (nothing picked) are not checked; Chat itself is checked
only when a model is chosen. The report is plain data with a ready-to-show sentence
per problem, so the UI needs no logic of its own.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.services import model_discovery, settings_store
from backend.services.api_access import has_api_key, key_enabled
from backend.services.lm_runtime import HostSnapshot, host_label, snapshot_has_model

# (wire name, settings role, label). Images run on ComfyUI, which has no model list to check.
CHECKED_ROLES: tuple[tuple[str, str, str], ...] = (
    ("chat", "companion", "Chat"),
    ("coding", "coding", "Coding"),
    ("memory", "memory", "Memory"),
    ("vision", "vision", "Vision"),
    ("planning", "planning", "Planning"),
    ("review", "review", "Review"),
)

REPORT_TTL_S = 60.0


@dataclass
class Issue:
    role: str  # wire name: "coding"
    role_label: str  # "Coding"
    host: str
    host_label: str
    model: str
    reason: str  # "missing" | "offline" | "no_key" | "disabled"
    message: str


@dataclass
class HostState:
    online: bool
    error_kind: str
    model_count: int


@dataclass
class Report:
    checked_at: float = 0.0
    hosts: dict[str, HostState] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "ok": self.ok,
            "hosts": {name: asdict(state) for name, state in self.hosts.items()},
            "issues": [asdict(issue) for issue in self.issues],
        }


def _message(reason: str, role_label: str, host: str, model: str) -> str:
    label = host_label(host)
    if reason == "missing":
        return f"The model '{model}' assigned to '{role_label}' is no longer available on {label}."
    if reason == "no_key":
        return f"The model '{model}' assigned to '{role_label}' needs a {label} API key, and none is set."
    if reason == "disabled":
        return f"The model '{model}' assigned to '{role_label}' is on {label}, which is turned off."
    return f"The model '{model}' assigned to '{role_label}' cannot be reached: {label} is offline."


def assignments() -> list[tuple[str, str, str, str]]:
    """(wire role, label, host, model) for every checked role that has a model picked."""
    out: list[tuple[str, str, str, str]] = []
    for wire, role, label in CHECKED_ROLES:
        host, model = settings_store.saved_role_brain(role)
        if host and model:
            out.append((wire, label, host, model))
    return out


def _blocked(host: str) -> str:
    """Why a host cannot be used at all (``"disabled"`` / ``"no_key"``), or ``""`` when it can be asked."""
    if not has_api_key(host):
        return "no_key"
    return "" if key_enabled(host) else "disabled"


def check(*, force: bool = True) -> Report:
    """Probe every host a role uses (in parallel) and compare. Never raises."""
    picked = assignments()
    blocked = {host: _blocked(host) for host in {host for _w, _l, host, _m in picked}}
    hosts = sorted(host for host, why in blocked.items() if not why)
    snaps: dict[str, HostSnapshot] = {}
    if hosts:
        with ThreadPoolExecutor(max_workers=len(hosts), thread_name_prefix="catcrew-health") as pool:
            for host, snap in zip(hosts, pool.map(lambda h: model_discovery.cached_snapshot(h, force=force), hosts)):
                snaps[host] = snap
    report = Report(checked_at=time.time())
    for host, snap in snaps.items():
        report.hosts[host] = HostState(snap.online, snap.error_kind, len(snap.models))
    for wire, label, host, model in picked:
        snap = snaps.get(host)
        reason = blocked.get(host) or ""
        if not reason and snap is not None:
            if snap.error_kind == "no_key":
                reason = "no_key"
            elif not snap.online:
                reason = "offline"  # includes a rejected key: the host is not usable either way
            elif not snapshot_has_model(snap, model):
                reason = "missing"
        if reason:
            report.issues.append(Issue(wire, label, host, host_label(host), model, reason, _message(reason, label, host, model)))
    return report


_lock = threading.Lock()  # guards _last
_check_lock = threading.Lock()  # one check at a time: a second caller reuses the first one's result
_last: Report | None = None
# Checks started so far, and which one produced ``_last``. A refresh may reuse a check that
# *started* after it asked. Comparing wall-clock times did not work: on Windows time.time() can
# return the same value twice in a row, and a refresh then got the stale report back.
_started = 0
_last_seq = 0


def _fresh(report: Report | None) -> bool:
    return report is not None and time.time() - report.checked_at <= REPORT_TTL_S


def current(*, refresh: bool = False) -> Report:
    """The latest report; a new check when there is none, it is old, or ``refresh`` is set."""
    global _last, _started, _last_seq
    with _lock:
        report = _last
        asked = _started
    if not refresh and _fresh(report):
        return report  # type: ignore[return-value]
    with _check_lock:
        with _lock:
            report, seq = _last, _last_seq
        # Someone else may have finished a check while this call waited for the lock.
        if report is not None and (seq > asked if refresh else _fresh(report)):
            return report
        with _lock:
            _started += 1
            mine = _started
        report = check()
        with _lock:
            _last, _last_seq = report, mine
        return report


def invalidate() -> None:
    """Assignments or keys changed: the next ``current()`` re-checks."""
    global _last
    with _lock:
        _last = None


def start_background_check() -> threading.Thread:
    """Warm the report at start-up without delaying it: the UI's first ask then answers from cache."""

    def _run() -> None:
        try:
            current(refresh=True)
        except Exception as exc:  # noqa: BLE001 - never let a probe take the engine down
            print(f"[models] start-up check failed: {exc}", flush=True)

    thread = threading.Thread(target=_run, name="catcrew-model-check", daemon=True)
    thread.start()
    return thread
