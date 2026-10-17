from jinx_ai.voice import spoken_vs_chat, strip_for_speech, strip_speak_tags


def test_strip_emoji_keeps_words() -> None:
    assert strip_for_speech("Done! 😊 I opened the link.") == "Done! I opened the link."
    assert "😊" not in strip_for_speech("Hi 💜 there")


def test_spoken_vs_chat_strips_emoji_not_display() -> None:
    display, spoken = spoken_vs_chat("Found you 😄 here is the link.", tools_used=False)
    assert "😄" in display
    assert "😄" not in spoken
    assert "Found you" in spoken


def test_spoken_vs_chat_hides_tool_json() -> None:
    dump = 'Step 1\n{"name": "run_command", "arguments": {"command": "npm start"}}\n'
    display, spoken = spoken_vs_chat(dump, tools_used=True)
    assert "run_command" not in display
    assert "npm start" not in display
    assert "run_command" not in spoken


def test_speak_tags_never_appear_in_chat_even_at_end() -> None:
    reply = (
        "I need to pause and flag this for developers.\n\n"
        "[speak]Thank you for your patience. I'll be back later.[/speak]"
    )
    display, spoken = spoken_vs_chat(reply, tools_used=False)
    assert "[speak]" not in display
    assert "[/speak]" not in display
    assert "Thank you for your patience" in spoken
    assert "flag this for developers" in display


def test_strip_speak_tags_helper() -> None:
    chat, spoken = strip_speak_tags(
        "Hello\n[speak]Hi aloud[/speak]\nMore text"
    )
    assert "[speak]" not in chat
    assert "Hello" in chat and "More text" in chat
    assert spoken == "Hi aloud"
