from jinx_ai.wake import interpret_utterance, is_sleep, split_wake


def test_hey_jinx_with_command() -> None:
    hit, rest = split_wake("Hey Jinx open the git panel")
    assert hit is True
    assert rest == "open the git panel"


def test_bare_wake() -> None:
    hit, rest = split_wake("hey jinx")
    assert hit is True
    assert rest == ""


def test_jinx_at_start() -> None:
    hit, rest = split_wake("Jinx, review this file")
    assert hit is True
    assert rest == "review this file"


def test_no_wake_ignored() -> None:
    hit, rest = split_wake("please format the disk")
    assert hit is False
    assert rest == "please format the disk"


def test_jinx_not_at_start() -> None:
    hit, rest = split_wake("tell jinx nothing")
    assert hit is False


def test_whisper_comma_jinx() -> None:
    hit, rest = split_wake("Hey, Jinx!")
    assert hit is True
    assert rest == ""


def test_whisper_jinks() -> None:
    hit, rest = split_wake("Hey, Jinks!")
    assert hit is True
    assert rest == ""


def test_repeated_wake_is_still_bare() -> None:
    hit, rest = split_wake("hey jinx hey jinx")
    assert hit is True
    assert rest == ""


def test_filler_then_wake() -> None:
    hit, rest = split_wake("Um, hey Jinx open git")
    assert hit is True
    assert rest == "open git"


def test_sleep_phrases() -> None:
    assert is_sleep("goodbye")
    assert is_sleep("Good bye")
    assert is_sleep("ok bye")
    assert is_sleep("okay, bye Jinx")
    assert is_sleep("that's all")
    assert is_sleep("thanks, that's all")
    assert is_sleep("go to sleep")
    assert not is_sleep("please format the disk")
    assert not is_sleep("open git")


def test_interpret_asleep_ignores_chatter() -> None:
    kind, payload = interpret_utterance("please format the disk", awake=False)
    assert kind == "ignore"
    assert payload == "please format the disk"


def test_interpret_wake_then_command() -> None:
    kind, payload = interpret_utterance("Hey, Jinx open the git panel", awake=False)
    assert kind == "command"
    assert payload == "open the git panel"


def test_interpret_session_follow_up() -> None:
    kind, payload = interpret_utterance("now commit that", awake=True)
    assert kind == "command"
    assert payload == "now commit that"


def test_interpret_goodbye_ends_session() -> None:
    kind, payload = interpret_utterance("ok bye", awake=True)
    assert kind == "sleep"
    assert payload == ""


def test_interpret_goodbye_while_asleep_ignored() -> None:
    kind, _payload = interpret_utterance("goodbye", awake=False)
    assert kind == "ignore"


def test_interpret_wake_then_bye() -> None:
    kind, _payload = interpret_utterance("Hey Jinx goodbye", awake=False)
    assert kind == "sleep"
