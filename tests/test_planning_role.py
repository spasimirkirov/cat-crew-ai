"""The Planning role: only the architecture/distribution phases use it, and only when the user picked a model."""

from __future__ import annotations

from backend.harness.runtime import reset_provider, set_provider
from backend.platform import sop
from backend.platform.delegation import run_delegated
from backend.platform.models import Bot, Project, Registry
from backend.providers.base import AssistantMessage, BaseProvider, Message, ToolCall


class _Says(BaseProvider):
    def __init__(self, text: str) -> None:
        self.text = text

    def chat(self, messages: list[Message], tools: list[dict] | None = None):
        return AssistantMessage(content=self.text)

    def tool_message(self, tool_call: ToolCall, result: str) -> Message:
        return {"role": "tool", "name": tool_call.name, "content": result}


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.platform.registry.STORE_PATH", tmp_path / "registry.json")
    folder = tmp_path / "app"
    folder.mkdir()
    bot = Bot(id="vanguard", name="Vanguard", role="vanguard", system_prompt="Plan.")
    project = Project(id="p1", name="P", folders=[str(folder)])
    return bot, project, Registry(bots={"vanguard": bot}, projects={"p1": project})


def test_run_delegated_uses_the_given_provider_instead_of_the_chats(tmp_path, monkeypatch):
    bot, project, registry = _setup(tmp_path, monkeypatch)
    token = set_provider(_Says("chat model answer"))
    try:
        result = run_delegated(bot, "plan it", project=project, registry=registry, provider=_Says("planning model answer"))
        plain = run_delegated(bot, "plan it", project=project, registry=registry)
    finally:
        reset_provider(token)
    assert "planning model answer" in result
    assert "chat model answer" in plain


def test_planning_phases_ask_for_planning_and_qa_for_review(monkeypatch):
    asked: list[str] = []
    marker = _Says("x")

    def fake(role: str, *_a, **_k):
        asked.append(role)
        return marker

    monkeypatch.setattr("backend.core.provider_factory.provider_if_explicit", fake)
    assert sop._planning_provider("architecture") is marker
    assert sop._planning_provider("distribution") is marker
    assert sop._planning_provider("qa") is marker
    for phase in ("blueprint", "devops", "build", "design", ""):
        assert sop._planning_provider(phase) is None
    assert asked == ["planning", "planning", "review"]


def test_an_unset_planning_model_keeps_the_chat_model(isolated):
    assert sop._planning_provider("architecture") is None


def test_a_broken_planning_setting_does_not_stop_the_build(monkeypatch):
    def boom(role: str, *_a, **_k):
        raise RuntimeError("no key")

    monkeypatch.setattr("backend.core.provider_factory.provider_if_explicit", boom)
    assert sop._planning_provider("architecture") is None


def test_run_one_passes_the_planning_provider_only_for_planning_phases(monkeypatch, tmp_path):
    bot, project, registry = _setup(tmp_path, monkeypatch)
    seen: dict[str, object] = {}
    marker = _Says("x")

    def fake_run(bot, task, **kw):
        seen[kw.get("phase_marker", "")] = kw.get("provider")
        return "ok"

    monkeypatch.setattr(sop, "run_delegated", fake_run)
    monkeypatch.setattr("backend.core.provider_factory.provider_if_explicit", lambda role, *_a, **_k: marker)
    sop._run_one(bot, "t", project=project, registry=registry, phase="architecture")
    assert seen[""] is marker
    seen.clear()
    sop._run_one(bot, "t", project=project, registry=registry, phase="build")
    assert seen[""] is None
