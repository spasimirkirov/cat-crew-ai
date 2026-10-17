"""Prompt caching: the system prompt stays identical from message to message, and what changes
rides in the turn note on the newest user message."""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.harness.turn_note import NOTE_HEAD, attach_turn_note
from backend.platform.chief import describe_project, turn_context
from backend.platform.models import Bot, Project, ProjectEvent, Registry
from backend.providers.openai_compat import OpenAICompatProvider


def test_the_turn_note_goes_on_the_newest_user_message_of_a_copy() -> None:
    history = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second"},
    ]
    out = attach_turn_note(history, "Now: Monday")
    assert out[-1]["content"].startswith("second\n\n" + NOTE_HEAD) and "Now: Monday" in out[-1]["content"]
    assert out[1]["content"] == "first" and history[-1]["content"] == "second"  # the stored chat is untouched


def test_a_vision_turn_gets_the_note_as_one_more_text_part() -> None:
    parts = [{"type": "text", "text": "what is this?"}, {"type": "image_url", "image_url": {"url": "data:x"}}]
    out = attach_turn_note([{"role": "user", "content": parts}], "Now: Monday")
    assert out[0]["content"][:2] == parts and NOTE_HEAD in out[0]["content"][2]["text"]


def test_the_system_prompt_does_not_change_with_the_clock(monkeypatch) -> None:
    from backend.services import here
    from backend.tools import build_system_prompt

    monkeypatch.setattr(here, "clock_now", lambda: datetime(2026, 10, 5, 9, 0).astimezone())
    first = build_system_prompt("developer")
    note_a = turn_context()
    monkeypatch.setattr(here, "clock_now", lambda: datetime(2026, 10, 5, 9, 0).astimezone() + timedelta(minutes=7))
    assert build_system_prompt("developer") == first
    assert turn_context() != note_a  # the clock moved, and the note shows it


def test_the_stable_project_card_leaves_out_state_and_recent_memory() -> None:
    bot = Bot(id="pulse", name="Pulse", role="developer", system_prompt="Build.")
    project = Project(
        id="p1",
        name="Shop",
        folders=["D:/shop"],
        state={"kickoff": "a shop", "created_artifacts": ["Pulse wrote src/x.ts"] * 300},
        events=[ProjectEvent(role="assistant", text="built the cart", bot_id="pulse")],
    )
    registry = Registry(bots={"pulse": bot}, projects={"p1": project})
    stable = describe_project(project, registry, live=False)
    assert "a shop" in stable and "Recent memory" not in stable and "created_artifacts" not in stable
    live = describe_project(project, registry)
    assert "built the cart" in live
    state_line = next(line for line in live.splitlines() if "created_artifacts" in line)
    assert len(state_line) < 700  # 300 artifacts no longer land in the prompt whole


def _payload(model: str, messages: list[dict]) -> list[dict]:
    provider = OpenAICompatProvider(base_url="https://x.test/v1", api_key="k", model=model, host_id="openrouter")
    provider.enable_prompt_caching = True
    return provider._payload(messages, None, stream=False)["messages"]


def test_claude_gets_a_moving_breakpoint_on_the_newest_message() -> None:
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
    ]
    sent = _payload("anthropic/claude-sonnet-4", messages)
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[-1]["content"][0] == {"type": "text", "text": "file body", "cache_control": {"type": "ephemeral"}}
    assert sent[1]["content"] == "go" and messages[-1]["content"] == "file body"
    other = _payload("google/gemini-2.5-flash", messages)
    assert other[-1]["content"] == "file body"  # one breakpoint (the system prompt) elsewhere
