"""Browser mode's tray icon: open the UI, stop the engine, go away when the engine stops."""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from backend import __main__ as entry
from backend import tray


class FakeIcon:
    instances: list["FakeIcon"] = []

    def __init__(self, name, image, title, menu=None) -> None:
        self.name, self.title, self.menu = name, title, menu
        self.stopped = threading.Event()
        FakeIcon.instances.append(self)

    def run(self) -> None:
        self.stopped.wait(5)

    def stop(self) -> None:
        self.stopped.set()


class FakeItem:
    def __init__(self, text, action, default=False, enabled=True) -> None:
        self.text, self.action, self.default, self.enabled = text, action, default, enabled


@pytest.fixture
def fake_pystray(monkeypatch):
    module = types.ModuleType("pystray")
    module.Icon = FakeIcon
    module.MenuItem = FakeItem
    module.Menu = type("Menu", (), {"SEPARATOR": object(), "__init__": lambda self, *items: setattr(self, "items", items)})
    monkeypatch.setitem(sys.modules, "pystray", module)
    FakeIcon.instances.clear()
    return module


def test_the_menu_opens_the_ui_by_default_and_stop_stops_the_engine(fake_pystray, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(tray.webbrowser, "open", opened.append)
    stopped = threading.Event()
    runner = threading.Thread(target=tray.run, args=("http://127.0.0.1:8765/",), kwargs={"stop_engine": stopped.set, "engine_alive": lambda: True})
    runner.start()
    for _ in range(50):
        if FakeIcon.instances:
            break
        time.sleep(0.02)
    icon = FakeIcon.instances[0]
    items = [i for i in icon.menu.items if isinstance(i, FakeItem)]
    default = next(i for i in items if i.default)
    assert default.text == "Open CatCrewAI"
    default.action(icon, default)
    assert opened == ["http://127.0.0.1:8765/"]
    assert any("Running at http://127.0.0.1:8765/" == i.text and not i.enabled for i in items)
    stop = next(i for i in items if i.text == "Stop CatCrewAI")
    stop.action(icon, stop)
    runner.join(2)
    assert stopped.is_set() and icon.stopped.is_set() and not runner.is_alive()


def test_the_icon_goes_away_when_the_engine_stops_by_itself(fake_pystray) -> None:
    alive = threading.Event()
    alive.set()
    runner = threading.Thread(target=tray.run, args=("http://x/",), kwargs={"stop_engine": lambda: None, "engine_alive": alive.is_set, "poll": 0.05})
    runner.start()
    time.sleep(0.2)
    assert runner.is_alive()
    alive.clear()  # Power off in the UI
    runner.join(2)
    assert not runner.is_alive()


def test_without_a_desktop_it_runs_with_no_icon(monkeypatch) -> None:
    monkeypatch.setattr(tray, "available", lambda: "no tray icon (DisplayNameError)")
    ran: list[str] = []
    server = types.SimpleNamespace(run=lambda: ran.append("run"), should_exit=False)
    entry._run_with_tray(server, "http://x/")
    assert ran == ["run"]


def test_a_second_start_opens_the_running_one(monkeypatch, tmp_path) -> None:
    url_file = tmp_path / "ui.url"
    monkeypatch.setattr(tray, "url_file", lambda: url_file)
    opened: list[str] = []
    monkeypatch.setattr(entry.webbrowser, "open", opened.append)
    assert entry._open_running(True) is False  # nothing running
    url_file.write_text("http://127.0.0.1:8770/")
    assert entry._open_running(False) is False  # not browser mode: the old "already running" error
    assert entry._open_running(True) is True and opened == ["http://127.0.0.1:8770/"]


def test_tray_implies_browser_mode(monkeypatch, tmp_path) -> None:
    from backend.api import ui

    monkeypatch.setattr(ui, "FRONTEND_DIST", tmp_path / "missing")
    assert entry.main(["--tray"]) == 2  # browser mode: it asks for the built UI
