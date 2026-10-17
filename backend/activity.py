"""Map tool names to chat activity-card copy."""

from __future__ import annotations

import re
from typing import Any

from .providers.base import strip_printed_tool_json

_PHASES: dict[str, str] = {
    "web_search": "Searching the web",
    "image_search": "Searching images",
    "web_fetch": "Reading a page",
    "generate_image": "Generating image",
    "comfy_info": "Checking ComfyUI",
    "comfy_list": "Listing Comfy assets",
    "comfy_run_workflow": "Running a Comfy workflow",
    "comfy_upload": "Uploading to ComfyUI",
    "upscale_image": "Upscaling an image",
    "remove_background": "Removing the background",
    "comfy_interrupt": "Cancelling Comfy job",
    "comfy_stop": "Stopping ComfyUI",
    "screenshot": "Taking a screenshot",
    "read_file": "Reading a file",
    "write_file": "Writing a file",
    "replace_in_file": "Editing a file",
    "list_dir": "Listing files",
    "get_file_info": "Reading file info",
    "create_directory": "Creating a folder",
    "list_allowed_directories": "Listing folders",
    "glob_files": "Finding files",
    "search_files": "Searching files",
    "read_terminal": "Reading the terminal",
    "open_program": "Opening a program",
    "open_url": "Opening a link",
    "open_in_vscode": "Opening in VS Code",
    "git_status": "Checking git",
    "git_diff": "Reading a diff",
    "git_log": "Reading git history",
    "git_commit": "Creating a commit",
    "git_add": "Staging files",
    "git_unstage": "Unstaging files",
    "git_reset": "Unstaging files",
    "git_branch": "Git branches",
    "git_checkout": "Switching branch",
    "git_show": "Showing a commit",
    "git_pull": "Pulling",
    "git_push": "Pushing",
    "db_query": "Querying the database",
    "db_tables": "Listing tables",
    "db_describe_table": "Describing a table",
    "db_list_connections": "Listing databases",
    "clipboard": "Using the clipboard",
    "notify": "Sending a notification",
    "system_time": "Checking the time",
    "hardware_info": "Checking this PC",
    "docker_info": "Checking Docker",
    "read_skill": "Reading a skill",
    "update_todo": "Updating the checklist",
    "self_modify": "Patching CatCrewAI",
    "restart_catcrew": "Restarting CatCrewAI",
    "copy_path": "Copying a file",
    "move_path": "Moving a file",
    "list_project": "Reading the project roster",
    "list_roster": "Looking up teammates",
    "list_members": "Listing members",
    "get_member": "Reading a member dossier",
    "list_bots": "Listing bots",
    "delegate_to_bot": "Delegating to a bot",
    "update_project_state": "Updating project memory",
}


def phase_for(name: str, arguments: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return (title, detail) for the in-chat activity card."""
    raw = name or ""
    if raw.startswith("connector__"):
        parts = raw.split("__", 2)
        if len(parts) == 3:
            _prefix, connector_id, tool = parts
            title = f"{connector_id.replace('_', ' ').title()} · {tool.replace('_', ' ')}"
            args = arguments or {}
            detail = ""
            for key in ("owner", "repo", "query", "path", "url"):
                value = str(args.get(key) or "").strip()
                if value:
                    detail = value.replace("\n", " ")[:88]
                    break
            return title, detail or tool.replace("_", " ")
    if raw in {"list_connectors", "load_connector_tools"}:
        title = "Connectors" if raw == "list_connectors" else "Loading connector tools"
        args = arguments or {}
        detail = str(args.get("connector_id") or args.get("id") or "").strip()
        return title, detail
    if raw == "ask_user":
        args = arguments or {}
        detail = str(args.get("prompt") or args.get("question") or "").strip()[:88]
        return "Asking you", detail
    if raw == "run_subagent":
        args = arguments or {}
        detail = str(args.get("task") or args.get("goal") or "").strip()[:88]
        return "Subagent", detail
    if raw == "run_code_mode":
        return "Code mode", "Running a Python script"
    if raw == "delegate_to_bot":
        args = arguments or {}
        who = str(args.get("bot_id") or "").strip()
        detail = str(args.get("task") or args.get("goal") or "").strip()[:88]
        return "Delegating to a bot", (who + (": " if who and detail else "") + detail)[:88]
    if raw == "list_members":
        who = str((arguments or {}).get("role") or "")
        return "Listing members", who
    if raw == "get_member":
        who = str((arguments or {}).get("bot_id") or (arguments or {}).get("name") or "")
        return "Reading a member dossier", who
    if raw == "list_roster":
        who = str((arguments or {}).get("bot_id") or "")
        return "Looking up teammates", who
    if raw == "list_project":
        return "Project roster", ""
    if raw == "list_bots":
        return "Listing bots", str((arguments or {}).get("role") or "")
    if raw == "update_project_state":
        return "Project memory", str((arguments or {}).get("key") or "")[:88]

    title = _PHASES.get(raw, "") or "Working"
    args = arguments or {}
    detail = ""
    for key in ("query", "prompt", "url", "path", "command", "file_path", "src", "title", "item", "session_id"):
        value = str(args.get(key) or "").strip()
        if value:
            detail = value.replace("\n", " ")[:88]
            break
    if not detail and raw:
        detail = raw.replace("_", " ")
    return title, detail


def next_reveal(shown: str, target: str) -> str:
    """Advance the live reply by a few characters so large model chunks type smoothly."""
    if not target:
        return ""
    if shown == target or shown.startswith(target) and len(shown) >= len(target):
        return target
    if not target.startswith(shown):
        # Provider resent a full snapshot instead of a delta.
        shown = ""
    behind = len(target) - len(shown)
    if behind <= 0:
        return target
    take = 4 if behind < 12 else min(28, max(6, behind // 4))
    return target[: len(shown) + take]


_STEP = re.compile(r"^Step\s+\d+\s*:.*$", re.I)
_NOISE = re.compile(
    r"^(i.?m sorry for the confusion.*|"
    r"let'?s get started.*|"
    r"first,?\s+let'?s.*|"
    r"now,?\s+let'?s.*|"
    r"once the project is created.*|"
    r"that'?s it!.*|"
    r"great idea!.*|"
    r"feel free to expand.*)$",
    re.I,
)
_DONE = "Done — I used the tools. The files are in the workspace."
_CODEISH = re.compile(
    r"^(import |from |const |let |var |function |export |def |class |<!DOCTYPE|"
    r"</?[a-zA-Z]|\{|\}|\s*//|\s*\*)"
)


def _looks_like_source_dump(text: str) -> bool:
    """True when the bubble is mostly dumped source, not a teammate report."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 8:
        return False
    hits = sum(1 for line in lines if _CODEISH.match(line.strip()))
    return hits >= max(8, int(len(lines) * 0.55))


def visible_chat_reply(text: str, *, tools_used: bool = False) -> str:
    """What belongs in the chat bubble: a report, never tool JSON or source dumps."""
    raw = repair_utf8_text(text or "")
    # Belt-and-suspenders: speak tags must never reach the bubble.
    raw = re.sub(r"\[speak\]\s*.*?\s*\[/speak\]", "", raw, flags=re.I | re.S)
    cleaned, removed = strip_printed_tool_json(raw)
    if tools_used:
        cleaned = re.sub(r"```[\s\S]*?```", "\n", cleaned)
    cleaned = re.sub(r"```(?:json|javascript|jsx|tsx|html|css|bash)?", "\n", cleaned, flags=re.I)
    cleaned = cleaned.replace("```", "\n")
    if removed or tools_used:
        lines = []
        for line in cleaned.splitlines():
            stripped = line.strip()
            if not stripped or _STEP.match(stripped) or _NOISE.match(stripped):
                continue
            lines.append(line)
        cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip().strip("`").strip()
    if not cleaned:
        return _DONE if (tools_used or removed) else ""
    if (removed or tools_used) and _looks_like_source_dump(cleaned):
        return _DONE
    if removed and _looks_like_source_dump(cleaned) and re.search(
        r"create-react-app|npx |npm start|function App\(",
        cleaned,
        re.I,
    ):
        return _DONE
    if len(cleaned) > 8000:
        return cleaned[:8000].rstrip() + "\n…"
    return cleaned


def repair_utf8_text(text: str) -> str:
    """Undo Latin-1/cp1252 misreads of UTF-8 (letâs → let's)."""
    raw = text or ""
    if not raw:
        return raw
    if not any(ord(ch) >= 0x80 for ch in raw):
        return raw
    try:
        repaired = raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw
    return repaired


