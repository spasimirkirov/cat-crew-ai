"""platform/activity_board.py: the in-memory "what is bot X doing right
now" store behind the sidebar's Live Activity Line + Last active timestamp
(Master Architecture Document Phase 2, Part 2.2)."""

from __future__ import annotations

import time

from backend.platform import activity_board


def test_set_and_get_activity_roundtrips() -> None:
    activity_board.reset()
    assert activity_board.get_activity("ekko") == ("", 0.0)
    activity_board.set_activity("Ekko", "Working: fix src/App.jsx")
    text, ts = activity_board.get_activity("ekko")  # lookup is case-insensitive
    assert text == "Working: fix src/App.jsx"
    assert ts > 0


def test_touch_updates_timestamp_but_not_text() -> None:
    activity_board.reset()
    activity_board.set_activity("caitlyn", "Running QA")
    _, first_ts = activity_board.get_activity("caitlyn")
    time.sleep(0.01)
    activity_board.touch("caitlyn")
    text, second_ts = activity_board.get_activity("caitlyn")
    assert text == "Running QA"
    assert second_ts >= first_ts


def test_mark_idle_sets_a_readable_note() -> None:
    activity_board.reset()
    activity_board.set_activity("catcrew", "Writing theme.css")
    activity_board.mark_idle("catcrew")
    text, ts = activity_board.get_activity("catcrew")
    assert text == "Idle"
    assert ts > 0


def test_humanize_age_buckets() -> None:
    now = time.time()
    assert activity_board.humanize_age(0.0) == "Never active"
    assert activity_board.humanize_age(now) == "Active now"
    assert activity_board.humanize_age(now - 30) == "30s ago"
    assert activity_board.humanize_age(now - 120) == "2m ago"
    assert activity_board.humanize_age(now - 7_200) == "2h ago"
    assert activity_board.humanize_age(now - 172_800) == "2d ago"


def test_blank_bot_id_is_a_no_op() -> None:
    activity_board.reset()
    activity_board.set_activity("", "should not be stored")
    activity_board.touch("")
    assert activity_board.get_activity("") == ("", 0.0)
