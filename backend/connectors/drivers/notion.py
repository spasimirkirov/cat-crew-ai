"""Notion REST connector — tools via load_connector_tools."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ..model import ConnectorConfig

ToolExecutor = Callable[[dict[str, Any]], str]
_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


def _headers(cfg: ConnectorConfig) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Notion-Version": _VERSION,
        "User-Agent": "CatCrewAI",
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


def _request(
    cfg: ConnectorConfig,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(f"{_API}{path}", data=data, headers=_headers(cfg), method=method)
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as exc:
        body_t = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"Notion HTTP {exc.code}: {body_t or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Notion network error: {exc.reason}") from exc


def _tool(name: str) -> str:
    return f"connector__notion__{name}"


def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def _title_from_page(page: dict[str, Any]) -> str:
    props = page.get("properties") or {}
    for value in props.values():
        if not isinstance(value, dict):
            continue
        if value.get("type") == "title":
            parts = value.get("title") or []
            return "".join(
                str(p.get("plain_text") or "") for p in parts if isinstance(p, dict)
            )
    return str(page.get("id") or "")


def load_notion_tools(
    cfg: ConnectorConfig,
) -> tuple[list[dict[str, Any]], dict[str, ToolExecutor], str]:
    if not cfg.token:
        return [], {}, "Notion needs an integration token under Settings → Connectors."

    def search(args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        limit = max(1, min(int(args.get("limit") or 8), 20))
        data = _request(cfg, "POST", "/search", {"query": query, "page_size": limit})
        results = data.get("results") or []
        lines = []
        for item in results:
            if not isinstance(item, dict):
                continue
            kind = item.get("object")
            title = _title_from_page(item) if kind == "page" else str(item.get("id"))
            url = item.get("url") or ""
            lines.append(f"[{kind}] {title} {url}".strip())
        return "\n".join(lines) or "(no results)"

    def get_page(args: dict[str, Any]) -> str:
        page_id = str(args.get("page_id") or args.get("id") or "").strip()
        if not page_id:
            return "Error: page_id is required."
        data = _request(cfg, "GET", f"/pages/{quote(page_id)}")
        keep = {
            "id": data.get("id"),
            "url": data.get("url"),
            "title": _title_from_page(data),
            "last_edited_time": data.get("last_edited_time"),
        }
        return json.dumps(keep, indent=2, ensure_ascii=False)

    schemas = [
        _schema(
            _tool("search"),
            "Notion: search pages/databases shared with the integration.",
            {"query": {"type": "string"}, "limit": {"type": "integer"}},
            [],
        ),
        _schema(
            _tool("get_page"),
            "Notion: get page metadata by id.",
            {"page_id": {"type": "string"}},
            ["page_id"],
        ),
    ]
    executors = {_tool("search"): search, _tool("get_page"): get_page}
    return schemas, executors, f"Loaded Notion REST tools for {cfg.display_name()}."
