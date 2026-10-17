from datetime import datetime, timezone

from jinx_ai.chat_transcript import bubble_cap, measure_card_text, parse_when, prepare_markdown


def test_duplicate_markdown_link_uses_host() -> None:
    url = "https://steamcommunity.com/id/spasimirkirov"
    raw = f"Profile: [{url}]({url})"
    out = prepare_markdown(raw)
    assert out == f"Profile: [steamcommunity.com]({url})"
    assert f"[{url}]({url})" not in out


def test_bare_url_is_autolinked() -> None:
    url = "https://example.com/path"
    out = prepare_markdown(f"See {url} please")
    assert f"[example.com]({url})" in out


def test_code_fence_urls_stay_raw() -> None:
    raw = "```\nhttps://example.com/secret\n```"
    assert prepare_markdown(raw) == raw


def test_mojibake_apostrophes_are_repaired() -> None:
    from jinx_ai.activity import repair_utf8_text

    broken = "let" + "â" + "\x80\x99" + "s"
    assert repair_utf8_text(broken) == "let’s"
    assert "let’s" in prepare_markdown(broken)


def test_bubble_cap_is_half_the_transcript() -> None:
    assert bubble_cap(800) == 400
    assert bubble_cap(200) == 220


def test_parse_when_uses_local_clock() -> None:
    when = parse_when("2026-09-09T15:58:00+00:00")
    assert when is not None
    assert when.tzinfo is not None
    assert parse_when("") is None
    utc = datetime(2026, 9, 9, 15, 58, tzinfo=timezone.utc)
    assert parse_when(utc.isoformat()).hour == when.hour


def test_short_card_is_narrower_than_the_cap() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    width, height = measure_card_text("Hi", 400)
    assert width < 200
    assert height < 60


def test_live_bubble_starts_readable_not_a_sliver() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    from jinx_ai.chat_transcript import ChatBubble

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    bubble = ChatBubble("assistant", "")
    bubble.set_cap(400)
    bubble.set_text("Sure", markdown=False, relayout=False)
    bubble._fit()
    assert bubble.width() >= 240
    assert bubble.maximumWidth() == 400


def test_streamed_bubble_can_grow_after_first_chunk() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    from jinx_ai.chat_transcript import ChatBubble

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    bubble = ChatBubble("assistant", "Sure! Let")
    bubble.set_cap(400)
    bubble._fit()
    first = bubble.width()
    bubble.set_text(
        "Sure! Let me search the web for the latest weather forecast.",
        markdown=False,
        relayout=False,
    )
    bubble._fit()
    assert bubble.width() > first
    assert bubble.width() >= 240
    assert bubble.maximumWidth() == 400


def test_bubble_hides_clock_without_a_stamp() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    from jinx_ai.chat_transcript import ChatBubble

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    blank = ChatBubble("user", "old", when=None)
    assert blank.stamp.isHidden()
    assert blank.stamp.text() == ""
    stamped = ChatBubble(
        "user",
        "new",
        when=datetime(2026, 9, 9, 18, 1, tzinfo=timezone.utc).astimezone(),
    )
    assert not stamped.stamp.isHidden()
    assert stamped.stamp.text()


def test_assistant_bubble_can_show_chief_name() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    from jinx_ai.chat_transcript import ChatBubble

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    default = ChatBubble("assistant", "Ready")
    assert default.tag.text() == "Jinx AI"
    chief = ChatBubble("assistant", "Delegated to Ekko.", speaker="Chief of Staff")
    assert chief.tag.text() == "Chief of Staff"


def test_format_work_duration() -> None:
    from jinx_ai.activity import format_work_duration

    assert format_work_duration(9) == "9s"
    assert format_work_duration(90) == "1m 30s"
    assert format_work_duration(632) == "10m 32s"
    assert format_work_duration(3600) == "1h"


def test_activity_row_finishes_as_worked_for() -> None:
    import sys

    from PyQt6.QtWidgets import QApplication

    from jinx_ai.chat_transcript import ActivityCard

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    card = ActivityCard()
    card.set_status("Delegating to a team", "arcane")
    card.set_tool_preview("delegate_to_team", '{"team_id": "arcane"}', "Jayce ran")
    snap = card.finish(632)
    assert "Worked for 10m 32s" in card.title.text()
    assert not card._open
    assert snap["elapsed_s"] == 632
    assert snap["steps"]
    card._toggle()
    assert card._open
    assert not card.steps_host.isHidden()
    card.deleteLater()
