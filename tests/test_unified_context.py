"""Unified mode: one shared message history per run, the cat is a swapped mask; plus cat profiles."""

from __future__ import annotations

import json

import pytest

from backend import config
from backend.harness.runtime import reset_provider, set_provider
from backend.platform import cat_profiles, shared_context
from backend.platform.delegation import run_delegated
from backend.platform.models import Bot, Project, Registry
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall
from backend.services import compaction
from backend.services.workspace import current_folders, set_folders
import backend.services.pending_restart as _pending_restart


class _Script(BaseProvider):
    display_name = "script"

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)
        self.payloads: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> AssistantMessage:
        self.payloads.append([dict(m) for m in messages])
        return self._replies.pop(0)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


@pytest.fixture
def crew(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    _pending_restart.reset_runtime_state()
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "package.json").write_text(json.dumps({"name": "s", "scripts": {}}), encoding="utf-8")
    pulse = Bot(id="pulse", name="Pulse", role="developer", system_prompt="You are Pulse the developer.")
    aegis = Bot(id="aegis", name="Aegis", role="qa", system_prompt="You are Aegis the tester.")
    project = Project(id="p1", name="P", folders=[str(folder)], bot_ids=["pulse", "aegis"])
    registry = Registry(bots={"pulse": pulse, "aegis": aegis}, projects={"p1": project})
    saved = current_folders()
    set_folders([folder])
    yield registry, project, folder
    shared_context.end_run("p1")
    set_folders(saved)


def _hand_over(crew, provider):
    registry, project, folder = crew
    token = set_provider(provider)
    try:
        run_delegated(registry.bots["pulse"], "build the header", project=project, registry=registry)
        run_delegated(registry.bots["aegis"], "check the header", project=project, registry=registry)
    finally:
        reset_provider(token)


def _script():
    return [
        AssistantMessage(content="", tool_calls=[ToolCall(name="write_file", arguments={"path": "header.css", "content": ".h{}"})]),
        AssistantMessage(content="header written"),
        AssistantMessage(content="PASS header looks right"),
    ]


def test_unified_mode_swaps_only_the_mask_and_shares_one_history(crew, monkeypatch) -> None:
    monkeypatch.setattr(config, "UNIFIED_CONTEXT", True)
    thread = shared_context.begin_run("p1")
    assert thread is not None
    provider = _Script(_script())
    _hand_over(crew, provider)

    qa_payload = provider.payloads[-1]
    assert qa_payload[0]["role"] == "system" and "You are Aegis the tester." in qa_payload[0]["content"]
    assert "You are Pulse the developer." not in qa_payload[0]["content"]  # the mask, not a second prompt
    body = "\n".join(str(m.get("content")) for m in qa_payload[1:])
    assert "[assignment for Pulse (developer)]" in body and "[Pulse] " in body and "header written" in body
    assert "Files touched: " in body and "header.css" in body
    assert body.rstrip().endswith("check the header")  # the active cat's own assignment is last
    assert not any(m.get("role") == "tool" for m in qa_payload)  # Pulse's tool calls were collapsed
    assert thread.cats == ["Pulse", "Aegis"]
    assert len(thread.history) == 4  # a user/assistant pair per finished cat


def test_isolated_mode_is_untouched_when_the_flag_is_off(crew, monkeypatch) -> None:
    monkeypatch.setattr(config, "UNIFIED_CONTEXT", False)
    assert shared_context.begin_run("p1") is None
    provider = _Script(_script())
    _hand_over(crew, provider)
    qa_payload = provider.payloads[-1]
    assert [m["role"] for m in qa_payload] == ["system", "user"]
    assert "header written" not in "\n".join(str(m.get("content")) for m in qa_payload)


def test_the_shared_history_is_bounded() -> None:
    thread = shared_context.SharedThread("p")
    for n in range(40):
        thread.close_turn([{"role": "system", "content": "s"}], f"Cat{n}", "developer", "task " * 200, "result " * 400)
    size = sum(len(m["content"]) for m in thread.history)
    assert size <= shared_context._HISTORY_CAP and len(thread.history) % 2 == 0
    assert "Cat39" in thread.history[-1]["content"]  # the newest turns are the ones kept


def test_compaction_pins_the_active_cats_assignment_not_the_first_cats() -> None:
    class _Summarizer(BaseProvider):
        display_name = "sum"

        def chat(self, messages, tools=None):
            return AssistantMessage(content="summary of earlier work")

        def tool_message(self, tool_call, result):
            return {"role": "tool", "content": result}

    first = shared_context.assignment_message("Pulse", "developer", "build the header")
    mine = shared_context.assignment_message("Aegis", "qa", "verify the header thoroughly")
    body: list[Message] = [
        first,
        {"role": "assistant", "content": "[Pulse] built it"},
        mine,
    ]
    for n in range(8):
        body.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        body.append({"role": "tool", "tool_call_id": f"c{n}", "content": "x" * 50})
    out = compaction.compact_messages(_Summarizer(), [{"role": "system", "content": "S"}, *body], keep=4)
    assert out is not None
    pinned = out[1]["content"]
    assert pinned.startswith(compaction.ASSIGNMENT_HEAD) and "verify the header thoroughly" in pinned
    assert "build the header" not in pinned.split(compaction.COMPACTED_MARK)[0]
    # a second compaction keeps pinning it
    again = compaction.compact_messages(_Summarizer(), [*out, *body[3:]], keep=4)
    assert again is not None and "verify the header thoroughly" in again[1]["content"]


def test_a_normal_run_still_pins_its_first_message() -> None:
    class _Summarizer(BaseProvider):
        display_name = "sum"

        def chat(self, messages, tools=None):
            return AssistantMessage(content="s")

        def tool_message(self, tool_call, result):
            return {"role": "tool", "content": result}

    msgs: list[Message] = [{"role": "system", "content": "S"}, {"role": "user", "content": "do the thing"}]
    for n in range(8):
        msgs.append({"role": "assistant", "content": f"step {n}"})
        msgs.append({"role": "user", "content": f"go on {n}"})
    out = compaction.compact_messages(_Summarizer(), msgs, keep=4)
    assert out is not None and "do the thing" in out[1]["content"]


# ---- cat profiles ---------------------------------------------------------------------------


@pytest.fixture
def seeded(isolated):
    from backend.api.app import _startup_data

    _startup_data()
    return isolated


def test_every_core_cat_has_a_profile_with_its_own_scoped_tools(seeded) -> None:
    profiles = {p.id: p for p in cat_profiles.load_cat_profiles()}
    assert {"cipher", "vanguard", "pixel", "pulse", "aegis", "forge"} <= set(profiles)
    pulse, aegis, vanguard = profiles["pulse"], profiles["aegis"], profiles["vanguard"]
    assert {"read_file", "write_file", "search_files", "run_command", "write_file_patch"} <= set(pulse.allowed_tools)
    assert {"browser_check", "browser_click", "run_test_suite"} <= set(aegis.allowed_tools)
    assert "create_fix_task" in vanguard.allowed_tools and "update_todo" in vanguard.allowed_tools
    # scoping: each cat gets its own set, not everything
    assert "browser_click" not in pulse.allowed_tools and "write_file_patch" not in aegis.allowed_tools
    assert all("delegate_to_bot" not in p.allowed_tools for p in profiles.values())
    assert profiles["pulse"].avatar_icon == "faCode" and profiles["forge"].avatar_icon == "faServer"


def test_cat_profiles_endpoint_lists_them_without_the_prompt(seeded) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.core.chat_service import ChatService

    with TestClient(create_app(chat_service=ChatService(use_memory=False)), base_url="http://127.0.0.1") as client:
        rows = client.get("/api/cat-profiles").json()
    by_id = {r["id"]: r for r in rows}
    assert "pulse" in by_id and "system_prompt" not in by_id["pulse"]
    assert "write_file" in by_id["pulse"]["allowed_tools"]
