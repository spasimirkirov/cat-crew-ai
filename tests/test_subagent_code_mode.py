"""Subagent, code mode, and second-wave connector smoke tests."""

from __future__ import annotations

from jinx_ai.connectors.model import ConnectorConfig
from jinx_ai.harness.code_mode import run_code_mode
from jinx_ai.harness.runtime import nest_depth, push_depth, reset_depth, set_provider
from jinx_ai.harness.subagent import SUBAGENT_TOOL_NAMES, run_subagent
from jinx_ai.tools import tools_schema_for
from jinx_ai.activity_timeline import format_timeline


def test_developer_schema_includes_subagent_and_code_mode():
    names = {
        item.get("function", {}).get("name") for item in tools_schema_for("developer")
    }
    assert "run_subagent" in names
    assert "run_code_mode" in names


def test_code_mode_runs_simple_print(tmp_path, monkeypatch):
    monkeypatch.setattr("jinx_ai.session_fs.SCRATCH_ROOT", tmp_path / "scratch")
    from jinx_ai.trace import bind_session

    bind_session("code-mode-test")
    out = run_code_mode("print(2 + 2)")
    assert "4" in out
    assert "code mode" in out.lower()


def test_code_mode_blocks_shell():
    out = run_code_mode("import os; os.system('echo hi')")
    assert "Refused" in out


def test_subagent_requires_provider():
    out = run_subagent("list files")
    assert "no chat provider" in out.lower() or "Error" in out


def test_subagent_depth_limit():
    class Dummy:
        def run_agent(self, *a, **k):
            return "ok"

    token = set_provider(Dummy())
    depth = push_depth()
    try:
        assert nest_depth() == 1
        out = run_subagent("nested")
        assert "depth" in out.lower()
    finally:
        reset_depth(depth)
        from jinx_ai.harness.runtime import reset_provider

        reset_provider(token)


def test_subagent_tool_subset_excludes_nesting():
    assert "run_subagent" not in SUBAGENT_TOOL_NAMES
    assert "restart_jinx" not in SUBAGENT_TOOL_NAMES
    assert "read_file" in SUBAGENT_TOOL_NAMES


def test_linear_loader_needs_token():
    from jinx_ai.connectors.drivers.linear import load_linear_tools

    schemas, _exec, summary = load_linear_tools(
        ConnectorConfig(id="linear", name="Linear", kind="python", driver="linear")
    )
    assert schemas == []
    assert "API key" in summary or "token" in summary.lower() or "key" in summary.lower()


def test_notion_and_slack_loaders_need_token():
    from jinx_ai.connectors.drivers.notion import load_notion_tools
    from jinx_ai.connectors.drivers.slack import load_slack_tools

    s1, _, m1 = load_notion_tools(
        ConnectorConfig(id="notion", name="Notion", kind="python", driver="notion")
    )
    s2, _, m2 = load_slack_tools(
        ConnectorConfig(id="slack", name="Slack", kind="python", driver="slack")
    )
    assert s1 == [] and s2 == []
    assert "token" in m1.lower() and "token" in m2.lower()


def test_format_timeline_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("jinx_ai.trace.TRACES_DIR", tmp_path)
    text = format_timeline("missing-session")
    assert "No tool activity" in text or "missing" in text.lower() or text
