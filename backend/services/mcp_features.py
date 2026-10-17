"""Small behaviours built *on top of* an MCP server's tools, switched on in ``mcp_servers.json``.

``compile_check`` is the one that matters today: the Unity Editor compiles C# on its own, and its console is
where the errors land. Asking a model to call "refresh", wait, call "read console", parse the text and open
each file is five chances to get one step wrong, so one tool does it: recompile, read the console, return
every error as ``file:line CSxxxx message`` with the source lines around it. The cat only has to edit and
call the tool again, which is the fix loop the Unity playbook describes.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

# Assets/Scripts/Player.cs(12,5): error CS1002: ; expected
_ERROR = re.compile(
    r"(?P<file>[A-Za-z0-9_@./\\: -]+?\.cs)\((?P<line>\d+),(?P<col>\d+)\):\s*(?:error|Error)\s+(?P<code>CS\d{4}):\s*(?P<msg>[^\r\n\"]+)"
)
_MAX_ROUNDS = 8  # calls per turn before the tool tells the cat to stop and report
_REPORT_LIMIT = 5000


def parse_compile_errors(text: str) -> list[dict[str, Any]]:
    """Every C# compiler error in a console dump, once each, in the order they appear."""
    seen: set[tuple[str, int, str, str]] = set()
    out: list[dict[str, Any]] = []
    for match in _ERROR.finditer(text or ""):
        file = match.group("file").replace("\\\\", "/").replace("\\", "/").strip()
        if "Assets/" in file:  # Unity prints project-relative paths; keep them that way even if a prefix slipped in
            file = "Assets/" + file.split("Assets/", 1)[1]
        entry = {
            "file": file,
            "line": int(match.group("line")),
            "col": int(match.group("col")),
            "code": match.group("code"),
            "message": match.group("msg").strip(),
        }
        key = (entry["file"], entry["line"], entry["code"], entry["message"])
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


def _excerpt(file: str, line: int, context: int) -> str:
    """The source lines around an error, read from the project folder. Empty when the file is not there."""
    try:
        from backend.services.workspace import current_workspace

        path = Path(current_workspace()) / file
        if not path.is_file():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return ""
    lo, hi = max(1, line - context), min(len(lines), line + context)
    rows = []
    for n in range(lo, hi + 1):
        mark = ">" if n == line else " "
        rows.append(f"   {mark}{n:>4}| {lines[n - 1][:160]}")
    return "\n".join(rows)


def build_report(errors: list[dict[str, Any]], *, source_root: str = "", max_errors: int = 12, context: int = 2) -> str:
    if not errors:
        return "Compiles clean: the Unity console shows no compiler errors."
    root = source_root.strip("/").lower()
    mine = [e for e in errors if not root or e["file"].lower().startswith(root + "/")]
    others = [e for e in errors if e not in mine]
    shown = (mine or errors)[:max_errors]
    lines = [f"{len(errors)} compiler error(s) in Unity" + (f" ({len(mine)} under {source_root})" if root and mine else "") + ":"]
    for i, err in enumerate(shown, 1):
        lines.append(f"{i}. {err['file']}:{err['line']}:{err['col']} {err['code']} {err['message']}")
        snippet = _excerpt(err["file"], err["line"], context)
        if snippet:
            lines.append(snippet)
    hidden = len(mine or errors) - len(shown)
    if hidden > 0:
        lines.append(f"... and {hidden} more; fix these first, the rest often disappear with them.")
    if root and mine and others:
        lines.append(f"{len(others)} more error(s) are outside {source_root} (packages or generated code); leave them unless they block you.")
    lines.append("Fix them with replace_in_file, then call this tool again. Fix the first error first: later ones are often its echo.")
    return "\n".join(lines)


def compile_check_tool(manager: Any, cfg: Any, feature: dict[str, Any]) -> tuple[dict[str, Any], Callable[[dict[str, Any]], str]]:
    """``(schema, executor)`` for the server's compile-check tool. One executor per turn (it counts its calls)."""
    name = str(feature.get("tool_name") or f"{cfg.id}_compile_check")
    refresh_tool = str(feature.get("refresh_tool") or "")
    console_tool = str(feature.get("console_tool") or "read_console")
    refresh_args = dict(feature.get("refresh_args") or {})
    console_args = dict(feature.get("console_args") or {})
    source_root = str(feature.get("source_root") or "")
    settle = float(feature.get("settle_seconds") or 0)
    max_errors = int(feature.get("max_errors") or 12)
    context = int(feature.get("context_lines") or 2)
    rounds = {"n": 0}

    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": (
                f"Recompile the {cfg.label} project and read its console: returns every C# compiler error as file:line CSxxxx message "
                f"with the source lines around it{' (' + source_root + ')' if source_root else ''}. Use it after editing scripts and repeat until it reports a clean compile."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "refresh": {"type": "boolean", "description": "Trigger a refresh and compile first (default true). False only re-reads the console."},
                },
            },
        },
    }

    def run(args: dict[str, Any]) -> str:
        from backend.services.mcp_service import McpError

        rounds["n"] += 1
        if rounds["n"] > _MAX_ROUNDS:
            return (
                f"Error: {name} has been called {_MAX_ROUNDS} times this turn and the project still does not compile. Stop: "
                "call report_task_results with status blocked and list the errors that remain."
            )
        refresh = args.get("refresh", True)
        refresh = True if refresh is None else str(refresh).lower() not in {"false", "0", "no"}
        try:
            note = ""
            if refresh and refresh_tool:
                answer = manager.call(cfg.id, refresh_tool, refresh_args)
                if answer.startswith("Error:"):
                    note = f"(the refresh reported: {answer[:200]}) "
                if settle > 0:
                    time.sleep(settle)
            console = manager.call(cfg.id, console_tool, console_args)
        except McpError as exc:
            return f"Error: {exc}"
        if console.startswith("Error:"):
            return console
        return note + build_report(parse_compile_errors(console), source_root=source_root, max_errors=max_errors, context=context)

    return schema, run
