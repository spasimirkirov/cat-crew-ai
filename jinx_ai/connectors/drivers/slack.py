"""Slack Web API connector — tools via load_connector_tools."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..model import ConnectorConfig

ToolExecutor = Callable[[dict[str, Any]], str]
_API = "https://slack.com/api"


def _headers(cfg: ConnectorConfig) -> dict[str, str]:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Jinx-AI",
    }
    token = (cfg.token or "").strip()
    if token:
        headers["Authorization"] = (
            token if token.lower().startswith("bearer ") else f"Bearer {token}"
        )
    for key, value in (cfg.headers or {}).items():
        if key and value:
            headers[str(key)] = str(value)
    return headers


def _api(cfg: ConnectorConfig, method: str, params: dict[str, Any] | None = None) -> Any:
    body = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    req = Request(
        f"{_API}/{method}",
        data=body.encode("utf-8"),
        headers=_headers(cfg),
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"Slack HTTP {exc.code}: {text or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Slack network error: {exc.reason}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data.get('error') or data}")
    return data


def _tool(name: str) -> str:
    return f"connector__slack__{name}"


def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def load_slack_tools(
    cfg: ConnectorConfig,
) -> tuple[list[dict[str, Any]], dict[str, ToolExecutor], str]:
    if not cfg.token:
        return [], {}, "Slack needs a bot token under Settings → Connectors."

    def auth_test(_args: dict[str, Any]) -> str:
        data = _api(cfg, "auth.test")
        keep = {k: data.get(k) for k in ("user", "user_id", "team", "team_id", "url")}
        return json.dumps(keep, indent=2, ensure_ascii=False)

    def list_channels(args: dict[str, Any]) -> str:
        limit = max(1, min(int(args.get("limit") or 20), 50))
        data = _api(
            cfg,
            "conversations.list",
            {"limit": str(limit), "types": "public_channel,private_channel"},
        )
        channels = data.get("channels") or []
        lines = [
            f"#{c.get('name')} ({c.get('id')})"
            for c in channels
            if isinstance(c, dict)
        ]
        return "\n".join(lines) or "(no channels)"

    def post_message(args: dict[str, Any]) -> str:
        channel = str(args.get("channel") or "").strip()
        text = str(args.get("text") or args.get("message") or "").strip()
        if not channel or not text:
            return "Error: channel and text are required."
        data = _api(cfg, "chat.postMessage", {"channel": channel, "text": text})
        return f"Posted to {channel}: ts={data.get('ts')}"

    schemas = [
        _schema(_tool("auth_test"), "Slack: verify the bot token.", {}, []),
        _schema(
            _tool("list_channels"),
            "Slack: list channels the bot can see.",
            {"limit": {"type": "integer"}},
            [],
        ),
        _schema(
            _tool("post_message"),
            "Slack: post a message to a channel id or name.",
            {
                "channel": {"type": "string"},
                "text": {"type": "string"},
            },
            ["channel", "text"],
        ),
    ]
    executors = {
        _tool("auth_test"): auth_test,
        _tool("list_channels"): list_channels,
        _tool("post_message"): post_message,
    }
    return schemas, executors, f"Loaded Slack Web API tools for {cfg.display_name()}."
