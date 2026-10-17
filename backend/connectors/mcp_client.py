"""Thin MCP HTTP client (JSON-RPC over streamable HTTP).

No official SDK dependency — uses ``requests``. Supports ``application/json``
responses and simple SSE ``data:`` frames. OAuth / full SSE listen is later.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from .model import ConnectorConfig

ToolExecutor = Callable[[dict[str, Any]], str]

_PROTOCOL = "2025-03-26"
_id_lock = threading.Lock()
_next_id = 1


def _rpc_id() -> int:
    global _next_id
    with _id_lock:
        n = _next_id
        _next_id += 1
        return n


@dataclass
class McpSession:
    base_url: str
    headers: dict[str, str] = field(default_factory=dict)
    session_id: str = ""
    timeout: float = 45.0

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": _rpc_id(),
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _PROTOCOL,
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        resp = requests.post(
            self.base_url,
            data=json.dumps(payload),
            headers=headers,
            timeout=self.timeout,
        )
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        if resp.status_code >= 400:
            raise RuntimeError(
                f"MCP HTTP {resp.status_code} for {method}: {resp.text[:600]}"
            )
        body = _parse_mcp_body(resp)
        if isinstance(body, dict) and body.get("error"):
            err = body["error"]
            raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
        if isinstance(body, dict) and "result" in body:
            return body["result"]
        return body

    def initialize(self) -> Any:
        result = self.request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "cat-crew-ai", "version": "0.1"},
            },
        )
        # notifications/initialized has no id / response
        try:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": _PROTOCOL,
                **self.headers,
            }
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
            requests.post(
                self.base_url,
                data=json.dumps(
                    {"jsonrpc": "2.0", "method": "notifications/initialized"}
                ),
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException:
            pass
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return []
        return [t for t in tools if isinstance(t, dict) and t.get("name")]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
        )
        return _format_tool_result(result)


def _parse_mcp_body(resp: requests.Response) -> Any:
    ctype = (resp.headers.get("Content-Type") or "").lower()
    text = resp.text or ""
    if "text/event-stream" in ctype or text.lstrip().startswith("event:"):
        return _parse_sse_json(text)
    if not text.strip():
        return {}
    return json.loads(text)


def _parse_sse_json(text: str) -> Any:
    """Take the last JSON object from SSE ``data:`` lines."""
    chunks: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            chunks.append(line[5:].lstrip())
    if not chunks:
        # Some servers send one JSON blob without SSE framing.
        return json.loads(text) if text.strip() else {}
    blob = "\n".join(chunks)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Multiple events — parse last valid JSON object
        last: Any = {}
        for part in re.split(r"\n\n+", blob):
            part = part.strip()
            if not part:
                continue
            try:
                last = json.loads(part)
            except json.JSONDecodeError:
                continue
        return last


def _format_tool_result(result: Any) -> str:
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)[:12_000]
    if result.get("isError"):
        return f"MCP tool error: {_content_text(result)}"
    return _content_text(result) or json.dumps(result, ensure_ascii=False, indent=2)[
        :12_000
    ]


def _content_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
    return "\n".join(p for p in parts if p).strip()


def auth_headers(cfg: ConnectorConfig) -> dict[str, str]:
    headers = {str(k): str(v) for k, v in (cfg.headers or {}).items() if k and v}
    token = (cfg.token or "").strip()
    if not token:
        return headers
    name = (cfg.header_name or "Authorization").strip() or "Authorization"
    if cfg.auth == "bearer" or name.lower() == "authorization":
        if token.lower().startswith("bearer "):
            headers[name] = token
        else:
            headers[name] = f"Bearer {token}"
    else:
        headers[name] = token
    return headers


def open_session(cfg: ConnectorConfig) -> McpSession:
    url = (cfg.base_url or "").strip()
    if not url:
        raise ValueError("Connector has no base_url for MCP HTTP.")
    # Allow trailing path without forcing /mcp
    session = McpSession(base_url=url.rstrip("/") if url.endswith("/") else url)
    # Keep user URL as-is (many endpoints are …/mcp already)
    session.base_url = url
    session.headers = auth_headers(cfg)
    session.initialize()
    return session


def describe_mcp_connector(cfg: ConnectorConfig) -> str:
    name = cfg.display_name()
    if not cfg.base_url:
        return (
            f"Connector {name}: set a MCP HTTP base_url under Settings → Connectors "
            f"(auth={cfg.auth}, token={'yes' if cfg.token else 'no'})."
        )
    try:
        session = open_session(cfg)
        tools = session.list_tools()
    except Exception as exc:  # noqa: BLE001
        return f"Connector {name}: MCP probe failed — {exc}"
    names = [str(t.get("name")) for t in tools[:40]]
    return (
        f"Connector {name} at {cfg.base_url}: {len(tools)} tool(s). "
        f"Sample: {', '.join(names) or '(none)'}."
    )


def load_mcp_tools(
    cfg: ConnectorConfig,
) -> tuple[list[dict[str, Any]], dict[str, ToolExecutor], str]:
    """List remote tools and register ``connector__{id}__{tool}`` executors."""
    session = open_session(cfg)
    remote = session.list_tools()
    if cfg.tool_allowlist:
        allow = {x.lower() for x in cfg.tool_allowlist}
        remote = [t for t in remote if str(t.get("name") or "").lower() in allow]
    if cfg.tool_denylist:
        deny = {x.lower() for x in cfg.tool_denylist}
        remote = [t for t in remote if str(t.get("name") or "").lower() not in deny]

    schemas: list[dict[str, Any]] = []
    executors: dict[str, ToolExecutor] = {}
    cid = cfg.id

    for tool in remote:
        remote_name = str(tool.get("name") or "").strip()
        if not remote_name:
            continue
        safe = re.sub(r"[^a-zA-Z0-9_]+", "_", remote_name).strip("_") or "tool"
        full = f"connector__{cid}__{safe}"
        desc = str(tool.get("description") or remote_name)
        schema_props = tool.get("inputSchema") or tool.get("input_schema") or {
            "type": "object",
            "properties": {},
        }
        if not isinstance(schema_props, dict):
            schema_props = {"type": "object", "properties": {}}
        params = dict(schema_props)
        params.setdefault("type", "object")
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": full,
                    "description": f"{cfg.display_name()}: {desc}",
                    "parameters": params,
                },
            }
        )

        def _make(rn: str = remote_name, sess: McpSession = session) -> ToolExecutor:
            def _run(arguments: dict[str, Any]) -> str:
                return sess.call_tool(rn, arguments)

            return _run

        executors[full] = _make()

    summary = (
        f"Loaded {len(schemas)} MCP tool(s) from {cfg.display_name()} "
        f"({cfg.base_url})."
    )
    return schemas, executors, summary


