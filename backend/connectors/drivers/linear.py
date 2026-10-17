"""Linear GraphQL connector — tools via load_connector_tools."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..model import ConnectorConfig

ToolExecutor = Callable[[dict[str, Any]], str]
_API = "https://api.linear.app/graphql"


def _headers(cfg: ConnectorConfig) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "CatCrewAI",
    }
    token = (cfg.token or "").strip()
    if token:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else token
    for key, value in (cfg.headers or {}).items():
        if key and value:
            headers[str(key)] = str(value)
    return headers


def _gql(cfg: ConnectorConfig, query: str, variables: dict[str, Any] | None = None) -> Any:
    payload = {"query": query, "variables": variables or {}}
    req = Request(
        _API,
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(cfg),
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"Linear HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Linear network error: {exc.reason}") from exc
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False)[:800])
    return data.get("data") or {}


def _tool(name: str) -> str:
    return f"connector__linear__{name}"


def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def load_linear_tools(
    cfg: ConnectorConfig,
) -> tuple[list[dict[str, Any]], dict[str, ToolExecutor], str]:
    if not cfg.token:
        return [], {}, "Linear needs an API key under Settings → Connectors."

    def viewer(_args: dict[str, Any]) -> str:
        data = _gql(cfg, "{ viewer { id name email } }")
        return json.dumps(data.get("viewer") or data, indent=2, ensure_ascii=False)

    def list_issues(args: dict[str, Any]) -> str:
        limit = max(1, min(int(args.get("limit") or 10), 25))
        team = str(args.get("team") or args.get("team_key") or "").strip()
        filt = f'team: {{ key: {{ eq: "{team}" }} }}' if team else ""
        query = f"""
        {{
          issues(first: {limit}{', filter: { ' + filt + ' }' if filt else ''}) {{
            nodes {{ identifier title state {{ name }} url }}
          }}
        }}
        """
        data = _gql(cfg, query)
        nodes = ((data.get("issues") or {}).get("nodes")) or []
        lines = [
            f"{n.get('identifier')} {n.get('title')} "
            f"[{(n.get('state') or {}).get('name')}] {n.get('url')}"
            for n in nodes
            if isinstance(n, dict)
        ]
        return "\n".join(lines) or "(no issues)"

    def search_issues(args: dict[str, Any]) -> str:
        term = str(args.get("query") or "").strip()
        if not term:
            return "Error: query is required."
        limit = max(1, min(int(args.get("limit") or 8), 20))
        query = """
        query($term: String!, $first: Int!) {
          searchIssues(term: $term, first: $first) {
            nodes { identifier title url state { name } }
          }
        }
        """
        data = _gql(cfg, query, {"term": term, "first": limit})
        nodes = ((data.get("searchIssues") or {}).get("nodes")) or []
        lines = [
            f"{n.get('identifier')} {n.get('title')} — {n.get('url')}"
            for n in nodes
            if isinstance(n, dict)
        ]
        return "\n".join(lines) or "(no matches)"

    schemas = [
        _schema(_tool("viewer"), "Linear: current user.", {}, []),
        _schema(
            _tool("list_issues"),
            "Linear: list recent issues (optional team key).",
            {
                "team": {"type": "string"},
                "limit": {"type": "integer"},
            },
            [],
        ),
        _schema(
            _tool("search_issues"),
            "Linear: search issues by text.",
            {"query": {"type": "string"}, "limit": {"type": "integer"}},
            ["query"],
        ),
    ]
    executors = {
        _tool("viewer"): viewer,
        _tool("list_issues"): list_issues,
        _tool("search_issues"): search_issues,
    }
    return schemas, executors, f"Loaded Linear GraphQL tools for {cfg.display_name()}."
