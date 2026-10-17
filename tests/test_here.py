from datetime import datetime

from backend.services.here import here_and_now, looks_like_affirmation, looks_like_weather, should_attach_forecast


def test_here_and_now_has_clock_and_tomorrow() -> None:
    text = here_and_now()
    now = datetime.now().astimezone()
    assert "Now:" in text
    assert now.strftime("%A") in text
    assert "Tomorrow is" in text
    assert "Do not ask permission" in text


def test_affirmation_after_offer() -> None:
    assert looks_like_affirmation("Yes, I would like you to do that.")
    assert looks_like_affirmation("да")
    assert not looks_like_affirmation("no thanks")
    history = [
        {
            "role": "assistant",
            "content": "Would you like me to search the web for the weather?",
        }
    ]
    assert should_attach_forecast("Yes, I would like you to do that.", history)
    assert not should_attach_forecast("Yes, I would like you to do that.", [])


def test_weather_words_in_english_and_bulgarian() -> None:
    assert looks_like_weather("what's the weather tomorrow")
    assert looks_like_weather("какво ще е времето утре")
