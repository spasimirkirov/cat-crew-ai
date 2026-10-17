"""Shared fixtures: a fully isolated backend (temp DB, registry, sessions, settings)."""

from __future__ import annotations

import os

os.environ.setdefault("CATCREW_MEMORY_ENABLED", "0")
os.environ.setdefault("CATCREW_MCP", "0")  # no test starts a real Blender / Unity connection; tests/test_mcp_service.py opts in

import tempfile
from pathlib import Path

import pytest

from backend.db import ledger

# Before anything imports backend.tools: building a prompt opens the ledger (skills index), and
# that must never be the live data/platform/catcrew.db of a running CatCrewAI.
# The `isolated` fixture below still gives each test its own database on top of this.
ledger.DB_PATH = Path(tempfile.mkdtemp(prefix="catcrew-tests-")) / "catcrew.db"
ledger._initialized = False

from backend import config as config_mod  # noqa: E402
from backend.platform import activity_board, bus
from backend.platform import registry as registry_mod
from backend.services import balances, usage_meter
from backend.services import sessions as sessions_mod
from backend.services import settings_store, trace as trace_mod

# The same for data/settings.json: a test that does not ask for `isolated` must still never read the
# user's live settings (a gatekeeper or approval switch turned on there changes what run_agent does,
# and an API key there makes a "unit" test call a real host).
settings_store.SETTINGS_PATH = Path(tempfile.mkdtemp(prefix="catcrew-tests-settings-")) / "settings.json"

# And the same for keys in the environment (a real DEEPSEEK_API_KEY or OPENROUTER_API_KEY on this
# machine, or loaded from .env): every host would count as configured. Tests set the keys they need.
from backend.services import api_access as _api_access  # noqa: E402

for _spec in _api_access.CATALOG:
    for _name in _spec.env_names:
        os.environ.pop(_name, None)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point every persistent store at ``tmp_path`` and reset in-process live state."""
    monkeypatch.setattr(ledger, "DB_PATH", tmp_path / "catcrew.db")
    monkeypatch.setattr(ledger, "_initialized", False)
    monkeypatch.setattr(registry_mod, "STORE_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(registry_mod, "_cache", None)
    monkeypatch.setattr(sessions_mod, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(sessions_mod, "INDEX_PATH", tmp_path / "sessions" / "index.json")
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path / "traces")
    # CATCREW_HOME is the user-overlay root (custom skills, marketplace tools,
    # platform JSON): never let a test write into the real one.
    home = tmp_path / "catcrew_home"
    monkeypatch.setattr(config_mod, "CATCREW_HOME", home)
    monkeypatch.setattr(registry_mod, "CATCREW_HOME", home)
    bus.reset_bus()
    activity_board.reset()
    usage_meter.reset_meter()  # live counters and context gauges are process-wide
    balances.clear_cache()
    yield tmp_path
    activity_board.reset()
    bus.reset_bus()


class Recorder:
    """Collects every event published on the bus."""

    def __init__(self) -> None:
        self.events: list[bus.Event] = []
        bus.get_bus().subscribe(self.events.append)

    def of(self, type_: str) -> list[bus.Event]:
        return [e for e in self.events if e.type == type_]

    def types(self) -> list[str]:
        return [e.type for e in self.events]


@pytest.fixture
def recorder(isolated) -> Recorder:
    return Recorder()
