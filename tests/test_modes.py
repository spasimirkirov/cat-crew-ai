from datetime import datetime

from jinx_ai.sessions import format_session_title
from jinx_ai.settings_store import chat_pane_for
from jinx_ai.tools import ASSISTANT_TOOL_NAMES, tools_schema_for


def test_session_title_is_numeric_and_mode_agnostic() -> None:
    chat = format_session_title("2026-09-07T19:05:00+00:00", mode="assistant")
    dev = format_session_title("2026-09-07T19:05:00+00:00", mode="developer")
    assert chat == dev
    assert chat.startswith("Jinx AI - Session - ")
    stamp = chat.removeprefix("Jinx AI - Session - ")
    datetime.strptime(stamp, "%Y-%m-%d %H:%M")


def test_assistant_tools_are_companion_only() -> None:
    names = {item["function"]["name"] for item in tools_schema_for("assistant")}
    assert names == set(ASSISTANT_TOOL_NAMES)
    assert "read_file" not in names
    assert "web_search" in names
    assert "generate_image" in names
    assert "image_search" in names
    assert "screenshot" in names
    assert "hardware_info" in names
    assert "recommend_models" in names
    assert "open_url" in names
    full = {item["function"]["name"] for item in tools_schema_for("developer")}
    assert "replace_in_file" in full
    assert "read_terminal" in full
    assert "read_terminal" not in names


def test_home_and_members_have_no_chat() -> None:
    for tab in ("home", "members", "chat"):
        activity, chat_full, show_chat = chat_pane_for("developer", tab)
        if tab == "chat":
            assert activity == "home"
        else:
            assert activity == tab
        assert not chat_full
        assert not show_chat


def test_project_explorer_and_files_dock_chat() -> None:
    activity, chat_full, show_chat = chat_pane_for("developer", "teams")
    assert activity == "teams"
    assert not chat_full
    assert show_chat
    activity, chat_full, show_chat = chat_pane_for("developer", "files")
    assert activity == "files"
    assert show_chat
    for tab in ("git", "db", "settings", "look", "models", "appearance"):
        _, chat_full, show_chat = chat_pane_for("developer", tab)
        assert not chat_full
        assert not show_chat
