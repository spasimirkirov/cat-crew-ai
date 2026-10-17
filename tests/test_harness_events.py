"""Harness events, offload, deferred connectors, approvals."""

from __future__ import annotations

from pathlib import Path

from backend.connectors.model import ConnectorConfig
from backend.harness.approvals import (
    gate_tool,
    needs_approval,
    reset_approval_hook,
    set_approval_hook,
)
from backend.harness.deferred import (
    clear_loaded_tools,
    extra_tool_schemas,
    list_connectors_text,
    load_connector_tools_text,
)
from backend.harness.events import emit_activity, emit_tool_call, tool_info_for_name
from backend.harness.offload import maybe_offload_tool_result
from backend.services.session_fs import ensure_session_work_dir
from backend.services.settings_store import save_agent_settings
from backend.tools import execute_tool, tools_schema_for
from backend.services.trace import bind_session


def test_tool_info_connector_vs_builtin():
    info = tool_info_for_name("connector__github__list_issues")
    assert info.source == "connector"
    assert info.connector_id == "github"
    assert "list_issues" in info.label()
    assert tool_info_for_name("write_file").source == "builtin"


def test_emit_activity_does_not_raise():
    emit_activity("Thinking", "hi")
    emit_tool_call("read_file", {"path": "a.py"})


def test_offload_writes_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.services.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    bind_session("offload-test")
    ensure_session_work_dir("offload-test")
    big = "x" * 12_000
    out = maybe_offload_tool_result("run_command", big, threshold=4_000, preview=200)
    assert "offloaded" in out.lower()
    assert "tool_results" in out
    assert "preview" in out.lower()
    assert len(out) < len(big)
    folder = ensure_session_work_dir("offload-test") / "tool_results"
    assert folder.is_dir()
    assert any(folder.glob("*.txt"))


def test_deferred_meta_tools_in_developer_schema():
    names = {
        item.get("function", {}).get("name") for item in tools_schema_for("developer")
    }
    assert "list_connectors" in names
    assert "load_connector_tools" in names
    assert "list_connectors" not in {
        item.get("function", {}).get("name") for item in tools_schema_for("assistant")
    }


def test_list_connectors_and_github_load(tmp_path, monkeypatch):
    store = tmp_path / "connectors.json"
    monkeypatch.setattr("backend.connectors.store.STORE_PATH", store)
    clear_loaded_tools()
    text = list_connectors_text()
    assert "connector" in text.lower() or "Settings" in text

    from backend.connectors import set_enabled, set_token, upsert_connector

    upsert_connector(
        ConnectorConfig(
            id="github",
            name="GitHub",
            kind="python",
            driver="github",
            enabled=True,
            auth="bearer",
        )
    )
    set_enabled("github", True)
    set_token("github", "")
    out = load_connector_tools_text("github")
    assert "Registered" in out or "GitHub" in out
    extras = extra_tool_schemas()
    names = {(s.get("function") or {}).get("name") for s in extras}
    assert "connector__github__list_issues" in names
    assert "github" in execute_tool("list_connectors", {}).lower()


def test_approval_gate_emits_when_enabled(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("backend.services.settings_store.SETTINGS_PATH", settings)
    save_agent_settings({"tool_approval": True})
    assert needs_approval("run_command")
    assert needs_approval("connector__github__x")
    assert not needs_approval("read_file")
    assert gate_tool("run_command", {"command": "echo"}) is None

    def deny(_name, _args):
        return "deny"

    token = set_approval_hook(deny)
    try:
        msg = gate_tool("write_file", {"path": "a"})
        assert msg and "Denied" in msg
    finally:
        reset_approval_hook(token)
        save_agent_settings({"tool_approval": False})


def test_ask_user_with_hook():
    from backend.harness.ask_user import ask_user, reset_ask_user_hook, set_ask_user_hook

    token = set_ask_user_hook(lambda prompt, choices: choices[0] if choices else "ok")
    try:
        out = ask_user("Pick one", ["a", "b"])
        assert "User answered: a" in out
    finally:
        reset_ask_user_hook(token)


def test_catalog_loads():
    from backend.connectors import load_catalog

    cats = load_catalog()
    ids = {c.id for c in cats}
    assert "github" in ids
    assert "linear" in ids
