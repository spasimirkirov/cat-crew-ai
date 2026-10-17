"""backend/middleware/jev_gatekeeper.py: the precheck (skip tool schemas) and tool-call guard."""

from __future__ import annotations

import pytest

from backend.middleware import jev_gatekeeper as jg
from backend.services import jev_metrics
from backend.services.jev_client import Decision


class _FakeClient:
    def __init__(self, decisions):
        self._decisions = decisions
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        return self._decisions


@pytest.fixture(autouse=True)
def _clean_metrics():
    jev_metrics.clear()
    yield
    jev_metrics.clear()


def _enable(monkeypatch, *, threshold=0.92, model="typesafe/jev-latest", opt_in=True):
    """Jev on; ``opt_in`` also turns on the guard and the precheck, which are off by default."""
    monkeypatch.setattr(
        jg,
        "_settings",
        lambda: {
            "enabled": True,
            "model": model,
            "confidence_threshold": threshold,
            "api_key": "k",
            "guard_tool_calls": opt_in,
            "skip_tools_precheck": opt_in,
        },
    )


def test_precheck_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(jg, "_settings", lambda: {"enabled": False, "model": "typesafe/jev-latest", "confidence_threshold": 0.92, "api_key": ""})
    assert jg.precheck_intent([{"role": "user", "content": "hi"}]) is None


def test_precheck_is_a_noop_with_no_key(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jg, "_client", lambda: None)
    assert jg.precheck_intent([{"role": "user", "content": "hi"}]) is None


def test_precheck_bypasses_on_a_confident_noul_and_records_the_saving(monkeypatch):
    _enable(monkeypatch, threshold=0.92)
    decision = Decision(id="no_tools_needed", type="noul", value=True, confidence=0.99, ok=True, latency_ms=5.0)
    fake = _FakeClient([decision])
    monkeypatch.setattr(jg, "_client", lambda: fake)
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"a": "b" * 200}}}]
    out = jg.precheck_intent([{"role": "user", "content": "what is 2+2?"}], tools=tools)
    assert out is decision
    snap = jev_metrics.snapshot()
    assert snap["precheck_calls"] == 1 and snap["bypasses"] == 1
    assert snap["tokens_saved_estimate"] > 0  # the tool schema JSON was sized, not skipped


def test_precheck_does_not_bypass_below_the_fixed_bar(monkeypatch):
    _enable(monkeypatch)
    # 0.90 < BYPASS_CONFIDENCE (0.95): a real threshold slider setting must not affect this.
    decision = Decision(id="no_tools_needed", type="noul", value=True, confidence=0.90, ok=True, latency_ms=5.0)
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([decision]))
    out = jg.precheck_intent([{"role": "user", "content": "edit my file"}])
    assert out is None
    assert jev_metrics.snapshot()["bypasses"] == 0


def test_precheck_ignores_an_empty_conversation(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jg, "_client", lambda: pytest.fail("Jev must not be called with no user text"))
    assert jg.precheck_intent([{"role": "assistant", "content": "hello"}]) is None


def test_always_allowed_tools_skip_jev_entirely(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jg, "_client", lambda: pytest.fail("read-only tools must never reach Jev"))
    assert jg.guard_tool_call("read_file", {"path": "x"}) is None


def test_guard_blocks_only_a_call_jev_is_sure_is_unsafe(monkeypatch):
    _enable(monkeypatch, threshold=0.92)
    decision = Decision(id="looks_safe", type="noul", value=False, confidence=0.05, ok=True, latency_ms=9.0)
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([decision]))
    msg = jg.guard_tool_call("run_command", {"cmd": "rm -rf /"}, messages=[{"role": "user", "content": "clean up"}])
    assert msg is not None
    assert "run_command" in msg and "95%" in msg
    assert jev_metrics.snapshot()["blocked_tool_calls"] == 1


def test_guard_lets_a_call_through_when_jev_is_merely_unsure(monkeypatch):
    """The evals: Jev sees the goal and the arguments, never the files, so it is unsure about most
    real edits. Doubt is not a reason to block (it blocked every correct fix at a 92 % bar)."""
    _enable(monkeypatch, threshold=0.92)
    decision = Decision(id="looks_safe", type="noul", value=False, confidence=0.4, ok=True, latency_ms=9.0)
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([decision]))
    assert jg.guard_tool_call("replace_in_file", {"path": "pricing.py"}, messages=[{"role": "user", "content": "fix"}]) is None
    assert jev_metrics.snapshot()["blocked_tool_calls"] == 0


def test_guard_and_precheck_are_opt_in(monkeypatch):
    _enable(monkeypatch, opt_in=False)
    monkeypatch.setattr(jg, "_client", lambda: pytest.fail("Jev must not be asked unless opted in"))
    assert jg.guard_tool_call("run_command", {"cmd": "rm -rf /"}) is None
    assert jg.precheck_intent([{"role": "user", "content": "what is 2+2?"}]) is None


def test_the_goal_is_the_request_not_a_harness_nudge_or_the_turn_note():
    from backend.harness.turn_note import attach_turn_note

    messages = attach_turn_note([{"role": "user", "content": "Rename get_usr to get_user"}], "Now: Monday 21:00")
    assert jg.turn_goal(messages) == "Rename get_usr to get_user"


def test_a_clear_request_skips_the_clarify_rewrite_only_when_jev_is_sure(monkeypatch):
    _enable(monkeypatch, opt_in=False)
    sure = Decision(id="request_is_clear", type="noul", value=True, confidence=0.97, ok=True, latency_ms=3.0)
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([sure]))
    assert jg.request_is_clear("Add a dark mode toggle to src/Settings.tsx") is True
    unsure = Decision(id="request_is_clear", type="noul", value=True, confidence=0.7, ok=True, latency_ms=3.0)
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([unsure]))
    assert jg.request_is_clear("napravi go da raboti s dark mode i oshte neshto") is False
    snap = jev_metrics.snapshot()
    assert snap["gate_calls"] == 2 and snap["skipped_calls"] == 1


def test_a_one_off_task_skips_the_skill_proposal_call(monkeypatch):
    _enable(monkeypatch, opt_in=False)
    one_off = Decision(id="task_has_reusable_skill", type="noul", value=False, confidence=0.03, ok=True, latency_ms=3.0)
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([one_off]))
    assert jg.no_skill_in_task("Changed the hero title to 'Shop'") is True
    maybe = Decision(id="task_has_reusable_skill", type="noul", value=False, confidence=0.3, ok=True, latency_ms=3.0)
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([maybe]))
    assert jg.no_skill_in_task("Set up Vite + React + Tailwind from scratch") is False


def test_gates_have_no_say_when_jev_is_off_or_fails(monkeypatch):
    monkeypatch.setattr(jg, "_settings", lambda: {"enabled": False})
    assert jg.request_is_clear("anything") is False and jg.no_skill_in_task("anything") is False
    _enable(monkeypatch, opt_in=False)
    failed = Decision(id="x", type="noul", value=False, confidence=0.0, ok=False, reason="timeout")
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([failed]))
    assert jg.no_skill_in_task("anything") is False


def test_guard_lets_a_confident_call_through(monkeypatch):
    _enable(monkeypatch, threshold=0.92)
    decision = Decision(id="looks_safe", type="noul", value=True, confidence=0.98, ok=True, latency_ms=9.0)
    monkeypatch.setattr(jg, "_client", lambda: _FakeClient([decision]))
    assert jg.guard_tool_call("run_command", {"cmd": "npm run build"}) is None
    assert jev_metrics.snapshot()["blocked_tool_calls"] == 0


def test_guard_never_blocks_when_jev_call_fails(monkeypatch):
    _enable(monkeypatch)

    def boom():
        raise RuntimeError("no network")

    class _Raising:
        def decide(self, state, questions):
            raise RuntimeError("timeout")

    monkeypatch.setattr(jg, "_client", lambda: _Raising())
    assert jg.guard_tool_call("run_command", {"cmd": "echo hi"}) is None




def test_jev_runs_on_openrouter_with_the_openrouter_key(isolated, monkeypatch):
    """Jev through OpenRouter's Decisions API: the OpenRouter host's key, the OpenRouter model slug."""
    from backend.services import api_access

    monkeypatch.setattr(jg, "_settings", lambda: {"enabled": True, "backend": "openrouter"})
    assert jg._client() is None  # no OpenRouter key: Jev simply has no say
    api_access.set_api_key("openrouter", "sk-or-test")
    client = jg._client()
    assert client.api_key == "sk-or-test" and client.model == "typesafe/jev-latest"
    assert client.base_url == "https://openrouter.ai/api/alpha/decisions"


def test_jev_runs_on_typesafe_with_the_typesafe_key(isolated, monkeypatch):
    """Jev on TypeSafe's official API: the TypeSafe entry of the key catalog, the plain model name."""
    from backend.services import api_access

    monkeypatch.setattr(jg, "_settings", lambda: {"enabled": True, "backend": "typesafe"})
    api_access.set_api_key("openrouter", "sk-or-test")
    assert jg._client() is None  # the OpenRouter key does not count for TypeSafe
    api_access.set_api_key("typesafe", "ts-test")
    client = jg._client()
    assert client.api_key == "ts-test" and client.model == "jev-latest"
    assert client.base_url == "https://api.typesafe.ai/v1/systemone"


def test_an_old_jev_only_key_moves_into_the_key_catalog(isolated):
    """Jev used to keep its own key in its settings; keys now live in one place."""
    from backend.services import api_access, settings_store

    settings_store.save_settings({"jev": {"enabled": True, "api_key": "ts-legacy-key", "model": "jev-latest"}})
    cfg = settings_store.jev_settings()
    assert cfg["backend"] == "typesafe" and "api_key" not in cfg
    assert api_access.resolved_key("typesafe") == "ts-legacy-key"
    assert not str((settings_store.load_settings().get("jev") or {}).get("api_key") or "")  # erased

    settings_store.save_settings({"jev": {"enabled": True, "api_key": "sk-or-legacy", "backend": "local"}})
    cfg = settings_store.jev_settings()
    assert cfg["backend"] == "openrouter" and api_access.resolved_key("openrouter") == "sk-or-legacy"
