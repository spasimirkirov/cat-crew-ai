"""MCP (Model Context Protocol) client subsystem.

CatCrewAI does not ship tools for every program a cat may have to drive. Programs that already have an
MCP server (Blender, the Unity Editor, ...) are plugged in instead: ``backend/config/mcp_servers.json``
lists them, and this module connects to each one with the official ``mcp`` SDK (v2 API), asks it for its
tools (``tools/list``) and turns those into the function-calling tools the engine already runs.

What lives here

* :class:`MCPManager` (a singleton, :func:`get_manager`) owns one background asyncio loop. The engine is
  threaded and synchronous, the SDK is async, so every connection is a long-lived task on that loop and the
  synchronous entry points (:meth:`MCPManager.call`, :meth:`MCPManager.refresh`, ...) cross over with
  ``run_coroutine_threadsafe``.
* Transports: ``stdio`` (the manager starts the server as a child process), ``streamable-http`` and the
  legacy ``sse``.
* Status per server: ``connected``, ``connecting``, ``reconnecting``, ``disconnected`` or ``disabled``.
  A dead server is retried with a back-off (2 s, 4 s, ... 60 s); *Refresh* and *Reconnect* skip the wait.
  An optional ``probe`` (a cheap tool call or resource read) says whether the *program* behind the server
  (Blender, Unity) answers, not only the server process: ``ready`` needs both.
* Tools are normalised (:func:`openai_schema`), named ``mcp__<server>__<tool>`` and **bound per cat**: a
  server lists the cats it belongs to in ``bind_to`` and :meth:`MCPManager.register_for_agent` adds its tools
  to the *running turn* of those cats only (``harness.deferred``, a ContextVar cleared for every run), so
  Blender's ``bpy`` tools exist while Pixel works and are not even in the schema of any other cat.
* Context frugality: a server with hundreds of tools exposes at most ``max_tools`` of them directly
  (``priority`` patterns first); the rest stay reachable through ``mcp_find_tools`` / ``mcp_call_tool``.

Nothing here returns a secret. ``env`` and ``headers`` may reference environment variables
(``${NAME}``); they are expanded in memory and never appear in a status, an error or a log line.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "mcp_servers.json"
TOOL_PREFIX = "mcp__"
META_FIND = "mcp_find_tools"
META_CALL = "mcp_call_tool"

STATUSES = ("connected", "connecting", "reconnecting", "disconnected", "disabled")
_TRANSPORTS = {
    "stdio": "stdio",
    "streamable-http": "streamable-http",
    "streamable_http": "streamable-http",
    "streamablehttp": "streamable-http",
    "http": "streamable-http",
    "sse": "sse",
}
_BACKOFF_START = 2.0
_BACKOFF_CAP = 60.0
_BACKOFF_CAP_SETUP = 300.0  # a missing command will not appear by itself in a minute
_DESC_LIMIT = 700
_PARAM_DESC_LIMIT = 300
_IMAGE_KEEP = 40


def _log(message: str) -> None:
    print(f"[mcp] {message}", flush=True)


class McpError(RuntimeError):
    """Base class of the errors raised to callers of the manager."""


class McpUnavailable(McpError):
    """The server is not connected (or unknown, or not bound to the caller)."""


class McpCallError(McpError):
    """A call reached the server and failed, or the connection dropped during it."""


class McpSetupError(McpError):
    """A problem in the local setup found before connecting (a missing command...)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


# --------------------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------------------

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value: str) -> str:
    """``${NAME}`` and ``${NAME:-default}`` from the process environment (an unset name is empty)."""

    def sub(match: re.Match[str]) -> str:
        found = os.environ.get(match.group(1))
        if found:
            return found
        return match.group(2) or ""

    return _ENV_REF.sub(sub, str(value))


def expand_path(value: str) -> str:
    """``${NAME}`` references, then a leading ``~`` (the user's home): ``~/.unity/relay/relay_win.exe``.

    A default cannot nest ``${HOME}`` inside ``${NAME:-...}``, so ``~`` is what a config uses to say "in the
    home folder" on every OS (``USERPROFILE`` on Windows).
    """
    text = expand_env(value).strip()
    if text == "~" or text.startswith(("~/", "~\\")):
        text = os.path.expanduser(text)
    return text


def public_url(url: str) -> str:
    """The URL without credentials, query or fragment: what may be shown or logged."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except ValueError:
        return ""


@dataclass
class ServerConfig:
    id: str
    label: str = ""
    icon: str = "🔌"
    description: str = ""
    enabled: bool = True
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    bind_to: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=lambda: ["*"])
    deny: list[str] = field(default_factory=list)
    priority: list[str] = field(default_factory=list)
    max_tools: int = 24
    connect_timeout: float = 60.0
    call_timeout: float = 120.0
    ping_interval: float = 15.0
    protocol: str = "auto"
    probe: dict[str, Any] | None = None
    features: dict[str, Any] = field(default_factory=dict)
    skill: str = ""
    app_label: str = ""
    setup_hint: str = ""

    @property
    def target(self) -> str:
        """Where it connects, safe to show: the command name or the URL without credentials."""
        if self.transport == "stdio":
            return Path(self.command).name if self.command else ""
        return public_url(self.url)

    def signature(self) -> str:
        """Everything a running connection depends on (hashed: it holds expanded secrets): a change means reconnecting."""
        blob = json.dumps(self.__dict__, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


def _strings(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _num(raw: Any, default: float, low: float, high: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def parse_server(server_id: str, raw: dict[str, Any]) -> ServerConfig:
    """One entry of ``mcp_servers.json``; raises ``ValueError`` with a sentence for a broken one."""
    sid = re.sub(r"[^a-z0-9_-]", "", str(server_id).strip().lower())
    if not sid or sid != str(server_id).strip().lower():
        raise ValueError(f"'{server_id}' is not a valid server id (use lowercase letters, digits, - and _)")
    transport = _TRANSPORTS.get(str(raw.get("transport") or "stdio").strip().lower())
    if transport is None:
        raise ValueError(f"'{sid}': unknown transport '{raw.get('transport')}' (use stdio, streamable-http or sse)")
    cfg = ServerConfig(id=sid, transport=transport)
    cfg.label = str(raw.get("label") or sid.title()).strip()
    cfg.icon = str(raw.get("icon") or "🔌").strip()
    cfg.description = str(raw.get("description") or "").strip()
    cfg.enabled = bool(raw.get("enabled", True))
    if transport == "stdio":
        cfg.command = expand_path(str(raw.get("command") or ""))
        if not cfg.command:
            raise ValueError(f"'{sid}': a stdio server needs a 'command'")
        cfg.args = [expand_env(a) for a in _strings(raw.get("args"))]
        cfg.cwd = expand_path(str(raw.get("cwd") or ""))
    else:
        cfg.url = expand_env(str(raw.get("url") or "")).strip()
        if not cfg.url.startswith(("http://", "https://")):
            raise ValueError(f"'{sid}': a {transport} server needs an http(s) 'url'")
    env = raw.get("env") if isinstance(raw.get("env"), dict) else {}
    cfg.env = {str(k): expand_env(str(v)) for k, v in env.items() if expand_env(str(v)) != ""}
    headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
    cfg.headers = {str(k): expand_env(str(v)) for k, v in headers.items() if expand_env(str(v)) != ""}
    cfg.bind_to = [b.lower() for b in _strings(raw.get("bind_to"))]
    cfg.allow = _strings(raw.get("allow")) or ["*"]
    cfg.deny = _strings(raw.get("deny"))
    cfg.priority = _strings(raw.get("priority"))
    cfg.max_tools = int(_num(raw.get("max_tools"), 24, 1, 200))
    cfg.connect_timeout = _num(raw.get("connect_timeout"), 60.0, 3, 600)
    cfg.call_timeout = _num(raw.get("call_timeout"), 120.0, 3, 3600)
    cfg.ping_interval = _num(raw.get("ping_interval"), 15.0, 2, 300)
    protocol = str(raw.get("protocol") or "auto").strip().lower()
    cfg.protocol = protocol if protocol in {"auto", "legacy"} else "auto"
    probe = raw.get("probe")
    if isinstance(probe, dict) and (probe.get("tool") or probe.get("resource")):
        cfg.probe = dict(probe)
    features = raw.get("features")
    cfg.features = dict(features) if isinstance(features, dict) else {}
    cfg.skill = str(raw.get("skill") or "").strip()
    cfg.app_label = str(raw.get("app_label") or "").strip()
    cfg.setup_hint = str(raw.get("setup_hint") or "").strip()
    return cfg


def load_config(path: Path | str | None = None) -> tuple[list[ServerConfig], list[str]]:
    """``(servers, problems)``: what the file defines, and a sentence per thing that could not be read."""
    target = Path(path or os.environ.get("CATCREW_MCP_CONFIG") or CONFIG_PATH)
    if not target.is_file():
        return [], []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], [f"{target.name} could not be read as JSON."]
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return [], [f"{target.name} has no 'servers' object."]
    out: list[ServerConfig] = []
    problems: list[str] = []
    for sid, raw in servers.items():
        if str(sid).startswith("_") or not isinstance(raw, dict):
            continue  # "_comment" style keys
        try:
            out.append(parse_server(str(sid), raw))
        except ValueError as exc:
            problems.append(str(exc))
    return out, problems


# --------------------------------------------------------------------------------------------------
# Tools: naming and schema normalisation
# --------------------------------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def function_name(server_id: str, tool: str, taken: set[str] | None = None) -> str:
    """``mcp__<server>__<tool>`` restricted to what function calling accepts (``[a-zA-Z0-9_-]{1,64}``)."""
    base = TOOL_PREFIX + server_id + "__" + re.sub(r"[^a-zA-Z0-9_-]", "_", tool)
    name = base
    if len(name) > 64 or (taken is not None and name in taken):
        digest = hashlib.sha1(f"{server_id}/{tool}".encode()).hexdigest()[:6]
        name = base[: 64 - 7].rstrip("_") + "_" + digest
    return name


def _resolve_refs(node: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    if depth > 6:
        return {}
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target: Any = defs.get(ref.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                merged = {k: v for k, v in node.items() if k != "$ref"}
                return _resolve_refs({**target, **merged}, defs, depth + 1)
            return {}
        return {k: _resolve_refs(v, defs, depth) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(v, defs, depth) for v in node]
    return node


_DROP_KEYS = {"$schema", "$id", "$defs", "definitions", "additionalProperties", "examples", "title", "$comment"}


def _clean(node: Any) -> Any:
    """Keep what a function-calling provider understands; collapse ``anyOf`` to its first real variant."""
    if isinstance(node, list):
        return [_clean(v) for v in node]
    if not isinstance(node, dict):
        return node
    for key in ("anyOf", "oneOf"):
        variants = node.get(key)
        if isinstance(variants, list) and variants:
            real = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
            chosen = dict(real[0] if real else variants[0])
            rest = {k: v for k, v in node.items() if k not in (key,)}
            node = {**chosen, **rest}
            if len(real) > 1:
                kinds = "/".join(str(v.get("type", "?")) for v in real if isinstance(v, dict))
                node["description"] = _clip((str(node.get("description") or "") + f" (also accepts {kinds})").strip(), _PARAM_DESC_LIMIT)
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in _DROP_KEYS:
            continue  # includes additionalProperties: providers reject a typed map there
        if k == "properties" and isinstance(v, dict):
            out[k] = {name: _clean(prop) for name, prop in v.items()}
        elif k == "description":
            out[k] = _clip(str(v), _PARAM_DESC_LIMIT)
        else:
            out[k] = _clean(v)
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


def openai_schema(function: str, description: str, input_schema: Any) -> dict[str, Any]:
    """An MCP tool definition as the OpenAI-style function schema the providers take."""
    schema = input_schema if isinstance(input_schema, dict) else {}
    defs = {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}
    schema = _resolve_refs(schema, defs) if defs or "$ref" in json.dumps(schema, default=str) else schema
    params = _clean(schema)
    if not isinstance(params, dict) or params.get("type") != "object":
        params = {"type": "object", "properties": {}}
    params.setdefault("properties", {})
    required = [r for r in (params.get("required") or []) if r in params["properties"]]
    if required:
        params["required"] = required
    else:
        params.pop("required", None)
    return {
        "type": "function",
        "function": {"name": function, "description": _clip(description, _DESC_LIMIT) or "MCP tool.", "parameters": params},
    }


@dataclass(frozen=True)
class McpTool:
    server: str
    name: str  # the server's own name for it
    function: str  # what the model calls: mcp__<server>__<name>
    description: str
    schema: dict[str, Any]  # the full OpenAI function schema
    read_only: bool = False

    def param_names(self) -> tuple[list[str], set[str]]:
        params = self.schema["function"]["parameters"]
        return list((params.get("properties") or {}).keys()), set(params.get("required") or [])


def _tool_from_sdk(server_id: str, item: Any, taken: set[str]) -> McpTool:
    name = str(getattr(item, "name", "") or "")
    desc = str(getattr(item, "description", "") or getattr(item, "title", "") or "")
    schema = getattr(item, "input_schema", None)
    if schema is None:  # a v1 style attribute, in case an older SDK is installed
        schema = getattr(item, "inputSchema", None)
    annotations = getattr(item, "annotations", None)
    read_only = bool(getattr(annotations, "read_only_hint", None) or getattr(annotations, "readOnlyHint", None))
    fname = function_name(server_id, name, taken)
    taken.add(fname)
    return McpTool(server_id, name, fname, desc, openai_schema(fname, desc, schema), read_only)


def pick_exposed(tools: list[McpTool], cfg: ServerConfig) -> tuple[list[McpTool], list[McpTool]]:
    """``(shown, on_demand)``: the tools that go into the cat's schema, and the ones left behind ``mcp_call_tool``."""

    def match(tool: McpTool, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(tool.name.lower(), p.lower()) for p in patterns)

    allowed = [t for t in tools if match(t, cfg.allow) and not match(t, cfg.deny)]
    first = [t for t in allowed if match(t, cfg.priority)] if cfg.priority else []
    rest = [t for t in allowed if t not in first]
    ordered = first + rest
    shown = ordered[: cfg.max_tools]
    return shown, ordered[cfg.max_tools :]


# --------------------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------------------


def _data_dir() -> Path:
    """Where this subsystem keeps what it produces: server logs and the images tools return."""
    from backend.config import BASE_DIR

    return BASE_DIR / "data" / "mcp"


def _media_dir(server_id: str) -> Path:
    return _data_dir() / "media" / server_id


def _save_media(server_id: str, mime: str, b64: str, index: int) -> str:
    import base64

    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime, mime.split("/")[-1] or "bin")
    folder = _media_dir(server_id)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{index}.{re.sub(r'[^a-z0-9]', '', ext.lower()) or 'bin'}"
        data = base64.b64decode(b64 or "")
        path.write_bytes(data)
        old = sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime)
        for stale in old[:-_IMAGE_KEEP]:
            stale.unlink(missing_ok=True)
        return f"[{mime} saved to {path} ({max(1, len(data) // 1024)} KB)]"
    except (OSError, ValueError):
        return f"[{mime} returned but could not be saved]"


def format_result(server_id: str, result: Any, limit: int | None = None) -> str:
    """A tool result as the plain text a model reads. Images are saved to disk and referenced by path."""
    from backend.config import TOOL_RESULT_LIMIT

    limit = limit or TOOL_RESULT_LIMIT
    parts: list[str] = []
    for i, block in enumerate(getattr(result, "content", None) or []):
        kind = str(getattr(block, "type", "") or "")
        if kind == "text":
            parts.append(str(getattr(block, "text", "") or ""))
        elif kind in {"image", "audio"}:
            parts.append(_save_media(server_id, str(getattr(block, "mime_type", None) or getattr(block, "mimeType", "") or f"{kind}/bin"), str(getattr(block, "data", "") or ""), i))
        elif kind == "resource_link":
            parts.append(f"[resource {getattr(block, 'uri', '')}]")
        elif kind == "resource":
            res = getattr(block, "resource", None)
            text = getattr(res, "text", None)
            parts.append(str(text) if text else f"[resource {getattr(res, 'uri', '')}]")
    text = "\n".join(p for p in parts if p).strip()
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if not text and structured:
        try:
            text = json.dumps(structured, ensure_ascii=False, indent=1)
        except (TypeError, ValueError):
            text = str(structured)
    if not text:
        text = "(the tool returned no output)"
    if len(text) > limit:
        text = text[:limit] + f"\n…[truncated, {len(text) - limit} more characters]"
    if bool(getattr(result, "is_error", False) or getattr(result, "isError", False)):
        return "Error: " + text
    return text


# --------------------------------------------------------------------------------------------------
# Errors -> one sentence that never carries a secret
# --------------------------------------------------------------------------------------------------


def _leaves(exc: BaseException) -> list[BaseException]:
    group = getattr(exc, "exceptions", None)
    if isinstance(group, (tuple, list)) and group:
        out: list[BaseException] = []
        for inner in group:
            out.extend(_leaves(inner))
        return out
    out = [exc]
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc and not isinstance(exc, (McpSetupError,)):
        out.extend(_leaves(cause)[:1])
    return out


def explain(exc: BaseException, cfg: ServerConfig) -> tuple[str, str]:
    """``(kind, sentence)`` for a failed connection."""
    for e in _leaves(exc):
        name = type(e).__name__
        text = str(e).lower()
        if isinstance(e, McpSetupError):
            return e.kind, str(e)
        if isinstance(e, FileNotFoundError):
            return "not_found", f"The command '{cfg.target}' was not found on this computer."
        if isinstance(e, PermissionError):
            return "not_found", f"The command '{cfg.target}' could not be started (permission denied)."
        if isinstance(e, (TimeoutError, asyncio.TimeoutError)) or name.endswith("Timeout") or name.endswith("TimeoutError"):
            return "timeout", f"No answer from {cfg.target or cfg.id} in {int(cfg.connect_timeout)} s."
        if isinstance(e, ConnectionRefusedError) or name in {"ConnectError", "ConnectTimeout"} or "connection refused" in text or "all connection attempts failed" in text:
            return "refused", f"Nothing is listening at {cfg.target}. Start the program's MCP server, then press Reconnect."
        if name == "HTTPStatusError":
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (401, 403):
                return "auth", f"{cfg.target} refused the connection (HTTP {code}); check its credentials."
            return "http", f"{cfg.target} answered HTTP {code}."
        if name == "MCPError" or name == "McpError":
            code = getattr(e, "code", "")
            if code == -32000 or "closed" in text:
                return "closed", f"{cfg.label or cfg.id} closed the connection right after starting."
            return "protocol", f"{cfg.label or cfg.id} rejected the handshake (MCP error {code})."
        if name in {"ClosedResourceError", "BrokenResourceError", "EndOfStream"}:
            return "closed", f"{cfg.label or cfg.id} closed the connection."
        if isinstance(e, OSError) and cfg.transport == "stdio":
            return "not_found", f"The command '{cfg.target}' could not be started."
    first = _leaves(exc)[0]
    return "error", f"Could not connect to {cfg.label or cfg.id} ({type(first).__name__})."


def resolve_command(command: str, cwd: str = "") -> str:
    """The executable to start: PATH first, then next to this Python (where a venv keeps its scripts)."""
    found = shutil.which(command)
    if found:
        return found
    bindir = Path(sys.executable).resolve().parent
    for candidate in (command, command + ".exe", command + ".cmd"):
        p = bindir / candidate
        if p.is_file():
            return str(p)
    if cwd:
        p = Path(cwd) / command
        if p.is_file():
            return str(p)
    raise McpSetupError("not_found", f"The command '{Path(command).name}' was not found on this computer.")


# --------------------------------------------------------------------------------------------------
# The manager
# --------------------------------------------------------------------------------------------------


class _Server:
    """Live state of one configured server. Fields are written on the manager's loop, read under its lock."""

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self.status = "disabled" if not cfg.enabled else "disconnected"  # "connecting" once a supervisor starts
        self.error = ""
        self.error_kind = ""
        self.bridge = "unknown"  # ok | down | unknown (only meaningful with a probe)
        self.tools: list[McpTool] = []
        self.shown: list[McpTool] = []
        self.on_demand: list[McpTool] = []
        self.ever_connected = False
        self.fail_streak = 0
        self.connected_at = 0.0
        self.checked_at = 0.0
        self.next_retry_at = 0.0
        self.client: Any = None
        self.task: asyncio.Task[Any] | None = None
        self.wake: asyncio.Event | None = None
        self.want_refresh = False
        self.want_reconnect = False
        self.lost = False
        self.req_seq = 0  # bumped by a user request (Refresh / Reconnect)
        self.done_seq = 0  # the last request the supervisor finished handling


class MCPManager:
    """Connects to the configured MCP servers and hands their tools to the cats they are bound to."""

    _instance: "MCPManager | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "MCPManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance


    def __init__(self, configs: list[ServerConfig] | None = None, *, config_path: Path | str | None = None) -> None:
        self._config_path = config_path
        self._fixed_configs = configs is not None
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._servers: dict[str, _Server] = {}
        self._problems: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._available: bool | None = None
        self._read_only: dict[str, bool] = {}
        self._load(configs)

    # -- configuration -----------------------------------------------------------------------------
    def _load(self, configs: list[ServerConfig] | None = None) -> None:
        if configs is None:
            configs, problems = load_config(self._config_path)
        else:
            problems = []
        touched: list[_Server] = []
        added = False
        with self._lock:
            self._problems = problems
            wanted = {c.id: c for c in configs}
            for sid in [s for s in self._servers if s not in wanted]:
                gone = self._servers.pop(sid)  # its supervisor sees it is no longer registered and ends
                gone.status, gone.tools, gone.shown, gone.on_demand = "disabled", [], [], []
                touched.append(gone)
            for sid, cfg in wanted.items():
                current = self._servers.get(sid)
                if current is None:
                    self._servers[sid] = _Server(cfg)
                    added = True
                elif current.cfg.signature() != cfg.signature():
                    was_enabled = current.cfg.enabled
                    current.cfg = cfg
                    if not cfg.enabled:
                        current.status, current.error, current.error_kind = "disabled", "", ""
                        current.tools, current.shown, current.on_demand = [], [], []
                    elif not was_enabled:
                        current.status = "connecting"
                    touched.append(current)
            self._read_only = {t.function: t.read_only for s in self._servers.values() for t in s.tools}
            self._cond.notify_all()
        if self._loop is not None and not self._stopping:
            for srv in touched:
                self._kick(srv, refresh=False, reconnect=True)
            if added:
                self._spawn_missing()

    def mcp_installed(self) -> bool:
        if self._available is None:
            try:
                import mcp  # noqa: F401
                from mcp import Client  # noqa: F401

                self._available = True
            except ImportError:
                self._available = False
        return self._available

    # -- lifecycle ---------------------------------------------------------------------------------
    def start(self) -> None:
        """Start connecting in the background. Idempotent, never blocks, never raises."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            ready = threading.Event()

            def run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                loop.call_soon(ready.set)
                try:
                    loop.run_forever()
                finally:
                    try:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    except Exception:  # noqa: BLE001
                        pass
                    loop.close()

            self._thread = threading.Thread(target=run, name="catcrew-mcp", daemon=True)
            self._thread.start()
        ready.wait(5)
        if not self.mcp_installed():
            with self._lock:
                for srv in self._servers.values():
                    if srv.cfg.enabled:
                        srv.status = "disconnected"
                        srv.error_kind = "sdk_missing"
                        srv.error = "The 'mcp' package (version 2 or newer) is missing. Run: pip install -r requirements.txt"
                self._cond.notify_all()
            return
        self._spawn_missing()

    def _spawn_missing(self) -> None:
        loop = self._loop
        if loop is None:
            return

        def go() -> None:
            for srv in list(self._servers.values()):
                if srv.cfg.enabled and (srv.task is None or srv.task.done()):
                    srv.wake = asyncio.Event()
                    if not srv.ever_connected:
                        self._set(srv, status="connecting")
                    srv.task = loop.create_task(self._supervise(srv))

        loop.call_soon_threadsafe(go)

    def stop(self, timeout: float = 8.0) -> None:
        """Close every connection (stdio servers are terminated) and end the loop."""
        with self._lock:
            self._stopping = True
            loop, thread = self._loop, self._thread
        if loop is None or thread is None or not thread.is_alive():
            return

        async def shutdown() -> None:
            tasks = [s.task for s in self._servers.values() if s.task is not None and not s.task.done()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout)
        except Exception:  # noqa: BLE001
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout)
        with self._lock:
            self._thread = None
            self._loop = None
            for srv in self._servers.values():
                if srv.cfg.enabled:
                    srv.status = "disconnected"
                srv.client = None
            self._cond.notify_all()

    # -- the per-server supervisor (runs on the manager's loop) ------------------------------------
    def _set(self, srv: _Server, **fields: Any) -> None:
        with self._cond:
            for key, value in fields.items():
                setattr(srv, key, value)
            self._read_only = {t.function: t.read_only for s in self._servers.values() for t in s.tools}
            self._cond.notify_all()

    def _alive(self, srv: _Server) -> bool:
        return not self._stopping and srv.cfg.enabled and self._servers.get(srv.cfg.id) is srv

    async def _supervise(self, srv: _Server) -> None:
        import anyio

        delay = _BACKOFF_START
        while self._alive(srv):
            handled = srv.req_seq
            asked = srv.want_reconnect or srv.req_seq > srv.done_seq
            srv.want_reconnect = False
            if srv.ever_connected and (srv.fail_streak < 3 or asked):
                self._set(srv, status="reconnecting")
            elif not srv.ever_connected and (srv.fail_streak == 0 or asked):
                self._set(srv, status="connecting")
            reason = "lost"
            try:
                reason = await self._session(srv)
                delay = _BACKOFF_START
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - includes ExceptionGroup from the SDK's task groups
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                kind, message = explain(exc, srv.cfg)
                _log(f"{srv.cfg.id}: {kind}")
                streak = srv.fail_streak + 1
                status = "reconnecting" if srv.ever_connected and streak < 3 else "disconnected"
                self._set(srv, status=status, error=message, error_kind=kind, client=None, bridge="unknown", checked_at=time.time(), fail_streak=streak)
            finally:
                srv.client = None
            if not self._alive(srv):
                break
            if reason == "lost" and srv.status == "connected":
                streak = srv.fail_streak + 1
                self._set(
                    srv, status="reconnecting" if streak < 3 else "disconnected", error=f"{srv.cfg.label} closed the connection.",
                    error_kind="closed", bridge="unknown", fail_streak=streak,
                )
            elif srv.status == "connected":
                self._set(srv, status="reconnecting")
            with self._cond:
                srv.done_seq = max(srv.done_seq, handled)
                self._cond.notify_all()
            if reason == "reconnect" or srv.want_reconnect:
                continue  # asked for: try again now
            cap = _BACKOFF_CAP_SETUP if srv.error_kind in {"not_found", "sdk_missing"} else _BACKOFF_CAP
            self._set(srv, next_retry_at=time.time() + delay)
            with anyio.move_on_after(delay):
                await srv.wake.wait()  # type: ignore[union-attr]
            if srv.wake is not None:
                srv.wake.clear()
            delay = min(delay * 2, cap)
            self._set(srv, next_retry_at=0.0)

    def _make_client(self, srv: _Server, errlog: Any) -> tuple[Any, Any]:
        """``(client, http_client_or_None)``; nothing is entered yet."""
        from mcp import Client, StdioServerParameters, stdio_client

        cfg = srv.cfg

        async def on_message(message: Any) -> None:
            body = getattr(message, "root", message)
            if getattr(body, "method", "") == "notifications/tools/list_changed":
                srv.want_refresh = True
                if srv.wake is not None:
                    srv.wake.set()

        options: dict[str, Any] = {"message_handler": on_message}
        if cfg.protocol == "legacy":
            options["mode"] = "legacy"
        if cfg.transport == "stdio":
            params = StdioServerParameters(
                command=resolve_command(cfg.command, cfg.cwd),
                args=list(cfg.args),
                env=dict(cfg.env) or None,
                cwd=cfg.cwd or None,
            )
            return Client(stdio_client(params, errlog=errlog), **options), None
        if cfg.transport == "sse":
            from mcp.client.sse import sse_client

            return Client(sse_client(cfg.url, headers=dict(cfg.headers) or None), **options), None
        from mcp.client.streamable_http import streamable_http_client

        if cfg.headers:
            import httpx2

            http = httpx2.AsyncClient(headers=dict(cfg.headers), timeout=httpx2.Timeout(30.0, read=300.0))
            return Client(streamable_http_client(cfg.url, http_client=http), **options), http
        return Client(streamable_http_client(cfg.url), **options), None

    async def _list_all(self, client: Any, *, fresh: bool = False) -> list[Any]:
        items: list[Any] = []
        cursor: str | None = None
        for _ in range(30):
            page = await client.list_tools(cursor=cursor, cache_mode="bypass" if fresh else "use")
            items.extend(page.tools)
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        return items

    def _publish_tools(self, srv: _Server, items: list[Any]) -> None:
        taken: set[str] = set()
        tools = [_tool_from_sdk(srv.cfg.id, item, taken) for item in items if getattr(item, "name", "")]
        shown, on_demand = pick_exposed(tools, srv.cfg)
        self._set(srv, tools=tools, shown=shown, on_demand=on_demand)

    async def _probe(self, srv: _Server) -> None:
        import anyio

        probe = srv.cfg.probe
        if not probe or srv.client is None:
            self._set(srv, bridge="unknown")
            return
        ok = False
        try:
            with anyio.fail_after(float(probe.get("timeout") or 8)):
                if probe.get("resource"):
                    await srv.client.read_resource(str(probe["resource"]))
                    ok = True
                else:
                    result = await srv.client.call_tool(str(probe["tool"]), dict(probe.get("arguments") or {}))
                    ok = not bool(getattr(result, "is_error", False))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            ok = False
        self._set(srv, bridge="ok" if ok else "down")

    async def _keepalive(self, srv: _Server) -> None:
        """Ask the server for its tools again. That proves it is still there (a dead process or a closed socket
        fails right here) and doubles as re-discovery: a tool the server added since is picked up even when the
        server never sends ``tools/list_changed``. (``ping`` is not used: the 2026 protocol removed it.)"""
        import anyio

        try:
            with anyio.fail_after(15):
                items = await self._list_all(srv.client, fresh=True)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # An answer of any kind (an MCP error reply) proves the server is there; silence or a closed link does not.
            if type(exc).__name__ in {"MCPError", "McpError"} and getattr(exc, "code", None) != -32000:
                return
            raise
        if {getattr(i, "name", "") for i in items} != {t.name for t in srv.tools}:
            self._publish_tools(srv, items)
            _log(f"{srv.cfg.id}: tool list changed, {len(srv.tools)} tools")

    def _errlog(self, srv: _Server) -> Any:
        if srv.cfg.transport != "stdio":
            return sys.stderr
        try:
            folder = _data_dir() / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            return open(folder / f"{srv.cfg.id}.log", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        except OSError:
            return sys.stderr

    async def _connect(self, stack: AsyncExitStack, client: Any, cfg: ServerConfig) -> list[Any]:
        """Open the client and read its tools, giving up after ``connect_timeout``.

        Not an ``anyio.fail_after`` scope: the client's own task groups stay open after this returns, and a
        scope that closes before them breaks anyio's nesting rules. A timer that cancels this task does not.
        """
        task = asyncio.current_task()
        fired: list[bool] = []

        def give_up() -> None:
            fired.append(True)
            if task is not None:
                task.cancel()

        timer = asyncio.get_running_loop().call_later(cfg.connect_timeout, give_up)
        try:
            await stack.enter_async_context(client)
            return await self._list_all(client)
        except asyncio.CancelledError:
            if fired and task is not None:
                task.uncancel()
                raise TimeoutError(f"no answer in {cfg.connect_timeout} s") from None
            raise
        finally:
            timer.cancel()

    async def _session(self, srv: _Server) -> str:
        """One connection, from handshake to loss. Returns ``"reconnect"`` when the user asked for a new one."""
        import anyio

        cfg = srv.cfg
        errlog = self._errlog(srv)
        try:
            async with AsyncExitStack() as stack:
                client, http = self._make_client(srv, errlog)
                if http is not None:
                    await stack.enter_async_context(http)  # entered first, so it is closed last
                items = await self._connect(stack, client, cfg)
                srv.client = client
                srv.want_refresh = srv.want_reconnect = srv.lost = False
                self._publish_tools(srv, items)
                self._set(
                    srv, status="connected", error="", error_kind="", ever_connected=True, connected_at=time.time(),
                    checked_at=time.time(), next_retry_at=0.0, bridge="unknown", fail_streak=0,
                )
                _log(f"{cfg.id}: connected, {len(srv.tools)} tools ({len(srv.shown)} exposed)")
                await self._probe(srv)
                with self._cond:
                    srv.done_seq = max(srv.done_seq, srv.req_seq)
                    self._cond.notify_all()
                while self._alive(srv):
                    with anyio.move_on_after(cfg.ping_interval):
                        await srv.wake.wait()  # type: ignore[union-attr]
                    srv.wake.clear()  # type: ignore[union-attr]
                    if not self._alive(srv):
                        return "stopped"
                    if srv.want_reconnect:
                        return "reconnect"
                    if srv.lost:
                        raise McpCallError("The connection dropped during a call.")
                    if srv.want_refresh:
                        srv.want_refresh = False
                        self._publish_tools(srv, await self._list_all(client, fresh=True))
                        _log(f"{cfg.id}: tools re-read, {len(srv.tools)} tools")
                    else:
                        await self._keepalive(srv)
                    await self._probe(srv)
                    self._set(srv, checked_at=time.time())
                    with self._cond:
                        srv.done_seq = max(srv.done_seq, srv.req_seq)
                        self._cond.notify_all()
                return "stopped"
            # Reached only when the SDK's own task group cancelled us and swallowed it: the link is gone.
            return "lost" if self._alive(srv) else "stopped"
        finally:
            if errlog is not sys.stderr:
                try:
                    errlog.close()
                except OSError:
                    pass

    # -- synchronous control (called from request / turn threads) ----------------------------------
    def _kick(self, srv: _Server, *, refresh: bool, reconnect: bool) -> None:
        loop = self._loop

        def go() -> None:
            srv.want_refresh = srv.want_refresh or refresh
            srv.want_reconnect = srv.want_reconnect or reconnect
            if srv.task is None or srv.task.done():
                if srv.cfg.enabled:
                    srv.wake = asyncio.Event()
                    srv.task = asyncio.get_running_loop().create_task(self._supervise(srv))
            elif srv.wake is not None:
                srv.wake.set()

        if loop is not None:
            loop.call_soon_threadsafe(go)

    def refresh(self, server_id: str | None = None, *, hard: bool = False, reload: bool = True, wait: float = 15.0) -> list[dict[str, Any]]:
        """Look again without restarting anything.

        A connected server re-reads its tools (``tools/list``); one that is down reconnects now instead of
        waiting for its back-off; ``hard`` reconnects every targeted server. ``reload`` re-reads the config file
        first, so a server added or edited in ``mcp_servers.json`` shows up. Waits up to ``wait`` seconds for the
        result and returns the new statuses.
        """
        if reload and not self._fixed_configs:
            self._load()
        self.start()
        with self._lock:
            targets = [s for s in self._servers.values() if server_id in (None, s.cfg.id)]
        if server_id is not None and not targets:
            raise KeyError(server_id)
        pending: list[tuple[_Server, int]] = []
        for srv in targets:
            if not srv.cfg.enabled:
                continue
            with self._cond:
                srv.req_seq += 1
                pending.append((srv, srv.req_seq))
            connected = srv.status == "connected"
            self._kick(srv, refresh=connected and not hard, reconnect=hard or not connected)
        deadline = time.time() + max(0.0, wait)
        with self._cond:
            while time.time() < deadline and any(s.done_seq < seq and s.cfg.enabled for s, seq in pending):
                self._cond.wait(min(0.2, max(0.0, deadline - time.time())))
        return self.status()

    def reconnect(self, server_id: str, *, wait: float = 15.0) -> dict[str, Any]:
        """Tear one server's connection down and open a new one."""
        self.refresh(server_id, hard=True, reload=True, wait=wait)
        found = self.server(server_id)
        if found is None:
            raise KeyError(server_id)
        return found

    def call(self, server_id: str, tool: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None) -> str:
        """Run one tool on a server and return its result as text. Raises ``McpUnavailable`` / ``McpCallError``."""
        with self._lock:
            srv = self._servers.get(server_id)
            client, loop = (srv.client, self._loop) if srv else (None, None)
        if srv is None:
            raise McpUnavailable(f"There is no MCP server called '{server_id}'.")
        if srv.status != "connected" or client is None or loop is None:
            raise McpUnavailable(f"{srv.cfg.label} is not connected ({srv.error or srv.status}).")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            raise McpCallError("MCP calls must not be made from the MCP loop itself.")
        wait = float(timeout or srv.cfg.call_timeout)
        future = asyncio.run_coroutine_threadsafe(self._call(srv, client, tool, dict(arguments or {}), wait), loop)
        try:
            return future.result(wait + 15)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise McpCallError(f"{srv.cfg.label} did not answer '{tool}' in {int(wait)} s.") from None

    async def _call(self, srv: _Server, client: Any, tool: str, arguments: dict[str, Any], timeout: float) -> str:
        try:
            result = await client.call_tool(tool, arguments, read_timeout_seconds=timeout)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            name = type(exc).__name__
            if name in {"MCPError", "McpError"}:
                code = getattr(exc, "code", "")
                if code == -32000 or "closed" in str(exc).lower():
                    srv.lost = True  # the link is gone: the supervisor reconnects
                    if srv.wake is not None:
                        srv.wake.set()
                    raise McpCallError(f"The connection to {srv.cfg.label} dropped during '{tool}'. It reconnects by itself; try again in a moment.") from None
                if code == -32001 or "timed out" in str(exc).lower():
                    raise McpCallError(f"{srv.cfg.label} did not answer '{tool}' in {int(timeout)} s.") from None
                raise McpCallError(f"{srv.cfg.label} refused the call (MCP error {code}): {_clip(getattr(exc, 'message', str(exc)), 300)}") from None
            srv.lost = True  # the supervisor reconnects
            if srv.wake is not None:
                srv.wake.set()
            raise McpCallError(f"The connection to {srv.cfg.label} dropped during '{tool}'. It reconnects by itself; try again in a moment.") from None
        return format_result(srv.cfg.id, result)

    # -- what the API shows --------------------------------------------------------------------------
    def _view(self, srv: _Server) -> dict[str, Any]:
        cfg = srv.cfg
        ready = srv.status == "connected" and (cfg.probe is None or srv.bridge == "ok")
        app = cfg.app_label or cfg.label
        note = ""
        if srv.status == "connected" and cfg.probe is not None and srv.bridge == "down":
            note = f"The MCP server is running but {app} does not answer."
        retry = max(0, int(srv.next_retry_at - time.time())) if srv.next_retry_at else None
        return {
            "id": cfg.id,
            "label": cfg.label,
            "icon": cfg.icon,
            "description": cfg.description,
            "enabled": cfg.enabled,
            "transport": cfg.transport,
            "target": cfg.target,
            "status": srv.status,
            "ready": ready,
            "bridge": srv.bridge if cfg.probe is not None else "unknown",
            "app_label": app,
            "error": srv.error,
            "error_kind": srv.error_kind,
            "note": note,
            "setup_hint": cfg.setup_hint,
            "bound_to": list(cfg.bind_to),
            "tools_total": len(srv.tools),
            "tools_exposed": len(srv.shown),
            "tools_on_demand": len(srv.on_demand),
            "tool_names": [t.name for t in srv.shown],
            "connected_at": _iso(srv.connected_at),
            "checked_at": _iso(srv.checked_at),
            "next_retry_in": retry if srv.status in {"disconnected", "reconnecting"} else None,
        }

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._view(s) for s in self._servers.values()]

    def running(self) -> bool:
        """The background loop is up (``start`` was called and has not been stopped)."""
        return self._thread is not None and self._thread.is_alive() and not self._stopping

    def server(self, server_id: str) -> dict[str, Any] | None:
        with self._lock:
            srv = self._servers.get(server_id)
            return self._view(srv) if srv else None

    def tool_rows(self, server_id: str) -> list[dict[str, Any]] | None:
        """Every tool of a server for the API: name, purpose, and whether it is in the cats' own tool list."""
        with self._lock:
            srv = self._servers.get(server_id)
            if srv is None:
                return None
            shown = {t.function for t in srv.shown}
            return [
                {"name": t.name, "function": t.function, "description": _clip(t.description, 200), "exposed": t.function in shown, "read_only": t.read_only}
                for t in srv.tools
            ]

    def problems(self) -> list[str]:
        with self._lock:
            return list(self._problems)

    # -- binding tools to cats -----------------------------------------------------------------------
    def _bound(self, bot_id: str) -> list[_Server]:
        bid = str(bot_id or "").strip().lower()
        if not bid:
            return []
        with self._lock:
            return [s for s in self._servers.values() if s.cfg.enabled and bid in s.cfg.bind_to]

    def servers_for_agent(self, bot_id: str) -> list[str]:
        return [s.cfg.id for s in self._bound(bot_id)]

    def tools_for_agent(self, bot_id: str) -> list[McpTool]:
        """The tools this cat gets directly: connected servers it is bound to, capped per server."""
        out: list[McpTool] = []
        for srv in self._bound(bot_id):
            if srv.status == "connected":
                out.extend(srv.shown)
        return out

    def is_read_only(self, function: str) -> bool:
        return bool(self._read_only.get(function))

    def register_for_agent(self, bot_id: str) -> list[str]:
        """Bind this cat's MCP tools into the running turn (``harness.deferred``). Other cats get nothing."""
        bound = [s for s in self._bound(bot_id) if s.status == "connected"]
        if not bound:
            return []
        from backend.harness.deferred import register_tools

        schemas: list[dict[str, Any]] = []
        executors: dict[str, Callable[[dict[str, Any]], str]] = {}
        names: list[str] = []
        for srv in bound:
            for tool in srv.shown:
                schemas.append(tool.schema)
                executors[tool.function] = self._executor(srv.cfg.id, tool.name)
                names.append(tool.function)
            feature = srv.cfg.features.get("compile_check")
            if isinstance(feature, dict):
                from backend.services import mcp_features

                schema, fn = mcp_features.compile_check_tool(self, srv.cfg, feature)
                schemas.append(schema)
                executors[schema["function"]["name"]] = fn
                names.append(schema["function"]["name"])
        if any(s.on_demand for s in bound):
            schemas.extend(self._meta_schemas())
            executors[META_FIND] = lambda args, b=bot_id: self.find_tools(b, args)
            executors[META_CALL] = lambda args, b=bot_id: self.call_bound(b, args)
            names.extend([META_FIND, META_CALL])
        register_tools(schemas, executors)
        return names

    def _executor(self, server_id: str, tool: str) -> Callable[[dict[str, Any]], str]:
        def run(args: dict[str, Any]) -> str:
            try:
                return self.call(server_id, tool, args)
            except McpError as exc:
                return f"Error: {exc}"

        return run

    @staticmethod
    def _meta_schemas() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": META_FIND,
                    "description": "Search the MCP tools that are not in your tool list (there are more than fit). Returns names, arguments and a one-line purpose.",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Words from the tool's name or purpose, e.g. 'bevel' or 'lightmap'."}, "server": {"type": "string", "description": "Optional server id to search in."}}, "required": ["query"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": META_CALL,
                    "description": "Run an MCP tool found with mcp_find_tools.",
                    "parameters": {"type": "object", "properties": {"server": {"type": "string"}, "tool": {"type": "string", "description": "The tool's own name as mcp_find_tools printed it."}, "arguments": {"type": "object", "description": "The tool's arguments."}}, "required": ["server", "tool"]},
                },
            },
        ]

    def find_tools(self, bot_id: str, args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip().lower()
        only = str(args.get("server") or "").strip().lower()
        words = [w for w in re.split(r"\W+", query) if w]
        lines: list[str] = []
        for srv in self._bound(bot_id):
            if srv.status != "connected" or (only and srv.cfg.id != only):
                continue
            for tool in srv.on_demand:
                hay = f"{tool.name} {tool.description}".lower()
                if words and not all(w in hay for w in words):
                    continue
                params, required = tool.param_names()
                sig = ", ".join(p + ("*" if p in required else "") for p in params)
                lines.append(f"- {srv.cfg.id}/{tool.name}({sig}) - {_clip(tool.description, 140)}")
        if not lines:
            return "No matching tool. Try other words, or work with the tools already in your list."
        head = f"{len(lines)} match(es); call one with {META_CALL}(server, tool, arguments). * = required argument.\n"
        return head + "\n".join(lines[:15])

    def call_bound(self, bot_id: str, args: dict[str, Any]) -> str:
        server = str(args.get("server") or "").strip().lower()
        tool = str(args.get("tool") or "").strip()
        if server not in {s.cfg.id for s in self._bound(bot_id)}:
            return f"Error: you have no MCP server called '{server}'."
        arguments = args.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return "Error: 'arguments' must be an object."
        try:
            return self.call(server, tool, arguments if isinstance(arguments, dict) else {})
        except McpError as exc:
            return f"Error: {exc}"

    def meta_target(self, args: dict[str, Any]) -> str:
        """The function name behind an ``mcp_call_tool`` request (for the approval gate)."""
        server = str(args.get("server") or "").strip().lower()
        tool = str(args.get("tool") or "").strip()
        with self._lock:
            srv = self._servers.get(server)
            if srv:
                for t in srv.tools:
                    if t.name == tool:
                        return t.function
        return function_name(server, tool)

    def prompt_note(self, bot_id: str) -> str:
        """A few sentences for the cat's system prompt: what it has, or why it does not."""
        lines: list[str] = []
        for srv in self._bound(bot_id):
            cfg = srv.cfg
            if srv.status == "connected":
                more = f" {len(srv.on_demand)} more are reachable with {META_FIND}." if srv.on_demand else ""
                skill = f" Read the '{cfg.skill}' playbook (read_skill) before you start." if cfg.skill else ""
                if cfg.probe is not None and srv.bridge == "down":
                    app = cfg.app_label or cfg.label
                    lines.append(
                        f"{cfg.label} is connected but {app} is not answering right now, so its tools will fail. "
                        f"If the task needs {app}, do not guess: call report_task_results with status blocked and tell the user to open {app} and start its MCP server."
                    )
                else:
                    lines.append(f"{cfg.label} is connected. Its tools are named {TOOL_PREFIX}{cfg.id}__*.{more}{skill}")
            elif cfg.enabled:
                app = cfg.app_label or cfg.label
                lines.append(
                    f"{cfg.label} is offline, so its tools are not available this turn. If the task needs {app}, "
                    f"finish what you can without it and call report_task_results with status blocked, telling the user to start {app} and press Reconnect in Models."
                )
        return "\n".join(lines)


def _iso(ts: float) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


# --------------------------------------------------------------------------------------------------
# Module level helpers
# --------------------------------------------------------------------------------------------------


def get_manager() -> MCPManager:
    return MCPManager.instance()


def set_manager(manager: MCPManager | None) -> None:
    """Replace the singleton (tests). The old one is stopped."""
    with MCPManager._instance_lock:
        old, MCPManager._instance = MCPManager._instance, manager
    if old is not None and old is not manager:
        old.stop()


def enabled_by_environment() -> bool:
    return os.environ.get("CATCREW_MCP", "1").strip().lower() not in {"0", "false", "no", "off"}


def register_for_agent(bot_id: str) -> list[str]:
    """Called by ``platform.tool_registry.register_agent_tools`` for every turn of every cat. Never raises."""
    try:
        return get_manager().register_for_agent(bot_id)
    except Exception as exc:  # noqa: BLE001 - an MCP hiccup must not break a cat's turn
        _log(f"could not bind tools for {bot_id}: {type(exc).__name__}")
        return []


def prompt_note(bot_id: str) -> str:
    try:
        return get_manager().prompt_note(bot_id)
    except Exception:  # noqa: BLE001
        return ""
