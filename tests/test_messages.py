"""Wire shape of chat messages (docs/API.md -> Message)."""

from __future__ import annotations

from backend.core.messages import clean_summary, error_message, node_to_message, session_messages


def _node(**kw):
    base = {"id": "n1", "role": "assistant", "content": "hi", "created_at": "2026-01-01T00:00:00+00:00"}
    base.update(kw)
    return base


def test_role_banner_is_dropped_only_for_the_same_speaker():
    banner = "[Cipher / prompt_engineer]\nBlueprint ready."
    assert node_to_message(_node(content=banner, speaker="Cipher", bot_id="cipher"))["text"] == "Blueprint ready."
    assert node_to_message(_node(content=banner, speaker="Pulse"))["text"] == banner
    assert node_to_message(_node(content="[Note / Draft] keep me", speaker="Cipher"))["text"] == "[Note / Draft] keep me"


def test_tool_event_suffix_is_not_duplicated_on_the_wire():
    node = _node(content="Built it.\n\n· write_file a.py\n· run_command npm test", tool_events=["write_file a.py", "run_command npm test"])
    msg = node_to_message(node)
    assert msg["text"] == "Built it."
    assert msg["tool_events"] == ["write_file a.py", "run_command npm test"]


def test_activity_card_and_optional_fields():
    node = _node(activity={"elapsed_s": 2, "steps": [{"line": "Thinking", "preview": ""}, {"line": "write_file a.py", "preview": "ok"}]}, phase="build")
    msg = node_to_message(node, bot_id="pulse")
    assert msg["bot_id"] == "pulse" and msg["phase"] == "build"
    assert msg["activity"]["steps"] == [{"line": "Thinking"}, {"line": "write_file a.py", "preview": "ok"}]
    assert "activity" not in node_to_message(_node())


def test_user_messages_have_no_bot_id_and_errors_are_role_error():
    assert "bot_id" not in node_to_message(_node(role="user", content="build"), bot_id="pulse")
    err = error_message("Error: boom")
    assert err["role"] == "error" and err["text"] == "Error: boom" and err["id"]


def test_hidden_and_empty_nodes_are_skipped():
    from backend.services.session_tree import append_turn, ensure_tree

    session = {"id": "s", "messages": []}
    ensure_tree(session)
    append_turn(session, "user", "hello")
    hidden = append_turn(session, "assistant", "secret")
    hidden["hidden"] = True
    append_turn(session, "assistant", "visible")
    assert [m["text"] for m in session_messages(session)] == ["hello", "visible"]


def test_runner_computer_note_is_not_shown_to_people():
    note = "Computer after this turn: /work/cafe\n  index.html, style.css, assets/"
    msg = node_to_message(_node(content=f"Landing page built.\n\n{note}"))
    assert msg["text"] == "Landing page built."
    empty = node_to_message(_node(content="Nothing yet.\nComputer after this turn: /work/x — EMPTY. Nothing is on disk yet."))
    assert empty["text"] == "Nothing yet."
    # only a trailing note is removed
    keep = node_to_message(_node(content="Computer after this turn: is a phrase I use.\nMore text follows."))
    assert keep["text"].endswith("More text follows.")


def test_task_summaries_drop_banner_and_runner_note():
    raw = "[Pixel / designer] CatCrewAI here — done.\nComputer after this turn: /w\n  a.html"
    assert clean_summary(raw) == "CatCrewAI here — done."
    assert clean_summary("") == "" and clean_summary("plain") == "plain"
