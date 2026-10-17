"""Letta-style executable function registry (Phase 15's "Tools" half of the
Dual Skill/Tool Architecture — see the CatCrewAI project doc
``architecture/phase15-dual-skill-tool-architecture.md``).

Skills (``backend/skills/*/SKILL.md``, ``agent_context.py``) are Markdown
playbooks — reasoning/instructions a bot reads on demand via ``read_skill``.
Tools here are the opposite: real Python functions with a JSON schema a bot
calls through native function calling, mirroring ``letta-ai/skills``' tools
layout.

Storage: the ``tools`` / ``agent_tools`` SQLite tables (``platform/db.py``)
— same shape as ``skills`` / ``agent_skills``, same per-agent-or-global
assignment matrix, edited from the same kind of Settings UI
(``tools_page.py``).

Dispatch: a tool's ``handler_path`` (``"pkg.module:function_name"``) is
resolved once via ``importlib`` and wrapped as a
``harness.deferred.ToolExecutor`` (``dict[str, Any] -> str``). Binding a
bot's assigned tools for one turn reuses the exact mechanism connector
tools already use — ``harness.deferred.register_tools()`` — so nothing
about the tool-calling loop (``run_agent``, ``execute_tool``,
``tools_schema_for``) has to change to support this.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
import shutil
import sys
import tempfile
import typing
from pathlib import Path
from typing import Any, Callable

from backend.db import ledger as db
from backend.platform.essentials import essential_slugs_for_agent

_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _json_type(annotation: Any) -> tuple[str, str | None]:
    """Best-effort Python type hint -> JSON schema type. Returns
    ``(json_type, item_type_or_None)`` — ``item_type`` is only meaningful
    when ``json_type`` is ``"array"``. Unknown/complex annotations fall
    back to ``"string"`` rather than raising — a tool with an exotic
    parameter type should still register, just with a looser schema."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union:  # Optional[X] == Union[X, None]
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _json_type(args[0])
        return "string", None
    if origin in (list, set, tuple):
        args = typing.get_args(annotation)
        item = args[0] if args else str
        item_type, _ = _json_type(item)
        return "array", item_type
    if annotation in _TYPE_MAP:
        return _TYPE_MAP[annotation], None
    return "string", None


_ARGS_LINE = re.compile(r"^\s*(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$")


def _parse_google_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into ``(description, {param: help})``.
    Tolerant of missing/odd formatting — a function with a plain one-line
    docstring and no ``Args:`` section still gets a usable description
    instead of an empty one."""
    if not doc:
        return "", {}
    lines = [line.rstrip() for line in doc.strip().splitlines()]
    desc_lines: list[str] = []
    params: dict[str, str] = {}
    # Two independent flags, not one "current section" value: once the
    # docstring leaves the leading description (via either an Args: block
    # or a Returns:/Raises:/etc. block), it never returns to it — a plain
    # ``section = None`` after "Returns:" previously fell through to the
    # "still in the description" branch and re-appended trailing sections.
    in_description = True
    in_args = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:", "parameters:"):
            in_description = False
            in_args = True
            continue
        if stripped.lower() in (
            "returns:", "raises:", "yields:", "example:", "examples:", "note:", "notes:",
        ):
            in_description = False
            in_args = False
            continue
        if in_args:
            match = _ARGS_LINE.match(line)
            if match:
                params[match.group(1)] = match.group(2).strip()
            continue
        if in_description and stripped:
            desc_lines.append(stripped)
    return " ".join(desc_lines).strip(), params


def function_to_schema(fn: Callable[..., Any], *, name: str | None = None) -> dict[str, Any]:
    """``inspect.signature`` + type hints + a Google-style docstring -> the
    same OpenAI/Gemini function-schema shape ``tools.py``'s hand-written
    ``_fn()`` entries use, so a generated schema and a hand-written one are
    interchangeable everywhere ``TOOLS_SCHEMA`` is consumed."""
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 — a bad forward ref shouldn't break ingestion
        hints = {}
    description, param_help = _parse_google_docstring(inspect.getdoc(fn) or "")
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(pname, str)
        json_type, item_type = _json_type(annotation)
        spec: dict[str, Any] = {"type": json_type}
        if json_type == "array" and item_type:
            spec["items"] = {"type": item_type}
        if pname in param_help:
            spec["description"] = param_help[pname]
        properties[pname] = spec
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    return {
        "type": "function",
        "function": {
            "name": name or fn.__name__,
            "description": description or (fn.__name__.replace("_", " ") + "."),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def resolve_handler(handler_path: str) -> Callable[..., Any]:
    """``"pkg.module:function_name"`` -> the actual function object.
    Ensures the marketplace tools overlay (GitHub-imported tools, see
    ``import_tool_from_url``) is on ``sys.path`` first — cheap and
    idempotent, and required so a previously-imported marketplace tool
    still resolves after an app restart clears the in-memory import cache."""
    from backend.db.legacy import normalize_module_path

    # Rows written before the backend/ restructure still say ``cat_crew_ai.*``.
    handler_path = normalize_module_path(handler_path)
    module_name, _, fn_name = str(handler_path or "").partition(":")
    if not module_name or not fn_name:
        raise ValueError(f"Malformed handler_path {handler_path!r} (want 'module:function').")
    _ensure_marketplace_tools_importable()
    module = importlib.import_module(module_name)
    fn = getattr(module, fn_name, None)
    if fn is None or not callable(fn):
        raise ValueError(f"{handler_path!r} does not resolve to a callable.")
    return fn


def _make_executor(fn: Callable[..., Any]) -> Callable[[dict[str, Any]], str]:
    """Wrap a plain Python function as a ``harness.deferred.ToolExecutor``
    (``dict[str, Any] -> str``) — every other tool dispatch in this codebase
    returns an error string instead of raising into the tool-calling loop;
    match that convention here too."""

    def _execute(args: dict[str, Any]) -> str:
        try:
            result = fn(**dict(args or {}))
        except TypeError as exc:
            return f"Error calling {fn.__name__}: bad arguments ({exc})."
        except Exception as exc:  # noqa: BLE001 — a tool call must never raise into the loop
            return f"Error running {fn.__name__}: {exc}"
        if result is None:
            return "(done — no output)"
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except TypeError:
            return str(result)

    return _execute


def register_tool_function(
    slug: str,
    fn: Callable[..., Any],
    *,
    is_global: bool = False,
    handler_path: str | None = None,
    type: str | None = None,  # noqa: A002 — matches the db column name
    source_url: str | None = None,
) -> str:
    """Compute ``fn``'s schema and upsert it into the ``tools`` catalog
    (does not assign it to any agent — that's ``db.assign_tool``/the Tools
    Matrix UI, same split as skills). ``handler_path`` defaults to
    ``fn.__module__:fn.__qualname__``; pass it explicitly only when the
    function cannot be re-imported by that path (a dynamically-defined
    closure should not be registered this way in the first place — it has
    nothing stable to import at dispatch time)."""
    schema = function_to_schema(fn, name=slug)
    path = handler_path or f"{fn.__module__}:{fn.__qualname__}"
    return db.upsert_tool(
        slug,
        schema["function"]["name"],
        inspect.getdoc(fn) or "",
        path,
        json.dumps(schema, ensure_ascii=False),
        is_global=is_global,
        type=type,
        source_url=source_url,
    )


def register_agent_tools(bot_id: str) -> list[str]:
    """Everything bound to ``bot_id`` for this turn: its registry tools (``_register_db_tools``) plus the
    tools of the MCP servers configured for that cat (``services/mcp_service``, e.g. Blender for Pixel and
    Unity for Pulse). Both go into the same per-turn deferred registry, so the engine treats them alike, and
    a cat that is not named in a server's ``bind_to`` never sees that server's tools."""
    names = _register_db_tools(bot_id)
    from ..services import mcp_service

    return [*names, *mcp_service.register_for_agent(str(bot_id or "").strip())]


def _register_db_tools(bot_id: str) -> list[str]:
    """Bind ``bot_id``'s DB-assigned (+ global) tools into the deferred
    per-turn registry (``harness/deferred.py``) — the same mechanism a
    loaded connector's tools use. Call once per turn, alongside the
    existing connector-loading step; safe to call with an empty/unknown
    ``bot_id`` (a no-op, not an error) and safe to call repeatedly (schemas
    already registered this turn are skipped by
    ``deferred.register_tools``'s own de-dup). Returns the list of tool
    names actually registered."""
    bid = str(bot_id or "").strip()
    if not bid:
        return []
    try:
        rows = db.tools_for_agent(bid)
        # Essential tools are bound even if their assignment row is missing (removed by hand,
        # a wiped table, an install from before the tool existed): see platform/essentials.py.
        have = {str(row.get("slug") or "") for row in rows}
        for slug in essential_slugs_for_agent(bid):
            if slug not in have:
                extra = db.get_tool_by_slug(slug)
                if extra:
                    rows = [*rows, extra]
    except Exception:  # noqa: BLE001 — a DB hiccup should not break a turn
        return []
    if not rows:
        return []
    schemas: list[dict[str, Any]] = []
    executors: dict[str, Callable[[dict[str, Any]], str]] = {}
    names: list[str] = []
    for row in rows:
        slug = str(row.get("slug") or "")
        if not slug:
            continue
        schema: dict[str, Any] = {}
        raw_schema = row.get("schema_json") or "{}"
        try:
            parsed = json.loads(raw_schema)
            if isinstance(parsed, dict) and "function" in parsed:
                schema = parsed
        except (TypeError, ValueError):
            pass
        try:
            fn = resolve_handler(str(row.get("handler_path") or ""))
        except Exception:  # noqa: BLE001 — a missing/renamed handler shouldn't break the turn
            continue
        if not schema:
            # Malformed/empty cached schema — regenerate from the handler
            # rather than silently dropping this bot's tool for the turn.
            schema = function_to_schema(fn, name=slug)
        schemas.append(schema)
        executors[slug] = _make_executor(fn)
        names.append(slug)
    if schemas:
        from ..harness.deferred import register_tools

        register_tools(schemas, executors)
    return names


# --- GitHub ingestion (Phase 15 §2c/§5b analog / Task #25) ------------------
#
# Analogous to agent_context.import_skill_from_url, reusing its network/git
# primitives (fetch_raw_github_file, shallow_git_clone) rather than
# duplicating them — a skill is one Markdown file to parse and store; a tool
# is one or more Python functions to import and register, so the scan/parse/
# persist half is necessarily different code, kept here rather than forced
# into a shared abstraction (matches this codebase's "small, boring,
# near-identical function sets" style — see platform/db.py).

_MARKETPLACE_READY = False

_TOOL_BLOB_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")
_TOOL_TREE_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$")
_TOOL_REPO_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


def _marketplace_tools_dir() -> Path:
    from ..config import CATCREW_HOME

    return CATCREW_HOME / "catcrew_marketplace_tools"


def _ensure_marketplace_tools_importable() -> None:
    """Make ``CATCREW_HOME/catcrew_marketplace_tools/`` an importable top-level
    package (``catcrew_marketplace_tools.<repo>.<file>``) by adding
    ``CATCREW_HOME`` to ``sys.path`` once per process. Idempotent and cheap —
    safe to call on every ``resolve_handler`` (including for ordinary
    ``backend.*`` handler paths that never touch this overlay at all)."""
    global _MARKETPLACE_READY
    if _MARKETPLACE_READY:
        return
    from ..config import CATCREW_HOME

    home = str(CATCREW_HOME)
    if home not in sys.path:
        sys.path.insert(0, home)
    pkg_dir = _marketplace_tools_dir()
    pkg_dir.mkdir(parents=True, exist_ok=True)
    init_file = pkg_dir / "__init__.py"
    if not init_file.exists():
        init_file.write_text("", encoding="utf-8")
    _MARKETPLACE_READY = True


def _safe_module_segment(text: str) -> str:
    """A GitHub owner/repo/file name is not always a valid Python
    identifier segment (hyphens, leading digits, dots) — normalize it into
    one, deterministically, so the same repo always lands at the same
    importable path on re-import."""
    seg = re.sub(r"[^a-zA-Z0-9]+", "_", (text or "").strip()).strip("_").lower()
    if not seg:
        seg = "x"
    if seg[0].isdigit():
        seg = f"m_{seg}"
    return seg


def import_tool_from_url(url: str) -> str:
    """Import Letta-style tool functions from a GitHub URL: a raw/blob
    ``.py`` file, or a ``github.com/owner/repo`` (optionally
    ``/tree/<branch>/<subfolder>``) URL — cloned shallowly and scanned for
    every ``.py`` file under the given subfolder (or the whole repo),
    skipping ``.git``/``__pycache__``/``node_modules``/test directories and
    underscore-prefixed files.

    Every top-level, non-underscore function actually defined in each file
    (not merely imported into it) is copied into the marketplace tools
    overlay (``CATCREW_HOME/catcrew_marketplace_tools/<owner>_<repo>/``, made
    importable by ``_ensure_marketplace_tools_importable``) and registered
    into the ``tools`` catalog tagged ``type='marketplace'``,
    ``source_url=url`` — schema auto-generated exactly like a first-party
    tool via ``function_to_schema``. A multi-file repo does not fail
    atomically: each function's outcome is reported on its own line.
    """
    from ..agent_context import fetch_raw_github_file, shallow_git_clone

    target = (url or "").strip()
    if not target:
        return "Error: empty url."
    if not target.startswith(("http://", "https://")):
        return "Error: expected an http(s) GitHub URL."

    _ensure_marketplace_tools_importable()

    py_files: list[tuple[Path, str, str]] = []  # (path, owner, repo)
    tmp_dir = tempfile.TemporaryDirectory(prefix="catcrew_tool_import_")
    try:
        blob_match = _TOOL_BLOB_RE.match(target)
        if target.lower().endswith(".py") or blob_match:
            owner, repo = "external", "file"
            if blob_match:
                owner, repo, _ref, _path = blob_match.groups()
            try:
                text = fetch_raw_github_file(target)
            except Exception as exc:  # noqa: BLE001
                return f"Error: could not fetch {target}: {exc}"
            scratch = Path(tmp_dir.name) / "single_file.py"
            scratch.write_text(text, encoding="utf-8")
            py_files.append((scratch, owner, repo))
        else:
            match = _TOOL_TREE_RE.match(target)
            if match:
                owner, repo, ref, subfolder = match.groups()
            else:
                match = _TOOL_REPO_RE.match(target)
                if not match:
                    return (
                        "Error: unrecognized GitHub URL. Use a raw/blob .py file, or "
                        "a github.com/owner/repo (optionally /tree/<branch>/<subfolder>) URL."
                    )
                owner, repo = match.groups()
                ref, subfolder = "", ""
            clone_dest = Path(tmp_dir.name) / "repo"
            err = shallow_git_clone(f"https://github.com/{owner}/{repo}.git", clone_dest, ref=ref)
            if err:
                return f"Error: {err}"
            scan_root = clone_dest / subfolder if subfolder else clone_dest
            if not scan_root.is_dir():
                return f"Error: {subfolder!r} not found in {owner}/{repo}."
            skip_dirs = {".git", "__pycache__", "node_modules", "tests", "test"}
            for path in sorted(scan_root.rglob("*.py")):
                rel_parts = path.relative_to(scan_root).parts[:-1]
                if any(part in skip_dirs or part.startswith(".") for part in rel_parts):
                    continue
                if path.name.startswith("_"):
                    continue
                py_files.append((path, owner, repo))
            if not py_files:
                return f"Error: no .py files found under {target}."

        results: list[str] = []
        pkg_dir = _marketplace_tools_dir()
        for path, owner, repo in py_files:
            repo_seg = _safe_module_segment(f"{owner}_{repo}")
            file_seg = _safe_module_segment(path.stem)
            repo_dir = pkg_dir / repo_seg
            repo_dir.mkdir(parents=True, exist_ok=True)
            repo_init = repo_dir / "__init__.py"
            if not repo_init.exists():
                repo_init.write_text("", encoding="utf-8")
            dest_file = repo_dir / f"{file_seg}.py"
            try:
                shutil.copyfile(path, dest_file)
            except OSError as exc:
                results.append(f"  {path.name}: could not save: {exc}")
                continue

            module_name = f"catcrew_marketplace_tools.{repo_seg}.{file_seg}"
            sys.modules.pop(module_name, None)  # pick up a re-imported/updated file
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:  # noqa: BLE001
                results.append(f"  {path.name}: import failed: {exc}")
                continue

            found_any = False
            for attr_name in dir(module):
                if attr_name.startswith("_"):
                    continue
                candidate = getattr(module, attr_name)
                if not inspect.isfunction(candidate) or candidate.__module__ != module_name:
                    continue
                handler_path = f"{module_name}:{attr_name}"
                try:
                    register_tool_function(
                        attr_name,
                        candidate,
                        handler_path=handler_path,
                        type="marketplace",
                        source_url=target,
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append(f"  {attr_name}: could not register: {exc}")
                    continue
                found_any = True
                results.append(f"  {attr_name}: imported from {path.name} -> {handler_path}")
            if not found_any:
                results.append(f"  {path.name}: no top-level functions found.")

        if not results:
            return f"Error: nothing importable found at {target}."
        return f"Imported from {target}:\n" + "\n".join(results)
    finally:
        tmp_dir.cleanup()
