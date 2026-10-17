from jinx_ai.stt import looks_like_whisper_hallucination


def test_whisper_greeting_is_hallucination() -> None:
    text = "How are you doing? Good morning."
    assert looks_like_whisper_hallucination(text)
    assert not looks_like_whisper_hallucination("What's the weather tomorrow?")
    assert not looks_like_whisper_hallucination("")
