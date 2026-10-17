import sys

from PyQt6.QtWidgets import QApplication

from jinx_ai.avatar_window import OverlayHud
from jinx_ai.settings_store import look_settings
from jinx_ai.ui_style import APP_QSS
from jinx_ai.widgets import HudButton, PageHeader, Pill, TrafficLights, chip_button


def test_look_settings_include_hide_border() -> None:
    cfg = look_settings({"look": {"transparent_bg": True, "hide_border": True}})
    assert cfg["transparent_bg"] is True
    assert cfg["hide_border"] is True
    assert cfg["orb_style"] == "orb"


def test_qss_has_overlay_scrollbars_not_arrows() -> None:
    assert "QScrollBar:vertical" in APP_QSS
    assert "QScrollBar::add-line:vertical" in APP_QSS
    assert "height: 0" in APP_QSS


def test_reusable_header_pill_and_chip() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    header = PageHeader("Models", "Local brains")
    assert header.layout() is not None
    assert header.layout().count() == 2
    pill = Pill("Loaded", "loaded")
    assert pill.property("kind") == "loaded"
    clicked = []
    btn = chip_button("Use", lambda: clicked.append(1), primary=True)
    assert btn.objectName() == "chipPrimary"
    btn.click()
    assert clicked == [1]


def test_traffic_lights_and_overlay_hud() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    lights = TrafficLights()
    assert lights.close_btn._role == "close"
    assert lights.layout().itemAt(0).widget() is lights.min_btn
    assert lights.layout().itemAt(1).widget() is lights.max_btn
    assert lights.layout().itemAt(2).widget() is lights.close_btn
    hud = OverlayHud()
    assert hud.mute_btn.property("role") == "mute"
    assert hud.close_btn.property("role") == "close"
    assert isinstance(hud.mute_btn, HudButton)
    hud.set_live(False)
    assert hud.mute_btn.property("state") == "muted"
    hud.set_live(True)
    assert hud.mute_btn.property("state") == "live"
    assert hud.call_btn.property("role") == "call"
    assert isinstance(hud.call_btn, HudButton)
    hud.set_in_call(True)
    assert hud.call_btn.property("state") == "live"
    hud.set_in_call(False)
    assert hud.call_btn.property("state") == "idle"


def test_mic_icon_has_ink() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    from jinx_ai.icon import create_activity_icon

    image = create_activity_icon("mic", 24, "#f5f5f7").pixmap(24, 24).toImage()
    ink = sum(
        1
        for x in range(24)
        for y in range(24)
        if image.pixelColor(x, y).alpha() > 40
    )
    assert ink > 20


def test_download_flyout_folder_is_an_icon() -> None:
    from jinx_ai.download_flyout import DownloadFlyout

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    flyout = DownloadFlyout()
    assert flyout._folder.objectName() == "iconBtn"
    assert flyout._cancel.objectName() == "dangerIcon"
    assert not flyout._folder.text()
    flyout.set_snapshot(
        {
            "current": "repo#a.gguf",
            "percent": 6,
            "text": "file · 6%",
            "active": True,
            "queued": ["b.gguf"],
            "recent": [],
        }
    )
    assert flyout._pct.text() == "6%"
    assert not flyout._meta.isVisible()
    flyout.deleteLater()


def test_download_flyout_does_not_lock_a_square() -> None:
    from PyQt6.QtWidgets import QWidget

    from jinx_ai.download_flyout import DownloadFlyout

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    anchor = QWidget()
    flyout = DownloadFlyout()
    flyout.place_below(anchor)
    assert flyout.width() == 320
    assert flyout.minimumHeight() < flyout.minimumWidth()
    flyout.deleteLater()
    anchor.deleteLater()
