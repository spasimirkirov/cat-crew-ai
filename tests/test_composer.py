import sys

from PyQt6.QtWidgets import QApplication

from jinx_ai.composer import Composer


def test_composer_busy_shows_stop_and_queue() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    box = Composer()
    box.set_busy(True)
    assert not box.stop_button.isHidden()
    assert not box.now_button.isHidden()
    box.set_queue(["fix the npm error"])
    assert not box.queue_host.isHidden()
    box.set_busy(False)
    assert box.stop_button.isHidden()
    assert box.now_button.isHidden()
    assert box.call_toggle.isCheckable()
    assert not box.call_toggle.isChecked()
    box.set_in_call(True)
    assert box.call_toggle.isChecked()
    box.set_in_call(False)
    assert not box.call_toggle.isChecked()
    box.deleteLater()


def test_strip_wake_boilerplate_drops_hey_jinx() -> None:
    from jinx_ai.chat_window import strip_wake_boilerplate

    raw = (
        "Hey — this is a 1:1 with Jayce. Shared folder: D:\\laragon\\www\\team-arcane.\n\n"
        "Say Hey Jinx to talk. Goodbye puts me back to sleep."
    )
    cleaned = strip_wake_boilerplate(raw)
    assert "Jayce" in cleaned
    assert "Hey Jinx" not in cleaned
    assert "Goodbye" not in cleaned
