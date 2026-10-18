"""One lane per project: projects can run side by side, a project still runs one turn at a time."""

from __future__ import annotations

import threading

import pytest

from backend.core import chat_service as cs
from backend.core.chat_service import ChatService
from backend.platform.models import Project, new_id
from backend.platform.registry import save_project
from backend.services.workspace import current_folders
from tests.test_chat_service import FakeProvider, project, wait_for  # noqa: F401 - fixtures


class _Gated(FakeProvider):
    """Waits on ``gate`` before answering; records the folders the turn sees."""

    def __init__(self) -> None:
        super().__init__("done")
        self.gate = threading.Event()
        self.started = threading.Semaphore(0)
        self.seen_folders: list[list[str]] = []

    def chat(self, messages, tools=None):
        self.seen_folders.append([str(p) for p in current_folders()])
        self.started.release()
        assert self.gate.wait(10)
        return super().chat(messages, tools)


@pytest.fixture
def second(project, isolated):  # noqa: F811
    from backend.db import ledger

    ws = isolated / "workspace2"
    ws.mkdir()
    p = Project(id=new_id("proj"), name="Second", folders=[str(ws)])
    save_project(p)
    ledger.ensure_project(p.id, p.name, str(ws))
    return p


def _service(monkeypatch, provider, parallel: int) -> ChatService:
    service = ChatService(use_memory=False, parallel=parallel)
    monkeypatch.setattr(cs.provider_factory, "provider_for_turn", lambda *a, **k: provider)
    monkeypatch.setattr(cs.provider_factory, "caps_for_current", lambda *a, **k: ("openrouter", "fake", [], True))
    return service


def test_two_projects_run_at_once_each_on_its_own_computer(recorder, project, second, monkeypatch):  # noqa: F811
    provider = _Gated()
    svc = _service(monkeypatch, provider, parallel=2)
    try:
        a = svc.submit(project.id, "pulse", "hello A")
        b = svc.submit(second.id, "pulse", "hello B")
        assert a["queued"] is False and b["queued"] is False
        assert provider.started.acquire(timeout=10) and provider.started.acquire(timeout=10)
        assert {t["project_id"] for t in svc.current_turns()} == {project.id, second.id}
        assert sorted(f[0] for f in provider.seen_folders) == sorted([project.folders[0], second.folders[0]])
        # the same project still queues behind its own running turn
        assert svc.submit(project.id, "pulse", "and another")["queued"] is True
        provider.gate.set()
        wait_for(lambda: not svc.is_busy() and svc.queue_length() == 0, 15, "every lane to drain")
    finally:
        provider.gate.set()
        svc.close()


def test_with_one_lane_a_second_project_waits(recorder, project, second, monkeypatch):  # noqa: F811
    provider = _Gated()
    svc = _service(monkeypatch, provider, parallel=1)
    try:
        assert svc.submit(project.id, "pulse", "first")["queued"] is False
        assert provider.started.acquire(timeout=10)
        assert svc.submit(second.id, "pulse", "second")["queued"] is True
        assert svc.is_busy(project.id) and not svc.is_busy(second.id)
        provider.gate.set()
        wait_for(lambda: not svc.is_busy() and svc.queue_length() == 0, 15, "the queue to drain")
        assert len(provider.seen_folders) == 2
    finally:
        provider.gate.set()
        svc.close()


def test_stop_drops_only_its_own_projects_queue(recorder, project, second, monkeypatch):  # noqa: F811
    provider = _Gated()
    svc = _service(monkeypatch, provider, parallel=1)
    try:
        turn = svc.submit(project.id, "pulse", "first")["turn_id"]
        assert provider.started.acquire(timeout=10)
        svc.submit(project.id, "pulse", "queued for A")
        svc.submit(second.id, "pulse", "queued for B")
        assert svc.abort(turn)
        provider.gate.set()
        wait_for(lambda: not svc.is_busy() and svc.queue_length() == 0, 15, "B to run")
        assert [m["role"] for m in svc.list_messages(second.id, "pulse")] == ["user", "assistant"]
        assert all("queued for A" not in m.get("text", "") for m in svc.list_messages(project.id, "pulse"))
    finally:
        provider.gate.set()
        svc.close()


def test_a_command_scope_is_per_turn() -> None:
    from backend.services import terminal

    mine, other = terminal.CommandScope(), terminal.CommandScope()
    token = terminal.bind_command_scope(mine)
    try:
        terminal.abort_running_commands(other)  # Stop pressed for another project's turn
        assert not terminal._aborted()
        terminal.abort_running_commands()
        assert terminal._aborted()
    finally:
        terminal.reset_command_scope(token)
    assert not terminal._aborted()  # the process-wide scope never saw either Stop
