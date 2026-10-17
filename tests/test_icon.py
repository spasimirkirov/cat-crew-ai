import sys

from PyQt6.QtWidgets import QApplication

from jinx_ai.icon import create_activity_icon


def _image(kind: str, color: str = "#e8e8ed"):
    return create_activity_icon(kind, 24, color).pixmap(24, 24).toImage()


def _ink(image) -> int:
    return sum(
        1
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 40
    )


def test_power_icon_has_both_sides_of_the_ring() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    image = _image("power", "#fb7185")

    def ink(xs: range, ys: range) -> int:
        return sum(
            1
            for x in xs
            for y in ys
            if image.pixelColor(x, y).alpha() > 40
        )

    left = ink(range(0, 10), range(8, 18))
    right = ink(range(14, 24), range(8, 18))
    assert left > 8
    assert right > 8


def test_settings_icon_is_cogs() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    image = _image("settings")
    assert _ink(image) > 40
    from jinx_ai.icon import _fa_name

    assert _fa_name("settings") == "fa5s.cogs"


def test_look_icon_is_a_light_eye_not_a_black_hole() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    image = _image("look")
    assert _ink(image) > 40
    pupil = image.pixelColor(12, 12)
    assert pupil.alpha() > 40
    # Same ink as the rest of the glyph — not a navy punch-through.
    assert max(pupil.red(), pupil.green(), pupil.blue()) > 140
    corner = image.pixelColor(1, 1).alpha()
    assert corner < 20


def test_chat_window_can_stream_live_tokens() -> None:
    from jinx_ai.chat_window import next_reveal, phase_for

    assert next_reveal("", "Hello from Jinx") != ""
    title, _detail = phase_for("web_search")
    assert title


def test_sync_icon_is_not_empty() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    image = _image("sync")
    assert _ink(image) > 40


def test_robot_icon_has_ink() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    image = _image("robot")
    assert _ink(image) > 40


def test_teams_icons_are_font_awesome() -> None:
    from jinx_ai.icon import _fa_name

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    assert _fa_name("users") == "fa5s.users"
    assert _fa_name("crown") == "fa5s.crown"
    assert _fa_name("briefcase") == "fa5s.briefcase"
    for kind in ("users", "crown", "briefcase", "user-tie"):
        assert _ink(_image(kind)) > 20


def test_terminal_and_composer_icons_have_ink() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    for kind in ("terminal", "send", "stop", "bolt", "cloud", "image"):
        assert _ink(_image(kind)) > 20


def test_volume_and_tree_icons_are_font_awesome() -> None:
    from jinx_ai.icon import _fa_name

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    assert _fa_name("volume-up") == "fa5s.volume-up"
    assert _fa_name("volume-mute") == "fa5s.volume-mute"
    assert _fa_name("chevron-down") == "fa5s.chevron-down"
    assert _fa_name("file") == "fa5s.file"
    for kind in ("volume-up", "volume-mute", "chevron-down", "chevron-up", "chevron-right", "file"):
        assert _ink(_image(kind)) > 20


def test_phone_call_icons_are_font_awesome() -> None:
    from jinx_ai.icon import _fa_name

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    assert _fa_name("phone") == "fa5s.phone"
    assert _fa_name("phone-slash") == "fa5s.phone-slash"
    for kind in ("phone", "phone-slash"):
        assert _ink(_image(kind)) > 20


def test_icon_toggle_swaps_glyph_with_state() -> None:
    from jinx_ai.fa import IconToggle

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    btn = IconToggle("volume-up", "volume-mute", "Speech on", "Speech off", checked=True)
    assert btn.isChecked()
    assert "on" in (btn.toolTip() or "").lower()
    on_icon = btn.icon()
    btn.setChecked(False)
    assert not btn.isChecked()
    assert "off" in (btn.toolTip() or "").lower()
    assert btn.icon().cacheKey() != on_icon.cacheKey()
    btn.deleteLater()


def test_session_toolbar_icons_are_distinct() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    kinds = ("plus", "fork", "tree", "pen", "trash")
    images = [_image(kind) for kind in kinds]
    for i, left in enumerate(images):
        for right in images[i + 1 :]:
            assert left != right
