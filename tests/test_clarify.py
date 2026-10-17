from backend.services.clarify import parse_clarify, should_clarify
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall


def test_should_clarify_skips_assistant_and_short_steers() -> None:
    assert not should_clarify("направи бутон в сеттингс", mode="assistant")
    assert not should_clarify("yes", mode="developer")
    assert not should_clarify("да, моля", mode="developer")
    assert not should_clarify("fix that", mode="developer")
    assert not should_clarify("оправи го", mode="developer")
    assert not should_clarify("Add git tool", mode="developer")
    assert not should_clarify("Tell me the weather for tomorrow.", mode="developer")


def test_should_clarify_keeps_messy_bulgarian_and_long_english() -> None:
    assert should_clarify("направи ми бутон за сеттингс в look страницата", mode="developer")
    assert should_clarify(
        "implement a clarify pass for messy developer prompts and show Working from",
        mode="developer",
    )


def test_parse_clarify_strips_fences_and_labels() -> None:
    raw = "```\nTask spec: Add a save button on Look.\n- Wire the click\n```"
    out = parse_clarify(raw)
    assert "Add a save button" in out
    assert "```" not in out
    assert parse_clarify("") == ""
    assert parse_clarify("<tool_call>write_file</tool_call>") == ""


def test_clarify_task_uses_a_no_tools_chat() -> None:
    from backend.services import clarify as clarify_mod

    seen: list[list[Message]] = []

    class _Fake(BaseProvider):
        def chat(self, messages: list[Message], tools=None) -> AssistantMessage:
            seen.append(messages)
            assert tools is None
            return AssistantMessage(content="Goal: add a Look save button.\n- Wire the click")

        def tool_message(self, tool_call: ToolCall, result: str) -> Message:
            return {"role": "tool", "content": result}

    spec = clarify_mod.clarify_task(
        _Fake(),
        "направи бутон сейв в look",
        [{"role": "user", "content": "направи бутон сейв в look"}],
    )
    assert "Look save button" in spec
    assert seen[0][0]["role"] == "system"
    assert "Bulgarian" in seen[0][0]["content"]
