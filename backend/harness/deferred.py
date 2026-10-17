"""Deferred connector tools — meta tools + on-demand schemas (TrueForge-style).

Built-in tools stay in ``TOOLS_SCHEMA``. Connector schemas stay out of context until
``load_connector_tools`` registers them for the current agent turn (ContextVar).
``run_agent`` re-reads ``extra_tool_schemas()`` each round.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

from ..connectors import enabled_connectors, get_connector, load_catalog

ToolExecutor = Callable[[dict[str, Any]], str]

_extra_schemas: ContextVar[list[dict[str, Any]]] = ContextVar(
    "catcrew_deferred_schemas", default=[]
)
_extra_exec: ContextVar[dict[str, ToolExecutor]] = ContextVar(
    "catcrew_deferred_exec", default={}
)


def clear_loaded_tools() -> None:
    _extra_schemas.set([])
    _extra_exec.set({})


def snapshot_loaded_tools() -> tuple[list[dict[str, Any]], dict[str, ToolExecutor]]:
    """Copy currently registered connector schemas/executors (for nested bots)."""
    return list(_extra_schemas.get() or []), dict(_extra_exec.get() or {})


def restore_loaded_tools(
    schemas: list[dict[str, Any]],
    executors: dict[str, ToolExecutor],
) -> None:
    """Replace the deferred tool registry (used after a nested bot turn)."""
    _extra_schemas.set(list(schemas or []))
    _extra_exec.set(dict(executors or {}))


def extra_tool_schemas() -> list[dict[str, Any]]:
    return list(_extra_schemas.get() or [])


def execute_loaded_tool(name: str, arguments: dict[str, Any] | None = None) -> str | None:
    """Run a deferred connector tool; None if name is not loaded."""
    fn = (_extra_exec.get() or {}).get(name)
    if fn is None:
        return None
    return fn(dict(arguments or {}))


def register_tools(schemas: list[dict[str, Any]], executors: dict[str, ToolExecutor]) -> None:
    """Public entry point for a non-connector source of deferred, per-turn
    tools — currently ``platform/tool_registry.py``'s DB-backed per-agent
    tool bindings (Phase 15's Letta-style registry). Same mechanism a
    connector's tools register through; just a name a second caller is
    allowed to use instead of reaching for the underscore-prefixed
    internal helper directly."""
    _register(schemas, executors)


def _register(schemas: list[dict[str, Any]], executors: dict[str, ToolExecutor]) -> None:
    current_s = list(_extra_schemas.get() or [])
    current_e = dict(_extra_exec.get() or {})
    names = {
        (item.get("function") or {}).get("name")
        for item in current_s
        if isinstance(item, dict)
    }
    for item in schemas:
        n = (item.get("function") or {}).get("name")
        if n and n not in names:
            current_s.append(item)
            names.add(n)
    current_e.update(executors)
    _extra_schemas.set(current_s)
    _extra_exec.set(current_e)


def list_connectors_text() -> str:
    rows = enabled_connectors()
    if not rows:
        catalog = load_catalog()
        names = ", ".join(item.name for item in catalog[:8]) or "(none)"
        return (
            "No connectors enabled. Open Settings → Connectors, enable one, "
            f"and paste a token if needed. Catalog: {names}."
        )
    lines = ["Enabled connectors (load tools on demand with load_connector_tools):"]
    for item in rows:
        secret = "token set" if item.token or item.headers else "no token"
        lines.append(
            f"- {item.id}: {item.display_name()} [{item.kind}] ({secret}) — "
            f"{item.docs_url or 'no docs url'}"
        )
    return "\n".join(lines)


def _ensure_auth(cfg) -> str | None:
    """If auth is required and missing, pause via ask_user for a pasted token."""
    if cfg.auth in {"", "none"}:
        return None
    if cfg.token or cfg.headers:
        return None
    # GitHub REST allows anonymous public reads (rate-limited).
    if (cfg.driver or cfg.id) == "github":
        return None
    from ..connectors.store import get_connector as _refresh, set_token
    from ..harness.ask_user import ask_user
    from ..harness.events import emit_auth_required

    emit_auth_required(cfg.id, cfg.display_name())
    answer = ask_user(
        f"Connector {cfg.display_name()} needs a token. Paste it here (or Skip).",
        [],
    )
    if not answer.lower().startswith("user answered:"):
        return f"Auth required for {cfg.display_name()} — cancelled."
    token = answer[len("User answered:") :].strip()
    if not token:
        return f"Auth required for {cfg.display_name()} — empty token."
    set_token(cfg.id, token)
    refreshed = _refresh(cfg.id)
    if refreshed is not None:
        cfg.token = refreshed.token
    return None


def load_connector_tools_text(connector_id: str) -> str:
    cid = str(connector_id or "").strip()
    if not cid:
        return "Error: pass connector_id (e.g. github)."
    cfg = get_connector(cid)
    if cfg is None or not cfg.enabled:
        return (
            f"Connector {cid!r} is not enabled. "
            "Enable it under Settings → Connectors first."
        )

    auth_err = _ensure_auth(cfg)
    if auth_err:
        return auth_err

    if cfg.kind == "python":
        driver = (cfg.driver or cfg.id or "").strip().lower()
        loader = None
        if driver == "github":
            from ..connectors.drivers.github import load_github_tools as loader
        elif driver == "linear":
            from ..connectors.drivers.linear import load_linear_tools as loader
        elif driver == "notion":
            from ..connectors.drivers.notion import load_notion_tools as loader
        elif driver == "slack":
            from ..connectors.drivers.slack import load_slack_tools as loader
        if loader is not None:
            schemas, executors, summary = loader(cfg)
            if not schemas:
                return summary
            _register(schemas, executors)
            names = [
                (s.get("function") or {}).get("name")
                for s in schemas
                if isinstance(s, dict)
            ]
            return (
                f"{summary}\n"
                f"Registered for this turn: {', '.join(n for n in names if n)}.\n"
                "Call them on the next tool round (schemas are now in the tool list)."
            )
        return (
            f"Connector {cfg.display_name()}: Python driver `{driver}` is registered "
            "but not implemented yet. Token "
            f"{'present' if cfg.token else 'missing'}."
        )

    if cfg.kind == "mcp_http":
        from ..connectors.mcp_client import load_mcp_tools

        if not cfg.base_url:
            from ..connectors.mcp_client import describe_mcp_connector

            return describe_mcp_connector(cfg)
        try:
            schemas, executors, summary = load_mcp_tools(cfg)
        except Exception as exc:  # noqa: BLE001
            return f"Failed to load MCP tools for {cfg.display_name()}: {exc}"
        _register(schemas, executors)
        names = [
            (s.get("function") or {}).get("name") for s in schemas if isinstance(s, dict)
        ]
        return (
            f"{summary}\n"
            f"Registered for this turn: {', '.join(n for n in names if n) or '(none)'}.\n"
            "Call them on the next tool round."
        )

    return f"Connector {cfg.display_name()}: kind={cfg.kind} — no tools to load yet."


def deferred_tool_schemas() -> list[dict[str, Any]]:
    """OpenAI tool schema for the two meta tools (always cheap in context)."""

    def _fn(name: str, description: str, props: dict, required: list[str]) -> dict:
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

    return [
        _fn(
            "list_connectors",
            "List enabled external connectors (GitHub, Linear, …). "
            "Does not load remote tool schemas — call load_connector_tools next.",
            {},
            [],
        ),
        _fn(
            "load_connector_tools",
            "Discover and register tools for one enabled connector by id. "
            "Use after list_connectors. Schemas stay out of context until then.",
            {
                "connector_id": {
                    "type": "string",
                    "description": "Catalog id, e.g. github, linear, notion",
                }
            },
            ["connector_id"],
        ),
    ]
