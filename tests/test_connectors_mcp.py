"""MCP HTTP client unit tests (mocked transport)."""

from __future__ import annotations

import json

from backend.connectors.mcp_client import (
    McpSession,
    _format_tool_result,
    _parse_sse_json,
    load_mcp_tools,
)
from backend.connectors.model import ConnectorConfig


class _FakeResp:
    def __init__(self, payload, status=200, headers=None, as_sse=False):
        self.status_code = status
        self.headers = headers or {"Content-Type": "application/json"}
        if as_sse:
            self.headers["Content-Type"] = "text/event-stream"
            self.text = f"event: message\ndata: {json.dumps(payload)}\n\n"
        else:
            self.text = json.dumps(payload)


def test_parse_sse_json():
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    text = f"event: message\ndata: {json.dumps(payload)}\n\n"
    assert _parse_sse_json(text)["result"]["tools"] == []


def test_format_tool_result():
    assert "hello" in _format_tool_result(
        {"content": [{"type": "text", "text": "hello"}]}
    )


def test_session_list_and_call(monkeypatch):
    calls: list[str] = []

    def fake_post(url, data=None, headers=None, timeout=None):
        body = json.loads(data)
        method = body.get("method")
        calls.append(method)
        if method == "initialize":
            return _FakeResp(
                {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "x"}},
                headers={"Mcp-Session-Id": "sess-1"},
            )
        if method == "notifications/initialized":
            return _FakeResp({})
        if method == "tools/list":
            return _FakeResp(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                },
                            }
                        ]
                    },
                },
                as_sse=True,
            )
        if method == "tools/call":
            return _FakeResp(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"content": [{"type": "text", "text": "pong"}]},
                }
            )
        return _FakeResp({"error": {"code": -1, "message": method}}, status=500)

    monkeypatch.setattr("backend.connectors.mcp_client.requests.post", fake_post)
    cfg = ConnectorConfig(
        id="custom-mcp",
        name="Custom",
        kind="mcp_http",
        enabled=True,
        base_url="http://127.0.0.1:9999/mcp",
        token="secret",
        auth="bearer",
    )
    schemas, executors, summary = load_mcp_tools(cfg)
    assert "1 MCP" in summary or "1 MCP tool" in summary
    assert len(schemas) == 1
    name = schemas[0]["function"]["name"]
    assert name.startswith("connector__custom-mcp__")
    assert executors[name]({"text": "hi"}) == "pong"
    assert "initialize" in calls
    assert "tools/list" in calls
