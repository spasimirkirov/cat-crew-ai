"""GitHub REST connector driver — tools load only via load_connector_tools."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..model import ConnectorConfig

ToolExecutor = Callable[[dict[str, Any]], str]

_API = "https://api.github.com"


def _headers(cfg: ConnectorConfig) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "CatCrewAI",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (cfg.token or "").strip()
    if token:
        if token.lower().startswith("bearer "):
            headers["Authorization"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
    for key, value in (cfg.headers or {}).items():
        if key and value:
            headers[str(key)] = str(value)
    return headers


def _get(cfg: ConnectorConfig, path: str, query: dict[str, Any] | None = None) -> Any:
    url = f"{_API}{path}"
    if query:
        clean = {k: v for k, v in query.items() if v not in (None, "")}
        if clean:
            url = f"{url}?{urlencode(clean)}"
    req = Request(url, headers=_headers(cfg), method="GET")
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed GitHub API host
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"GitHub HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub network error: {exc.reason}") from exc


def _tool_name(tool: str) -> str:
    return f"connector__github__{tool}"


def _schema(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


def load_github_tools(
    cfg: ConnectorConfig,
) -> tuple[list[dict[str, Any]], dict[str, ToolExecutor], str]:
    """Return OpenAI schemas + executors for GitHub REST helpers."""

    def get_repo(args: dict[str, Any]) -> str:
        owner = str(args.get("owner") or "").strip()
        repo = str(args.get("repo") or "").strip()
        if not owner or not repo:
            return "Error: owner and repo are required."
        data = _get(cfg, f"/repos/{quote(owner)}/{quote(repo)}")
        keep = {
            k: data.get(k)
            for k in (
                "full_name",
                "description",
                "html_url",
                "default_branch",
                "stargazers_count",
                "open_issues_count",
                "language",
                "private",
            )
            if k in data
        }
        return json.dumps(keep, indent=2, ensure_ascii=False)

    def list_issues(args: dict[str, Any]) -> str:
        owner = str(args.get("owner") or "").strip()
        repo = str(args.get("repo") or "").strip()
        if not owner or not repo:
            return "Error: owner and repo are required."
        state = str(args.get("state") or "open").strip() or "open"
        limit = max(1, min(int(args.get("limit") or 10), 30))
        data = _get(
            cfg,
            f"/repos/{quote(owner)}/{quote(repo)}/issues",
            {"state": state, "per_page": limit},
        )
        if not isinstance(data, list):
            return json.dumps(data, indent=2, ensure_ascii=False)[:4000]
        lines = []
        for item in data:
            if item.get("pull_request"):
                continue
            lines.append(
                f"#{item.get('number')} {item.get('title')} "
                f"[{item.get('state')}] {item.get('html_url')}"
            )
        return "\n".join(lines) or "(no issues)"

    def search_issues(args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "Error: query is required (GitHub search syntax)."
        limit = max(1, min(int(args.get("limit") or 8), 20))
        data = _get(cfg, "/search/issues", {"q": query, "per_page": limit})
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return json.dumps(data, indent=2, ensure_ascii=False)[:4000]
        lines = [
            f"#{it.get('number')} {it.get('title')} — {it.get('html_url')}" for it in items
        ]
        total = data.get("total_count", len(lines))
        return f"total≈{total}\n" + ("\n".join(lines) if lines else "(no matches)")

    schemas = [
        _schema(
            _tool_name("get_repo"),
            "GitHub: get repository metadata (stars, default branch, open issues).",
            {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
            },
            ["owner", "repo"],
        ),
        _schema(
            _tool_name("list_issues"),
            "GitHub: list issues for a repository (skips PRs).",
            {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "state": {"type": "string", "description": "open | closed | all"},
                "limit": {"type": "integer"},
            },
            ["owner", "repo"],
        ),
        _schema(
            _tool_name("search_issues"),
            "GitHub: search issues/PRs with GitHub search syntax "
            "(e.g. repo:owner/name is:issue label:bug).",
            {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            ["query"],
        ),
    ]
    executors: dict[str, ToolExecutor] = {
        _tool_name("get_repo"): get_repo,
        _tool_name("list_issues"): list_issues,
        _tool_name("search_issues"): search_issues,
    }
    auth = "authenticated" if cfg.token else "anonymous (rate-limited)"
    summary = (
        f"Loaded GitHub REST tools for {cfg.display_name()} ({auth}). "
        "Needs a PAT under Settings → Connectors for private repos / higher limits."
    )
    return schemas, executors, summary
