from jinx_ai.agent_context import (
    expand_slash_command,
    list_templates,
    templates_prompt_index,
)


def test_bundled_review_and_commit_templates() -> None:
    names = {item.name.lower() for item in list_templates()}
    assert "review" in names
    assert "commit" in names


def test_expand_slash_review_with_extra() -> None:
    body, name = expand_slash_command("/review src/app.py")
    assert name == "review"
    assert "senior reviewer" in body.lower()
    assert body.endswith("src/app.py")


def test_expand_slash_commit() -> None:
    body, name = expand_slash_command("/commit")
    assert name == "commit"
    assert "conventional commit" in body.lower()
    assert "Do not commit unless I explicitly ask." in body


def test_unknown_slash_stays() -> None:
    raw = "/not-a-real-template please"
    body, name = expand_slash_command(raw)
    assert name is None
    assert body == raw


def test_plain_text_is_unchanged() -> None:
    body, name = expand_slash_command("fix the bug")
    assert name is None
    assert body == "fix the bug"


def test_templates_index_lists_slash_names() -> None:
    index = templates_prompt_index()
    assert "/review:" in index
    assert "/commit:" in index
