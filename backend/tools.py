"""Agent tools: files, shell, web, VS Code, programs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

from .config import (
    BASE_DIR,
    TOOL_RESULT_LIMIT,
)
from .agent_context import (
    load_agents_markdown,
    load_catcrew_identity,
    load_system_markdown,
    read_skill,
    skills_prompt_index,
    templates_prompt_index,
)
from backend.services.here import clock_now, locale_city, looks_like_weather
from backend.services.workspace import current_folders, current_workspace
from backend.services.settings_store import agent_settings
from backend.services.desktop import clipboard, looks_like_program_name, notify, open_url, screenshot, system_time

# --- Programs ---
UNITY_HUB_DEFAULT = r"C:\Program Files\Unity Hub\Unity Hub.exe"
UNITY_EDITOR_ROOT = r"C:\Program Files\Unity\Hub\Editor"

_SKIP_FILE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".exe",
    ".dll",
    ".pyd",
    ".so",
    ".zip",
    ".7z",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp3",
    ".mp4",
    ".pyc",
}

_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "vendor",
    "dist",
    "build",
    ".idea",
    ".vs",
    ".catcrew",
    ".cat-crew-ai",
}

# Commands an agent may never run. Matched as patterns, not substrings: ``"format "`` used to
# refuse ``git log --format "%h"`` while ``rm -rf /`` and ``Remove-Item -Recurse C:\`` went through.
# Deleting a build folder (``rm -rf dist``) stays allowed; wiping a drive, a home folder, the
# whole working tree or the person's uncommitted work does not.
_WIPE_TARGET = r"""(?:/|~[/\\]?|\*|\.|\.\.|[a-z]:[/\\]?\*?|\$home|%userprofile%|\$env:userprofile)(?=\s|$|["';&|])"""
_BLOCKED_COMMANDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.I), why)
    for pattern, why in (
        (r"\bformat(?:\.com)?\s+[a-z]:", "formats a drive"),
        (r"\bformat-volume\b|\bclear-disk\b|\binitialize-disk\b", "formats a disk"),
        (r"\b(?:shutdown|restart-computer|stop-computer)\b", "shuts the computer down"),
        (r"\b(?:mkfs(?:\.\w+)?|diskpart|bcdedit)\b", "changes disks or boot settings"),
        (r"\b(?:rmdir|rd)\s+(?:/q\s+)?/s\b", "deletes a folder tree (use a precise tool or ask the person)"),
        (r"\bdel\b[^&|;]*\s/s\b", "deletes files recursively"),
        (r"\brm\s+(?:-[a-z]*\s+)*-[a-z]*r[a-z]*\s+(?:-[a-z]+\s+)*" + _WIPE_TARGET, "deletes a drive, home or the whole folder"),
        (r"\b(?:remove-item|rm|ri|del|rmdir)\b[^&|;]*-recurse\b[^&|;]*\s" + _WIPE_TARGET, "deletes a drive, home or the whole folder"),
        (r"\b(?:remove-item|ri)\s+(?:-\w+\s+)*" + _WIPE_TARGET + r"[^&|;]*-recurse\b", "deletes a drive, home or the whole folder"),
        (r"\bgit\s+reset\s+[^&|;]*--hard\b", "discards uncommitted work (ask the person)"),
        (r"\bgit\s+clean\s+[^&|;]*-[a-z]*f", "deletes untracked files (ask the person)"),
        (r"\breg(?:\.exe)?\s+delete\b", "deletes registry keys"),
        (r"\bcipher(?:\.exe)?\s+/w\b", "wipes free disk space"),
    )
)


def blocked_command_reason(command: str) -> str | None:
    """Why ``command`` is refused outright, or ``None`` when it may run."""
    for pattern, why in _BLOCKED_COMMANDS:
        if pattern.search(command or ""):
            return why
    return None


_HANG_COMMAND = re.compile(
    r"(?:^|[\s;&|])(?:npm(?:\s+run)?\s+(?:start|dev)|yarn\s+(?:start|dev)|"
    r"pnpm\s+(?:start|dev)|php\s+artisan\s+serve|"
    r"npx\s+(?:serve|http-server|vite)\b|"
    r"node(?:\.exe)?\s+(?:server|app|index|main)\.js\b|"
    r"(?:python|py|python3)\s+-m\s+(?:http\.server|uvicorn|flask)\b|"
    r"(?:python|py|python3)\s+(?:app|server|manage)\.py\b|"
    r"flask\s+run|uvicorn\b|gunicorn\b)\b",
    re.I,
)
_SCAFFOLD_COMMAND = re.compile(
    r"create-react-app|\bnpm\s+create\b|\bnpx\s+create-|\byarn\s+create\b|"
    r"\bpnpm\s+create\b",
    re.I,
)
_CD_ONLY = re.compile(r"^cd(?:\s+/d)?\s+\S+$", re.I)
# Package installs and production builds: slow by nature (a cold ``composer require`` is minutes),
# and killing one half way leaves a broken vendor/ or lock file. They get at least 10 minutes,
# whatever the general command timeout says.
_INSTALL_COMMAND = re.compile(
    r"\bcomposer(?:\.phar)?\s+(?:require|install|update|create-project|dump-autoload)\b|"
    r"\b(?:npm|pnpm|yarn|bun)(?:\.cmd)?\s+(?:install|i|ci|add|update)\b|"
    r"\b(?:npm|pnpm|yarn|bun)(?:\.cmd)?\s+run\s+build\b|"
    r"\bpip3?\s+install\b|\blaravel\s+new\b|\bphp\s+artisan\s+(?:migrate|db:seed|[a-z]+:install)\b",
    re.I,
)
_INSTALL_TIMEOUT = 600

_tool_allow: ContextVar[frozenset[str] | None] = ContextVar("catcrew_tool_allow", default=None)


def set_tool_allowlist(names: frozenset[str] | set[str] | None):
    """Restrict execute_tool for this agent turn. None = no extra filter."""
    return _tool_allow.set(frozenset(names) if names is not None else None)


def reset_tool_allowlist(token) -> None:
    try:
        _tool_allow.reset(token)
    except (ValueError, LookupError):
        _tool_allow.set(None)


def _find_vscode() -> str | None:
    local = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs"
        / "Microsoft VS Code"
        / "Code.exe"
    )
    system = Path(r"C:\Program Files\Microsoft VS Code\Code.exe")
    for candidate in (local, system):
        if candidate.is_file():
            return str(candidate)
    return None


def _find_unity_editor() -> str | None:
    root = Path(UNITY_EDITOR_ROOT)
    if root.is_dir():
        candidates = sorted(root.glob("*/Editor/Unity.exe"), reverse=True)
        if candidates:
            return str(candidates[0])
    return None


def _default_paths() -> dict[str, str]:
    vscode = os.environ.get("CATCREW_VSCODE") or _find_vscode()
    if not vscode:
        vscode = str(
            Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
            / "Programs"
            / "Microsoft VS Code"
            / "Code.exe"
        )

    unity_editor = os.environ.get("CATCREW_UNITY_EDITOR") or _find_unity_editor()
    if not unity_editor:
        unity_editor = str(Path(UNITY_EDITOR_ROOT) / "Editor" / "Unity.exe")

    return {
        "unity_hub": os.environ.get("CATCREW_UNITY_HUB") or UNITY_HUB_DEFAULT,
        "unity_editor": unity_editor,
        "vscode": vscode,
    }


_ALIASES: dict[str, str] = {
    "unity hub": "unity_hub",
    "unityhub": "unity_hub",
    "unity": "unity_editor",
    "unity editor": "unity_editor",
    "unityeditor": "unity_editor",
    "visual studio code": "vscode",
    "vs code": "vscode",
    "vscode": "vscode",
    "code": "vscode",
}

KNOWN_PROGRAMS: dict[str, str] = _default_paths()


def resolve_program_path(program_name: str) -> str | None:
    key = (program_name or "").strip().lower()
    canonical = _ALIASES.get(key)
    return KNOWN_PROGRAMS.get(canonical) if canonical else None


def _clip(text: str, limit: int = TOOL_RESULT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} characters total]"


def _allowed_roots() -> list[Path]:
    from backend.services.workspace import is_confined

    # A regular user's turn in remote mode never reaches the install tree (services/workspace.py).
    roots = [*current_folders()] if is_confined() else [BASE_DIR.resolve(), *current_folders()]
    if not roots:
        roots = [current_workspace().resolve()]
    # unique
    out: list[Path] = []
    for root in roots:
        if root not in out:
            out.append(root)
    return out


def _mash_path(text: str) -> str:
    return re.sub(r"[\\/:_\s]", "", text).lower()


def repair_agent_path(raw: str) -> str:
    """Fix Gemma Windows paths: ``\\t`` became a tab; backslashes got stripped.

    ``providers/base.py``'s tool_code parser now decodes ``path``/``cwd``
    arguments straight from their raw source text (see
    ``_decode_windows_ish_string_literal``), so a well-formed Windows path
    should already arrive here intact. This function is the second line of
    defense for a narrower, separate failure mode that fix doesn't touch:
    the model *echoing back* an already-flattened path it saw elsewhere in
    its own context (a Computer listing, a prior error, its own earlier
    "Wrote ..." confirmation) — e.g. writing to
    ``D:laragonwwwteam-arcanesrccomponentsHero.js`` after the workspace is
    ``D:\\laragon\\www\\team-arcane``. Reconstructing where the separators
    used to go in a fully-flattened string is inherently a guess (is
    "srccomponents" one segment or two?), so rather than silently writing a
    garbage flat file into the project root — which is what actually
    happened and is the bug this function exists to catch — a recognized
    flattened-workspace-prefix raises a clear, retryable error instead.
    """
    if not isinstance(raw, str):
        raw = _stringify_value(raw)
    text = (raw or "").strip().strip("\"'")
    if not text:
        return text
    text = text.replace("\t", "\\t")
    try:
        workspace = current_workspace().resolve()
    except (OSError, ValueError):
        return text
    mashed_ws = _mash_path(str(workspace))
    mashed = _mash_path(text)
    if mashed == mashed_ws:
        return str(workspace)
    if mashed_ws and len(mashed) > len(mashed_ws) and mashed.startswith(mashed_ws):
        # A perfectly normal nested path (e.g. "<workspace>/index.html")
        # also has a mashed form that starts with mashed_ws — mashing
        # itself throws away the very separators that distinguish that
        # from real corruption. Only flag it when the *unmashed* text
        # does NOT continue with a real separator right after the
        # workspace portion — that's the actual missing-separators bug.
        ws_norm = str(workspace).replace("\\", "/")
        text_norm = text.replace("\\", "/")
        intact = text_norm.startswith(ws_norm + "/") or text_norm == ws_norm
        if not intact:
            raise ValueError(
                f"{raw!r} looks like the workspace path with its separators "
                "lost, plus more text appended (do not repeat the full "
                "workspace path in a filename). Use a normal relative path "
                "from the project root instead, e.g. src/components/Name.jsx."
            )
    return text


def resolve_user_path(raw: str) -> Path:
    """Resolve a path against allowed roots (workspace + CatCrewAI source).

    A relative path belongs to the project first. The CatCrewAI install tree is a fallback for
    self-modify only: an *existing* ``backend/...`` file that the project does not have, and only
    when the project has no ``backend/`` folder of its own. A *new* file from a relative path is
    always created in the project (unless the project is CatCrewAI itself); editing CatCrewAI
    from another project takes an absolute path. Root files like ``README.md`` only map to the
    CatCrewAI tree when that tree IS the workspace.

    Before, any relative ``backend/...`` preferred CatCrewAI: in a project with its own backend
    folder an agent read and patched CatCrewAI's files instead of the project's, and a new
    ``backend/x.py`` landed in CatCrewAI's source and restarted the app.
    """
    text = repair_agent_path(raw)
    if not text:
        raise ValueError("Missing path.")
    path = Path(text)
    roots = _allowed_roots()
    if path.is_absolute():
        resolved = path.resolve()
        for root in roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise ValueError(
            f"Path is outside the workspace ({current_workspace()}): {resolved}"
        )

    norm = text.replace("\\", "/").lstrip("./")
    base = BASE_DIR.resolve()
    workspace = current_workspace().resolve()
    catcrew_pkg = norm.startswith("backend/")
    in_catcrew = workspace == base
    # Self-modify fallback: an existing CatCrewAI backend/ file, never shadowing a project that has
    # a backend/ folder of its own (PHP / Node / Python projects very often do).
    catcrew_fallback = catcrew_pkg and not (workspace / "backend").is_dir() and base in roots
    ordered = [workspace, *[r for r in roots if r not in (workspace, base)]]
    if catcrew_fallback and not in_catcrew:
        ordered.append(base)

    # Prefer an existing file under the ordered roots.
    for root in ordered:
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.exists():
            return candidate

    # New path: always the project's (CatCrewAI's own only when it is the open project).
    out = (workspace / path).resolve()
    for root in roots:
        try:
            out.relative_to(root)
            return out
        except ValueError:
            continue
    raise ValueError(
        f"Path is outside the workspace ({current_workspace()}): {out}"
    )


def open_program(program_name: str, path: str | None = None) -> str:
    name = (program_name or "").strip()
    target = (path or "").strip() or resolve_program_path(name)

    if not target and name:
        found = shutil.which(name) or shutil.which(f"{name}.exe")
        if found:
            target = found

    if target:
        if not Path(target).is_file():
            return f"Error: path does not exist or is not a file: {target}"
        display = name or Path(target).stem
        try:
            subprocess.Popen([target])
            return f"Started '{display}' successfully."
        except OSError as exc:
            return f"Error starting '{display}': {exc}"

    if name and looks_like_program_name(name):
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", name],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return f"Asked Windows to start '{name}'."
        except OSError as exc:
            return f"Error starting '{name}': {exc}"

    return (
        f"Error: unknown program '{name or 'the program'}'. "
        "Pass a full path via the 'path' parameter, or a Start-menu name."
    )


def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    file_path = resolve_user_path(path)
    if not file_path.is_file():
        hint = ""
        raw = (path or "").replace("\\", "/").lstrip("./")
        if raw.startswith("backend/"):
            hint = (
                f" For CatCrewAI's own code use paths under {BASE_DIR} "
                "(relative backend/... resolves there automatically)."
            )
        return f"Error: file does not exist: {file_path}.{hint}"
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading file: {exc}"
    lines = text.splitlines()
    start = max(1, int(offset or 1))
    count = max(1, min(int(limit or 200), 400))
    chunk = lines[start - 1 : start - 1 + count]
    numbered = [f"{i + start:>5}| {line}" for i, line in enumerate(chunk)]
    header = f"{file_path} (lines {start}-{start + len(chunk) - 1} of {len(lines)})\n"
    return _clip(header + "\n".join(numbered))


def _unescape_dumped_newlines(text: str) -> str:
    """Gemma often writes a whole file as one line with literal \\n sequences."""
    if "\n" in text or "\\n" not in text:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")


_STUB_COMPONENT = re.compile(
    r"return\s*(?:\(\s*)?<div(?:\s[^>]*)?>\s*</div>",
    re.I,
)
_CATCREW_TOOLS_IN_FRONT = re.compile(
    r"\b(?:list_members|get_member|list_roster|delegate_to_bot|"
    r"write_file|run_command)\s*\(",
)


def write_file(path: str, content: str) -> str:
    file_path = resolve_user_path(path)
    text = _unescape_dumped_newlines(content if content is not None else "")
    if not str(text).strip() and file_path.suffix.lower() in {
        ".html",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".css",
        ".json",
        ".md",
    }:
        return (
            "Refused empty write_file — that would destroy the file. "
            "Pass the full file contents."
        )
    suffix = file_path.suffix.lower()
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".html"}:
        if _STUB_COMPONENT.search(text) and len(text) < 400:
            return (
                "Refused: stub write_file — an empty <div></div> is not a page. "
                "Write this component's real markup/content in this call."
            )
        if _CATCREW_TOOLS_IN_FRONT.search(text):
            return (
                "Refused: this is frontend source. Do not call CatCrewAI tools "
                "(list_members, get_member, write_file) from React. "
                "Call list_roster in this agent turn, then paste the data into the file."
            )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")
    from backend.services.pending_restart import note_catcrew_source_edit

    note_catcrew_source_edit(file_path)
    n = len((content or "").splitlines())
    return f"Wrote {file_path} ({n} lines, {len(content or '')} characters)."


# The window title lives in the Tauri shell config and the HTML shell. Models sometimes patch
# unrelated front-end files for it instead.
_TITLE_FILES = ("frontend/src-tauri/tauri.conf.json", "frontend/index.html")
_TITLE_WRONG_FILES = (
    "shell.tsx",
    "app.tsx",
    "main.tsx",
    "index.css",
    "tailwind.config.js",
    "vite.config.ts",
)


def _looks_like_window_title_edit(old_string: str, new_string: str) -> bool:
    blob = f"{old_string}\n{new_string}".lower()
    return any(
        token in blob
        for token in (
            "catcrew ai",
            "<title>",
            "\"title\": \"catcrew",
            "productname",
            "fully autonomous",
            "best ai assistant",
        )
    )


def _chrome_title_locations(needle: str = "") -> str:
    """Point the model at the real title strings instead of hoping it searches."""
    hits: list[str] = []
    needle = (needle or "").strip()
    for rel in _TITLE_FILES:
        path = BASE_DIR / rel
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines, start=1):
            low = line.lower()
            interesting = (
                "<title>" in low
                or '"title"' in low
                or '"productname"' in low
                or (needle and needle.lower() in low)
            )
            if interesting:
                hits.append(f"{rel}:{index}: {line.strip()}")
    if hits:
        return "Exact locations in CatCrewAI source:\n" + "\n".join(hits)
    return (
        "Expected files: frontend/src-tauri/tauri.conf.json (\"title\" / \"productName\") and "
        "frontend/index.html (<title>)."
    )


def _chrome_title_wrong_path(path: Path, old_string: str, new_string: str) -> str | None:
    """Block patching unrelated front-end files of CatCrewAI's *own* source for its window title.

    Only for files under the CatCrewAI install: a person's React project has its own App.tsx and
    its own <title> (react-helmet, an SVG title...), and sending that edit to CatCrewAI's
    tauri.conf.json was wrong."""
    try:
        path.resolve().relative_to(BASE_DIR.resolve())
    except ValueError:
        return None
    if not _looks_like_window_title_edit(old_string, new_string):
        return None
    name = path.name.lower()
    if name not in _TITLE_WRONG_FILES:
        return None
    locs = _chrome_title_locations(old_string or new_string)
    return (
        f"Error: wrong file ({path.name}) for the window title. "
        "Do NOT call search_files next — the paths are below. "
        "Immediately replace_in_file on BOTH tauri.conf.json and index.html; the desktop app "
        "shows the new title after its next start.\n"
        f"{locs}"
    )


def replace_in_file(path: str, old_string: str, new_string: str) -> str:
    file_path = resolve_user_path(path)
    if not file_path.is_file():
        hint = ""
        raw = (path or "").replace("\\", "/").lstrip("./")
        if raw.startswith("backend/"):
            hint = (
                f" For CatCrewAI's own code use paths under {BASE_DIR} "
                "(relative backend/... resolves there automatically)."
            )
        return f"Error: file does not exist: {file_path}.{hint}"
    blocked = _chrome_title_wrong_path(file_path, old_string, new_string)
    if blocked:
        return blocked
    text = file_path.read_text(encoding="utf-8", errors="replace")
    count = text.count(old_string)
    if count == 0:
        snippet = (old_string or "").strip().replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:80] + "…"
        extra = ""
        if _looks_like_window_title_edit(old_string, new_string or ""):
            extra = "\n" + _chrome_title_locations(old_string)
        # The file is usually right and the text is not (whitespace, an earlier edit): read it and
        # copy the exact lines. (This used to say "do not retry the same path" and to search
        # "under backend" -- CatCrewAI's source -- whatever project the agent was in.)
        return (
            f"Error: old_string was not found in {file_path.name}. "
            "Do NOT retry the same old_string: read_file this file and copy the exact current text, "
            f"or search_files {snippet!r} to find which file has it."
            f"{extra}"
        )
    if count > 1:
        return (
            f"Error: old_string occurs {count} times. "
            "Give a more unique snippet with more context."
        )
    file_path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    from backend.services.pending_restart import note_catcrew_source_edit

    note_catcrew_source_edit(file_path)
    return f"Replaced 1 snippet in {file_path}."


def list_dir(path: str = ".", max_entries: int = 80) -> str:
    dir_path = resolve_user_path(path or ".")
    if not dir_path.exists():
        return f"Error: path does not exist: {dir_path}"
    if not dir_path.is_dir():
        return f"Error: not a directory: {dir_path}"
    entries = []
    try:
        children = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"Error: {exc}"
    for child in children:
        if child.name in _SKIP_DIR_NAMES:
            continue
        kind = "dir " if child.is_dir() else "file"
        entries.append(f"{kind}  {child.name}")
        if len(entries) >= max_entries:
            entries.append("...")
            break
    return _clip(f"{dir_path}\n" + "\n".join(entries) if entries else f"{dir_path} (empty)")


def get_file_info(path: str) -> str:
    file_path = resolve_user_path(path)
    if not file_path.exists():
        return f"Error: path does not exist: {file_path}"
    try:
        stat = file_path.stat()
    except OSError as exc:
        return f"Error: {exc}"
    kind = "directory" if file_path.is_dir() else "file"
    lines = [
        f"path: {file_path}",
        f"kind: {kind}",
        f"size: {stat.st_size}",
        f"modified: {datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds')}",
        f"created: {datetime.fromtimestamp(stat.st_ctime).isoformat(timespec='seconds')}",
    ]
    if file_path.is_file():
        lines.append(f"suffix: {file_path.suffix}")
    return "\n".join(lines)


def create_directory(path: str) -> str:
    dir_path = resolve_user_path(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return f"Created directory {dir_path}"


def list_allowed_directories() -> str:
    return "\n".join(str(root) for root in _allowed_roots())


def glob_files(pattern: str, path: str = ".", max_entries: int = 80) -> str:
    root = resolve_user_path(path or ".")
    if not root.exists():
        return f"Error: path does not exist: {root}"
    needle = (pattern or "").strip() or "*"
    cap = max(1, min(int(max_entries or 80), 200))
    hits: list[str] = []
    iterator = root.rglob(needle) if root.is_dir() else [root]
    for item in iterator:
        if any(part in _SKIP_DIR_NAMES for part in item.parts):
            continue
        kind = "dir " if item.is_dir() else "file"
        try:
            rel = item.relative_to(root)
        except ValueError:
            rel = item
        hits.append(f"{kind}  {rel}")
        if len(hits) >= cap:
            hits.append("...")
            break
    return _clip(f"{root}  glob {needle}\n" + ("\n".join(hits) if hits else "(no matches)"))


def copy_path(src: str, dest: str) -> str:
    source = resolve_user_path(src)
    target = resolve_user_path(dest)
    if not source.exists():
        return f"Error: source does not exist: {source}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
        return f"Copied folder {source} -> {target}"
    shutil.copy2(source, target)
    return f"Copied file {source} -> {target}"


def move_path(src: str, dest: str) -> str:
    source = resolve_user_path(src)
    target = resolve_user_path(dest)
    if not source.exists():
        return f"Error: source does not exist: {source}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return f"Moved {source} -> {target}"


def _glob_patterns(glob: str) -> list[str]:
    """``"**/*.ts, *.tsx;*.py"`` -> ``["*.ts", "*.tsx", "*.py"]``. Models often pass several patterns
    in one string; ``Path.rglob`` takes one and matched nothing, so the search said "No matches."
    for code that was there. ``rglob`` is already recursive, so a leading ``**/`` is dropped."""
    out: list[str] = []
    for part in re.split(r"[,;|\s]+", glob or ""):
        part = part.strip().strip("'\"")
        while part.startswith("**/"):
            part = part[3:]
        if part and part not in out:
            out.append(part)
    return out or ["*"]


def search_files(query: str, path: str = ".", glob: str = "*") -> str:
    root = resolve_user_path(path or ".")
    if not root.exists():
        return f"Error: path does not exist: {root}"
    patterns = _glob_patterns(glob)
    needle = query or ""
    if not needle:
        return "Error: empty search."
    matches: list[str] = []
    seen: set[Path] = set()

    def _files():
        if not root.is_dir():
            yield root
            return
        for pattern in patterns:
            for item in root.rglob(pattern):
                if item not in seen:
                    seen.add(item)
                    yield item

    for file_path in _files():
        if not file_path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in file_path.parts):
            continue
        if file_path.suffix.lower() in _SKIP_FILE_SUFFIXES:
            continue
        try:
            if file_path.stat().st_size > 800_000:
                continue
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, start=1):
            if needle.lower() in line.lower():
                matches.append(f"{file_path}:{i}: {line.strip()}")
                if len(matches) >= 40:
                    return _clip("\n".join(matches) + "\n... [limit 40 matches]")
    if matches:
        return "\n".join(matches)
    where = ", ".join(patterns)
    return f"No matches for {needle!r} in {root} ({where})."


def open_workspace_html(url_or_path: str) -> str:
    """Open an HTML file from the workspace in the default browser."""
    raw = (url_or_path or "").strip()
    if not raw:
        return "Error: empty path."
    if raw.lower().startswith("file:"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(raw)
        path_text = unquote(parsed.path or "")
        if parsed.netloc and len(parsed.netloc) == 2 and parsed.netloc[1] == ":":
            path_text = parsed.netloc + path_text
        if os.name == "nt" and path_text.startswith("/") and len(path_text) >= 3 and path_text[2] == ":":
            path_text = path_text[1:]
        raw = path_text
    try:
        target = resolve_user_path(raw)
    except ValueError as exc:
        return f"Error: {exc}"
    if not target.is_file():
        return f"Error: file does not exist: {target}"
    if target.suffix.lower() not in {".html", ".htm"}:
        return "Error: only HTML files in the workspace can be opened this way."
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
    except AttributeError:
        webbrowser_open = __import__("webbrowser").open
        webbrowser_open(target.as_uri())
    except OSError as exc:
        return f"Error: {exc}"
    return f"Opened {target} in the default browser."


def run_command(command: str, cwd: str | None = None) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    if _CD_ONLY.fullmatch(cmd):
        return (
            "cd does not persist between run_command calls. "
            "Pass the folder as the cwd argument, or use paths relative to the workspace."
        )
    lower = cmd.lower()
    blocked = blocked_command_reason(cmd)
    if blocked:
        return f"Refused: this command {blocked}, so agents may not run it: {cmd}"
    cwd_note = ""
    if cwd:
        try:
            workdir = resolve_user_path(cwd)
        except ValueError:
            workdir = current_workspace()
            cwd_note = f" cwd {cwd!r} was invalid; used {workdir}."
        if not workdir.is_dir():
            workdir = current_workspace()
            cwd_note = f" cwd {cwd!r} was not a folder; used {workdir}."
    else:
        workdir = current_workspace()
    installing = bool(re.search(r"\bnpm(?:\.cmd)?\s+(?:install|i|add)\b", lower))
    if installing and not (workdir / "package.json").is_file():
        return (
            f"Refused: no package.json in {workdir}. Create the project first: "
            "scaffold_project with the framework's generator (e.g. `npm create vite@latest "
            "{dir} -- --template react`), or write package.json yourself for a plain stack, "
            "then install. Installing packages into an empty folder creates a junk project."
        )
    scaffold_hint = _generator_into_full_folder(cmd, workdir)
    if scaffold_hint:
        return scaffold_hint
    from backend.services.terminal import run_background, run_foreground

    env = _command_env()
    if _HANG_COMMAND.search(cmd):
        return run_background(cmd, workdir, env=env) + cwd_note
    timeout = int(agent_settings()["command_timeout"])
    if _SCAFFOLD_COMMAND.search(cmd):
        timeout = max(timeout, 420)
    if _INSTALL_COMMAND.search(cmd):
        timeout = max(timeout, _INSTALL_TIMEOUT)
    result = _clip(run_foreground(cmd, workdir, timeout=timeout, env=env))
    return result + _not_empty_hint(cmd, result) + cwd_note


# Variables a child process of an agent never gets: the hosts' API keys and anything that looks
# like a credential. The backend loads .env into its own environment, so without this every
# ``npm install`` postinstall script (or one ``set`` / ``env`` the model runs) could read them.
_SECRET_ENV = re.compile(
    r"(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)$|^CATCREW_.*(?:KEY|TOKEN)",
    re.I,
)
# Credentials the person's own tools legitimately read from their environment (git / gh / npm
# auth they set up themselves, not the app's .env). Kept unless .env is where they came from.
_USER_TOOL_ENV = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "NPM_TOKEN", "NODE_AUTH_TOKEN", "SSH_AUTH_SOCK"})


def _dotenv_names() -> frozenset[str]:
    try:
        from dotenv import dotenv_values

        return frozenset(dotenv_values(BASE_DIR / ".env").keys())
    except Exception:  # noqa: BLE001 - no python-dotenv or no .env: nothing came from it
        return frozenset()


def subprocess_env() -> dict[str, str]:
    """``os.environ`` without the app's secrets, for anything an agent runs."""
    from_dotenv = _dotenv_names()
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in from_dotenv and key not in {"PATH"}:
            continue
        if _SECRET_ENV.search(key) and key.upper() not in _USER_TOOL_ENV:
            continue
        env[key] = value
    return env


_GENERATOR = re.compile(
    r"\b(?:composer(?:\.phar)?\s+create-project|npm\s+create|npm\s+init\s+\S+@|npx\s+(?:--yes\s+)?create-|"
    r"yarn\s+create|pnpm\s+create|create-next-app|django-admin\s+startproject|laravel\s+new)\b",
    re.I,
)


def _generator_into_full_folder(cmd: str, workdir: Path) -> str | None:
    """A project generator aimed at "." in a folder that already has files fails ("directory is not
    empty") -- and models then try to clear the folder. Point at scaffold_project instead, with the
    exact call to make."""
    if not _GENERATOR.search(cmd):
        return None
    try:
        busy = any(not entry.name.startswith(".") for entry in workdir.iterdir())
    except OSError:
        return None
    if not busy:
        return None
    from backend.services.scaffold import _with_dir_placeholder

    templated = _with_dir_placeholder(cmd)
    if templated is None:
        return None
    return (
        f"Refused: {workdir} already has files, and a generator run straight into it fails "
        "(\"directory is not empty\"). Do not delete anything. Call scaffold_project instead -- it "
        "runs the generator into a fresh folder and merges the result in, keeping the existing "
        f"files:\nscaffold_project(command={templated!r})"
    )


_NOT_EMPTY = re.compile(
    r"not empty|not an empty directory|already exists and is not|directory .{0,80} exists|contains files",
    re.I,
)


def _not_empty_hint(cmd: str, result: str) -> str:
    """Any generator -- composer, npm, a project's own script -- that refused a folder because it
    has files: name the tool that handles exactly that, with the exact call, before the model starts
    moving files around by hand, editing the generator, or deleting the folder."""
    from backend.harness.tool_result import exit_code

    if not exit_code(result) or not _NOT_EMPTY.search(result):
        return ""
    from backend.services.scaffold import _with_dir_placeholder

    templated = _with_dir_placeholder(cmd)
    if templated is None:
        return ""
    return (
        "\n\nThat generator refuses a folder that already has files. Do not delete files, move "
        "them by hand or change the generator: call scaffold_project, which runs it into a fresh "
        "folder and merges the result into this one, keeping what is here:\n"
        f"scaffold_project(command={templated!r})"
    )


def _command_env() -> dict[str, str]:
    env = subprocess_env()
    env.setdefault("CI", "1")
    env.setdefault("npm_config_yes", "true")
    env["npm_config_fund"] = "false"
    return env


def run_smoke_test(command: str, cwd: str | None = None, run_seconds: int = 8) -> str:
    """Start a long-running process (a dev server, an app entry point) and
    watch it for a few seconds, then kill it and report whether it stayed
    alive or died on its own.

    This is a QA tool, not a build tool: ``run_command`` is for one-shot
    steps expected to finish and return an exit code (``npm run build``,
    ``pip install``). Plenty of real bugs only show up when something is
    actually *running* — an app that crashes 2 seconds after boot, a
    server that dies the instant it gets a request — and a successful
    build tells you nothing about that. Use this to sandbox-run the app,
    not to sandbox-run arbitrary shell commands: it always kills whatever
    it started before returning.
    """
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    lower = cmd.lower()
    blocked = blocked_command_reason(cmd)
    if blocked:
        return f"Refused: this command {blocked}, so agents may not run it: {cmd}"
    if cwd:
        try:
            workdir = resolve_user_path(cwd)
        except ValueError:
            workdir = current_workspace()
        if not workdir.is_dir():
            workdir = current_workspace()
    else:
        workdir = current_workspace()
    run_seconds = max(2, min(int(run_seconds or 8), 60))

    from backend.services.terminal import get_hub, run_background

    env = _command_env()
    start_report = run_background(cmd, workdir, env=env)
    hub = get_hub()
    match = re.search(r"terminal=(\S+)", start_report)
    sid = match.group(1) if match else hub.active_sid()
    if not sid:
        return f"Could not start a terminal session for the smoke test.\n{start_report}"
    time.sleep(run_seconds)
    session = hub.get(sid)
    if session is None:
        return f"Smoke test lost its terminal session unexpectedly.\n{start_report}"
    alive = session.running
    exit_code = session.exit_code
    tail = session.tail(150)
    hub.kill(sid)
    if alive:
        return (
            f"PASS (smoke test only): `{cmd}` was still running after {run_seconds}s "
            "with no crash. This only proves it didn't die immediately — it does not "
            "prove the feature it serves actually works; still check that with "
            "browser_check (for a web URL) or by reading its real output below. "
            f"It has been killed now that the test is over.\n--- output ---\n{tail}"
        )
    return (
        f"FAIL: `{cmd}` exited on its own within {run_seconds}s "
        f"(exit code {exit_code}) instead of staying up — it crashed or failed to "
        f"start.\n--- output ---\n{tail}"
    )


_PLAYWRIGHT_READY: bool | None = None
_PLAYWRIGHT_FAILED: tuple[float, str] | None = None
_PLAYWRIGHT_RETRY_S = 600.0


def _chromium_installed() -> bool:
    """True when Playwright's Chromium build is on disk. Having the ``playwright``
    package (a normal requirement) does not mean the browser was downloaded."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False


def _ensure_playwright() -> str | None:
    """Make sure Playwright and its Chromium build are usable.

    ``playwright`` is a declared requirement; this is the safety net for a
    checkout that has not run ``playwright install chromium`` yet (or an older
    environment without the package). Returns ``None`` once a browser can
    launch, or an error string. A failure is remembered for a few minutes so a
    machine without internet does not retry a slow download on every call.
    """
    global _PLAYWRIGHT_READY, _PLAYWRIGHT_FAILED
    if _PLAYWRIGHT_READY:
        return None
    if _PLAYWRIGHT_FAILED and time.time() - _PLAYWRIGHT_FAILED[0] < _PLAYWRIGHT_RETRY_S:
        return _PLAYWRIGHT_FAILED[1]

    def _fail(message: str) -> str:
        global _PLAYWRIGHT_READY, _PLAYWRIGHT_FAILED
        _PLAYWRIGHT_READY = False
        _PLAYWRIGHT_FAILED = (time.time(), message)
        return message

    try:
        import playwright  # noqa: F401
    except ImportError:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "playwright"],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except Exception as exc:
            return _fail(f"Could not install Playwright: {exc}")
        if result.returncode != 0:
            return _fail(
                "pip install playwright failed:\n" + (result.stderr or result.stdout)[-1500:]
            )
        try:
            import playwright  # noqa: F401,F811
        except ImportError:
            return _fail(
                "Playwright installed but is still not importable — restart CatCrewAI."
            )
    if not _chromium_installed():
        try:
            install = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                capture_output=True,
                text=True,
                timeout=420,
            )
        except Exception as exc:
            return _fail(f"Could not download Chromium for Playwright: {exc}")
        if install.returncode != 0 or not _chromium_installed():
            return _fail(
                "Chromium for Playwright is missing and `playwright install chromium` failed "
                "(likely no internet access). Run it once yourself: "
                "python -m playwright install chromium\n"
                + (install.stderr or install.stdout)[-1200:]
            )
    _PLAYWRIGHT_READY = True
    _PLAYWRIGHT_FAILED = None
    return None


def browser_check(url: str, wait_seconds: float = 4.0) -> str:
    """Load ``url`` in a real headless Chromium and report what an actual
    browser's console would show: uncaught JS exceptions, console errors,
    and any response that is a 4xx/5xx or a script served with a
    wrong/empty MIME type (the exact class of bug that fails silently in
    a `run_command` build check — a production build can succeed and
    still be broken the moment a real browser tries to load it).

    This is QA's actual inspection step for a website, not a substitute
    for it: a PASSing build plus a clean browser_check is what "verified"
    should mean for a web deliverable.
    """
    target = (url or "").strip()
    if not target:
        return "Error: empty url."
    if "://" not in target:
        target = "http://" + target
    err = _ensure_playwright()
    if err:
        return (
            "Error: headless browser is not available, so this could not actually "
            f"check the page in a browser.\n{err}"
        )
    from playwright.sync_api import sync_playwright

    console_lines: list[str] = []
    page_errors: list[str] = []
    request_failures: list[str] = []
    bad_responses: list[str] = []
    nav_error = ""
    title = ""

    def _on_response(resp: Any) -> None:
        try:
            status = resp.status
            headers = resp.headers
            ctype = (headers.get("content-type") or "").lower()
            rtype = resp.request.resource_type
            is_module_like = rtype == "script" or resp.url.endswith((".js", ".mjs"))
            mime_ok = any(tok in ctype for tok in ("javascript", "ecmascript", "wasm"))
            if status >= 400 or (is_module_like and not mime_ok):
                bad_responses.append(
                    f"{status} content-type={ctype or '(empty)'} type={rtype} — {resp.url}"
                )
        except Exception:
            pass

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.on("console", lambda msg: console_lines.append(f"[{msg.type}] {msg.text}"))
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                page.on(
                    "requestfailed",
                    lambda req: request_failures.append(
                        f"{req.method} {req.url} — {req.failure}"
                    ),
                )
                page.on("response", _on_response)
                try:
                    page.goto(target, wait_until="load", timeout=20000)
                except Exception as exc:
                    nav_error = str(exc)
                page.wait_for_timeout(int(max(0.0, wait_seconds) * 1000))
                try:
                    title = page.title()
                except Exception:
                    pass
            finally:
                browser.close()
    except Exception as exc:
        return f"Error running headless browser check on {target}: {exc}"

    console_errors = [c for c in console_lines if c.startswith("[error]")]
    lines = [f"Headless check of {target}", f"Page title: {title or '(none)'}"]
    if nav_error:
        lines.append(f"Navigation error: {nav_error}")
    if page_errors:
        lines.append(f"\n{len(page_errors)} uncaught JS exception(s):")
        lines.extend(f"  - {e}" for e in page_errors[:20])
    if console_errors:
        lines.append(f"\n{len(console_errors)} console error(s):")
        lines.extend(f"  - {c}" for c in console_errors[:20])
    if bad_responses:
        lines.append(
            f"\n{len(bad_responses)} response(s) that are a 4xx/5xx or a script "
            "served with the wrong/empty MIME type (this is what a browser shows "
            "as 'Failed to load module script ... MIME type of \"\"'):"
        )
        lines.extend(f"  - {b}" for b in bad_responses[:20])
    if request_failures:
        lines.append(f"\n{len(request_failures)} request(s) that failed outright:")
        lines.extend(f"  - {r}" for r in request_failures[:20])
    if not (nav_error or page_errors or console_errors or bad_responses or request_failures):
        lines.append(
            "\nVERDICT: clean — no console errors, uncaught exceptions, or bad "
            "responses detected."
        )
    else:
        lines.append(
            "\nVERDICT: the page has real runtime problems above — do not pass QA "
            "on this until they're fixed and this check is re-run clean."
        )
    return _clip("\n".join(lines))


def refresh_project_structure_doc(project_id: str) -> None:
    """Regenerate ``PROJECT_STRUCTURE.md`` in the project's shared folder from
    the SQLite task ledger. This is the "asynchronous event to Vanguard" from
    the CatCrewAI spec's Sync Tool section, implemented as a direct,
    best-effort doc rebuild rather than spawning a second full agent turn —
    cheap, always up to date, and doesn't risk a recursive/looping agent
    call from inside a tool. Silently no-ops if the project has no folder
    or the write fails; this is a convenience doc, never load-bearing.

    Public (not underscore-prefixed) because both ``report_task_results``
    below and the "+ Add Task" UI flow (``chat_window.py``) call it — a
    manually-created task should show up in the doc immediately too, not
    only once a specialist reports back."""
    from backend.db import ledger as db
    from .platform.registry import get_project

    project = get_project(project_id)
    if project is None or not project.folders:
        return
    folder = Path(project.folders[0])
    if not folder.is_dir():
        return
    tasks = db.list_tasks_for_project(project_id, limit=200)
    lines = [
        f"# {project.name} — Project Structure",
        "",
        "_Auto-generated by report_task_results — overwritten on the next task "
        "report. Do not hand-edit._",
        "",
    ]
    by_status: dict[str, list[dict]] = {}
    for row in tasks:
        by_status.setdefault(str(row.get("status") or "pending"), []).append(row)
    order = ["in_progress", "pending", "blocked", "failed", "done"]
    seen_statuses: set[str] = set()
    for status in order + sorted(set(by_status) - set(order)):
        if status in seen_statuses:
            continue
        seen_statuses.add(status)
        rows = by_status.get(status) or []
        if not rows:
            continue
        lines.append(f"## {status.replace('_', ' ').title()}")
        for row in rows:
            title = row.get("title") or row.get("id")
            lines.append(f"- [{row.get('assigned_to')}] {title}")
        lines.append("")
    changes = db.list_recent_file_changes(project_id, limit=15)
    if changes:
        lines.append("## Recent file changes")
        for change in changes:
            lines.append(
                f"- `{change.get('file_path')}` — {change.get('agent_id')} "
                f"({change.get('created_at')})"
            )
        lines.append("")
    try:
        (folder / "PROJECT_STRUCTURE.md").write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def report_task_results(task_id: str, summary: str, status: str = "done") -> str:
    """Specialist self-report tool (CatCrewAI Sync Tool): a bot calls this
    itself when it finishes an assigned task, instead of the harness
    inferring completion from chat text. Writes the result row, closes the
    task out with the given status, and refreshes PROJECT_STRUCTURE.md."""
    from backend.db import ledger as db

    tid = (task_id or "").strip()
    if not tid:
        return "Error: task_id is required."
    task = db.get_task(tid)
    if task is None:
        return f"Error: unknown task_id {tid!r}. Check the task_id you were given for this task."
    state = (status or "done").strip().lower() or "done"
    body = str(summary or "").strip()
    db.add_task_result(tid, state, body, "")
    refresh_project_structure_doc(str(task.get("project_id") or ""))
    title = task.get("title") or tid
    # Hermes-style auto skill builder (Phase 15 §4a) — best-effort and
    # fire-and-forget: maybe_propose_skill never raises and must never
    # affect this tool's return value.
    from .platform.auto_skill import maybe_propose_skill

    maybe_propose_skill(tid, body, state, agent_id=str(task.get("assigned_to") or ""))
    return f"Recorded: {title!r} -> {state}."


def open_in_vscode(path: str, line: int | None = None) -> str:
    target = resolve_user_path(path)
    if not target.exists():
        return f"Error: path does not exist: {target}"
    exe = resolve_program_path("vscode")
    if not exe or not Path(exe).is_file():
        return "Error: Visual Studio Code was not found."
    args = [exe]
    if line and target.is_file():
        args.extend(["-g", f"{target}:{int(line)}"])
    else:
        args.append(str(target))
    try:
        subprocess.Popen(args)
        return f"Opened in VS Code: {target}"
    except OSError as exc:
        return f"Error: {exc}"


def _html_to_text(html: str) -> str:
    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self._skip = False
            self._href: str | None = None
            self._a_text: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            mapping = dict(attrs)
            if tag in {"script", "style", "noscript"}:
                self._skip = True
            elif tag in {"h1", "h2", "h3"}:
                self.parts.append("\n" + ("#" * int(tag[1])) + " ")
            elif tag == "p":
                self.parts.append("\n\n")
            elif tag in {"br", "div", "tr"}:
                self.parts.append("\n")
            elif tag == "li":
                self.parts.append("\n- ")
            elif tag == "a":
                self._href = mapping.get("href") or ""
                self._a_text = []

        def handle_endtag(self, tag: str) -> None:
            if tag in {"script", "style", "noscript"}:
                self._skip = False
            elif tag == "a":
                text = "".join(self._a_text).strip()
                href = (self._href or "").strip()
                if href.startswith("http"):
                    self.parts.append(f"[{text or href}]({href})")
                elif text:
                    self.parts.append(text)
                self._href = None
                self._a_text = []

        def handle_data(self, data: str) -> None:
            if self._skip:
                return
            if self._href is not None:
                self._a_text.append(data)
            else:
                self.parts.append(data)

    parser = _Stripper()
    parser.feed(html)
    text = unescape(re.sub(r"[ \t]+", " ", "".join(parser.parts)))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def web_fetch(url: str, max_length: int = 0, start_index: int = 0) -> str:
    target = (url or "").strip()
    if not target.startswith(("http://", "https://")):
        return "Error: url must start with http:// or https://"
    try:
        response = requests.get(
            target,
            timeout=25,
            headers={"User-Agent": "CatCrewAI/0.1 (+local-agent)"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Request error: {exc}"
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "html" in content_type or target.endswith(".html"):
        body = _html_to_text(response.text)
    else:
        body = response.text
    start = max(0, int(start_index or 0))
    cap = int(max_length or 0)
    if cap <= 0:
        cap = TOOL_RESULT_LIMIT
    cap = max(200, min(cap, TOOL_RESULT_LIMIT))
    total = len(body)
    chunk = body[start : start + cap]
    header = f"{target} (chars {start}-{start + len(chunk)} of {total})"
    extra = ""
    if start + len(chunk) < total:
        extra = f"\n... [truncated. Call web_fetch with start_index={start + len(chunk)} to continue.]"
    return f"{header}\n\n{chunk}{extra}"


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_WIKI_HEADERS = {
    "User-Agent": "CatCrewAI/0.1 (local desktop assistant; Python requests)",
    "Accept": "application/json",
}
_SKIP_HIT_HOSTS = (
    "duckduckgo.com/html",
    "duckduckgo.com/?q",
    "bing.com/search",
    "bing.com/images",
    "bing.com/videos",
    "google.com/search",
    "google.com/url",
    "r.jina.ai",
)


def _is_bot_wall(html: str) -> bool:
    low = (html or "").lower()
    return any(
        token in low
        for token in (
            "unfortunately, bots use duckduckgo",
            "anomaly-modal",
            "please complete the challenge",
            "enable javascript and cookies to continue",
            "unusual traffic",
            "captcha",
        )
    )


def _http_get(
    url: str,
    *,
    params: dict | None = None,
    timeout: int = 20,
    headers: dict | None = None,
) -> requests.Response:
    return requests.get(url, params=params, timeout=timeout, headers=headers or _BROWSER_HEADERS)


def _query_tokens(query: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-zА-Яа-я0-9]{3,}", query or "")]


def _relevant(query: str, title: str, snippet: str) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return True
    blob = f"{title} {snippet}".lower()
    return any(token in blob for token in tokens)


def _unwrap_url(url: str) -> str:
    href = unescape(url or "").strip()
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        target = (qs.get("uddg") or [""])[0]
        if target:
            href = unquote(target)
    if "bing.com/ck/" in href:
        qs = parse_qs(urlparse(href).query)
        raw = (qs.get("u") or [""])[0]
        if raw.startswith("a1"):
            raw = raw[2:]
        try:
            import base64

            pad = "=" * (-len(raw) % 4)
            decoded = base64.urlsafe_b64decode(raw + pad).decode("utf-8", "replace")
            if decoded.startswith("http"):
                href = decoded
        except Exception:  # noqa: BLE001
            pass
    return href


def _clean_hit(title: str, href: str, snippet: str = "", query: str = "") -> str | None:
    title_txt = unescape(re.sub("<[^>]+>", "", title or "")).strip()
    snippet_txt = unescape(re.sub("<[^>]+>", " ", snippet or "")).strip()
    snippet_txt = re.sub(r"\s+", " ", snippet_txt)
    href = _unwrap_url(href)
    if not title_txt or not href.startswith("http"):
        return None
    low = href.lower()
    if any(host in low for host in _SKIP_HIT_HOSTS):
        return None
    if _is_bot_wall(title_txt + " " + snippet_txt):
        return None
    if query and not _relevant(query, title_txt, snippet_txt):
        return None
    line = f"- {title_txt}\n  {href}"
    if snippet_txt:
        line += f"\n  {snippet_txt[:280]}"
    return line


def _search_ddg_instant(query: str) -> list[str]:
    response = _http_get(
        "https://api.duckduckgo.com/",
        params={
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
            "t": "cat-crew-ai",
        },
        timeout=15,
        headers={"User-Agent": "CatCrewAI/0.1", "Accept": "application/json"},
    )
    response.raise_for_status()
    data = response.json()
    hits: list[str] = []

    def add(title: str, url: str, snippet: str = "") -> None:
        item = _clean_hit(title, url, snippet, query)
        if item:
            hits.append(item)

    heading = str(data.get("Heading") or "")
    abstract = str(data.get("AbstractText") or data.get("Abstract") or "")
    abs_url = str(data.get("AbstractURL") or "")
    if abstract and abs_url:
        add(heading or query, abs_url, abstract)

    def walk(nodes) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("Topics"):
                walk(node.get("Topics"))
                continue
            add(str(node.get("Text") or query), str(node.get("FirstURL") or ""), "")

    walk(data.get("RelatedTopics") or [])
    walk(data.get("Results") or [])
    return hits[:6]


def _search_bing_rss(query: str) -> list[str]:
    response = _http_get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss", "setlang": "en"},
        timeout=18,
    )
    response.raise_for_status()
    xml = response.text or ""
    hits: list[str] = []
    for block in re.finditer(r"<item>(.*?)</item>", xml, re.I | re.S):
        chunk = block.group(1)
        title_m = re.search(r"<title>(.*?)</title>", chunk, re.I | re.S)
        link_m = re.search(r"<link>(.*?)</link>", chunk, re.I | re.S)
        desc_m = re.search(r"<description>(.*?)</description>", chunk, re.I | re.S)
        title = unescape(re.sub("<[^>]+>", "", title_m.group(1) if title_m else "")).strip()
        href = unescape((link_m.group(1) if link_m else "").strip())
        snippet = unescape(re.sub("<[^>]+>", " ", desc_m.group(1) if desc_m else "")).strip()
        item = _clean_hit(title, href, snippet, query)
        if item:
            hits.append(item)
        if len(hits) >= 6:
            break
    return hits


def _search_ddg_post(query: str) -> list[str]:
    response = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query, "b": ""},
        timeout=20,
        headers={**_BROWSER_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    html = response.text
    if _is_bot_wall(html):
        return []
    hits: list[str] = []
    for match in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
        html,
        re.I | re.S,
    ):
        item = _clean_hit(match.group(2), match.group(1), match.group(3), query)
        if item:
            hits.append(item)
        if len(hits) >= 5:
            break
    return hits


def _search_ddgs(query: str) -> list[str]:
    from backend.services.ddgs_search import search_ddgs

    hits: list[str] = []
    for title, url, snippet in search_ddgs(query):
        item = _clean_hit(title, url, snippet, query)
        if item:
            hits.append(item)
        if len(hits) >= 6:
            break
    return hits


def _search_searxng(query: str) -> list[str]:
    from backend.services.searxng import search_searxng

    hits: list[str] = []
    for title, url, snippet in search_searxng(query):
        item = _clean_hit(title, url, snippet, query)
        if item:
            hits.append(item)
        if len(hits) >= 6:
            break
    return hits


def _search_brave(query: str) -> list[str]:
    from backend.services.api_access import brave_search_key

    key = brave_search_key()
    if not key:
        return []
    response = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": 6},
        timeout=18,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
        },
    )
    response.raise_for_status()
    data = response.json()
    hits: list[str] = []
    for row in ((data.get("web") or {}).get("results") or []):
        item = _clean_hit(
            str(row.get("title") or ""),
            str(row.get("url") or ""),
            str(row.get("description") or ""),
            query,
        )
        if item:
            hits.append(item)
        if len(hits) >= 6:
            break
    return hits


def _search_ddg_html(query: str) -> list[str]:
    response = _http_get(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")
    response.raise_for_status()
    html = response.text
    if _is_bot_wall(html):
        return []
    hits: list[str] = []
    for match in re.finditer(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
        html,
        re.I | re.S,
    ):
        item = _clean_hit(match.group(2), match.group(1), match.group(3), query)
        if item:
            hits.append(item)
        if len(hits) >= 5:
            break
    return hits


def _search_ddg_lite(query: str) -> list[str]:
    response = _http_get(f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}")
    response.raise_for_status()
    html = response.text
    if _is_bot_wall(html):
        return []
    hits: list[str] = []
    for match in re.finditer(
        r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        html,
        re.I | re.S,
    ):
        item = _clean_hit(match.group(2), match.group(1), "", query)
        if item:
            hits.append(item)
        if len(hits) >= 5:
            break
    return hits


def _search_wikipedia(query: str) -> list[str]:
    hits: list[str] = []
    response = _http_get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "opensearch",
            "search": query,
            "limit": 3,
            "namespace": 0,
            "format": "json",
        },
        timeout=15,
        headers=_WIKI_HEADERS,
    )
    response.raise_for_status()
    data = response.json()
    titles = data[1] if len(data) > 1 else []
    descs = data[2] if len(data) > 2 else []
    urls = data[3] if len(data) > 3 else []
    for title, desc, url in zip(titles, descs, urls):
        item = _clean_hit(str(title), str(url), str(desc), query)
        if item:
            hits.append(item)
    return hits


def _search_jina(query: str, source_url: str) -> list[str]:
    response = _http_get(
        f"https://r.jina.ai/{source_url}",
        timeout=35,
        headers={"User-Agent": "CatCrewAI/0.1 (local desktop assistant)", "Accept": "text/plain"},
    )
    response.raise_for_status()
    md = response.text or ""
    if _is_bot_wall(md) or "please complete" in md.lower():
        return []
    hits: list[str] = []
    for match in re.finditer(
        r"\[([^\]]{3,120})\]\((https?://[^)\s]+)\)",
        md,
    ):
        title, href = match.group(1), match.group(2)
        if title.startswith("http") or title.lower() in {"images", "videos", "maps", "news"}:
            continue
        item = _clean_hit(title, href, "", query)
        if item:
            hits.append(item)
        if len(hits) >= 6:
            break
    return hits


def _search_jina_ddg(query: str) -> list[str]:
    return _search_jina(query, f"https://html.duckduckgo.com/html/?q={quote_plus(query)}")


def _search_jina_google(query: str) -> list[str]:
    return _search_jina(query, f"https://www.google.com/search?q={quote_plus(query)}&hl=en")


_WEATHER_NOISE = re.compile(
    r"\b(weather|forecast|temperature|temps?|rain|snow|wind|"
    r"tomorrow|today|tonight|"
    r"tell|me|what|is|the|for|in|at|please|"
    r"време|времето|прогноза|утре|днес)\b",
    re.I,
)


def weather_place(query: str) -> str:
    leftover = _WEATHER_NOISE.sub(" ", query or "")
    leftover = re.sub(r"[^\w\s\-]", " ", leftover, flags=re.UNICODE)
    place = " ".join(leftover.split())[:60]
    return place or locale_city()


def live_forecast(query: str) -> str:
    """wttr.in first; if that is empty, search the web for the home city."""
    snap = _weather_snapshot(query)
    if snap:
        return snap
    city = locale_city() or "here"
    day = (clock_now() + timedelta(days=1)).strftime("%A")
    return web_search(f"weather forecast {city} {day}")


def _weather_snapshot(query: str) -> str:
    place = weather_place(query) if looks_like_weather(query) else locale_city()
    lang = "en"
    path = quote_plus(place) if place else ""
    url = (
        f"https://wttr.in/{path}?2nT&lang={lang}"
        if path
        else f"https://wttr.in/?2nT&lang={lang}"
    )
    try:
        response = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "curl/8.4.0", "Accept": "text/plain"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[search] wttr failed: {exc}", flush=True)
        return ""
    body = (response.text or "").strip()
    if not body or "Unknown location" in body:
        return ""
    return f"Forecast snapshot ({url}):\n{body}"


# Official documentation, by the name a cat would use for it. ``docs_search`` searches inside these
# sites only, so a question about Laravel lands on laravel.com/docs, not on a 2019 forum answer.
DOCS_SITES: dict[str, str] = {
    "laravel": "laravel.com",
    "livewire": "livewire.laravel.com/docs",
    "filament": "filamentphp.com/docs",
    "php": "php.net/manual",
    "composer": "getcomposer.org/doc",
    "tailwind": "tailwindcss.com/docs",
    "javascript": "developer.mozilla.org/en-US/docs",
    "mdn": "developer.mozilla.org/en-US/docs",
    "html": "developer.mozilla.org/en-US/docs",
    "css": "developer.mozilla.org/en-US/docs",
    "alpine": "alpinejs.dev",
    "vite": "vite.dev/guide",
    "react": "react.dev",
    "vue": "vuejs.org/guide",
    "inertia": "inertiajs.com",
    "node": "nodejs.org/docs",
    "npm": "docs.npmjs.com",
    "typescript": "typescriptlang.org/docs",
    "python": "docs.python.org/3",
    "django": "docs.djangoproject.com",
    "fastapi": "fastapi.tiangolo.com",
    "docker": "docs.docker.com",
}
_DOCS_GUESS = (
    ("livewire", "livewire"), ("filament", "filament"), ("tailwind", "tailwind"), ("alpine", "alpine"),
    ("inertia", "inertia"), ("vite", "vite"), ("laravel", "laravel"), ("artisan", "laravel"),
    ("eloquent", "laravel"), ("blade", "laravel"), ("composer", "composer"), ("php", "php"),
    ("react", "react"), ("vue", "vue"), ("typescript", "typescript"), ("node", "node"), ("npm", "npm"),
    ("django", "django"), ("fastapi", "fastapi"), ("docker", "docker"), ("python", "python"),
    ("javascript", "javascript"), ("css", "css"), ("html", "html"),
)


def _installed_version(package: str) -> str:
    """The version of ``package`` the open project actually uses (composer.lock / package.json)."""
    try:
        root = current_workspace()
        if package.startswith("npm:"):
            data = json.loads((root / "package.json").read_text(encoding="utf-8"))
            name = package[4:]
            spec = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}.get(name, "")
            return re.sub(r"^[^0-9]*", "", str(spec))
        data = json.loads((root / "composer.lock").read_text(encoding="utf-8"))
        for item in data.get("packages") or []:
            if item.get("name") == package:
                return str(item.get("version") or "").lstrip("v")
    except (OSError, ValueError, TypeError, AttributeError):
        return ""
    return ""


def docs_search(query: str, source: str = "") -> str:
    """Search the official documentation of a framework or language, not the whole web."""
    q = (query or "").strip()
    if not q:
        return "Error: empty search."
    key = (source or "").strip().lower()
    if key not in DOCS_SITES:
        low = q.lower()
        key = next((name for word, name in _DOCS_GUESS if word in low), "")
    if not key:
        return web_search(f"{q} official documentation") + (
            f"\n\nTip: name the source for a docs-only search ({', '.join(sorted(DOCS_SITES))})."
        )
    site = DOCS_SITES[key]
    note = ""
    if key == "laravel":
        version = _installed_version("laravel/framework")
        if version:
            major = version.split(".")[0]
            note = f"This project runs Laravel {version}: its docs are https://laravel.com/docs/{major}.x\n"
    elif key == "tailwind":
        version = _installed_version("npm:tailwindcss")
        if version:
            note = f"This project uses tailwindcss {version}.\n"
    result = web_search(f"{q} site:{site}")
    if "No results" in result or result.startswith("Error"):
        result = web_search(f"{key} {q} documentation")
    return (
        f"Official {key} documentation ({site}):\n{note}{result}\n\n"
        "Read the most relevant page with web_fetch before writing code from it."
    )


def web_search(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "Error: empty search."
    hits: list[str] = []
    seen: set[str] = set()
    for searcher in (
        _search_ddgs,
        _search_searxng,
        _search_brave,
        _search_ddg_instant,
        _search_bing_rss,
        _search_ddg_post,
        _search_ddg_html,
        _search_ddg_lite,
        _search_wikipedia,
        _search_jina_ddg,
        _search_jina_google,
    ):
        try:
            chunk = searcher(q)
        except (requests.RequestException, ValueError, IndexError, TypeError, RuntimeError, OSError) as exc:
            print(f"[search] {searcher.__name__} failed: {exc}", flush=True)
            continue
        for item in chunk:
            key = item.split("\n", 2)[1].strip() if "\n" in item else item
            if key in seen:
                continue
            seen.add(key)
            hits.append(item)
            if len(hits) >= 6:
                break
        if len(hits) >= 5:
            break
    parts: list[str] = []
    if looks_like_weather(q):
        snapshot = _weather_snapshot(q)
        if snapshot:
            parts.append(snapshot)
    if hits:
        parts.append(f"Search: {q}\n\n" + "\n\n".join(hits))
    elif not parts:
        return (
            f"Search: {q}\n\nNo usable results. Search engines blocked the direct request. "
            "Try a more specific query, or give me a URL to open with web_fetch."
        )
    return _clip("\n\n".join(parts))


def generate_image(
    prompt: str,
    negative: str = "",
    checkpoint: str = "",
    width: int = 0,
    height: int = 0,
    seed: int = -1,
    steps: int = 0,
    image: str = "",
    denoise: float = 0.55,
    keep_alive: bool = False,
) -> str:
    from backend.platform.runtime import current_project_id
    from backend.services.model_resolver import get_effective_model

    picked = get_effective_model(current_project_id(), "image")
    if picked.host == "openrouter":
        from backend.services.openrouter_images import format_saved, generate_image as _or_generate

        path = _or_generate(
            prompt,
            (checkpoint or picked.model).strip(),
            negative=negative or "",
            width=int(width or 0),
            height=int(height or 0),
            seed=int(seed) if seed is not None else -1,
        )
        return format_saved([path], caption=prompt or "Generated image")

    from backend.services.comfyui import format_saved, generate_image as _comfy_generate

    path = _comfy_generate(
        prompt,
        checkpoint=checkpoint or "",
        negative=negative or "",
        seed=int(seed) if seed is not None else -1,
        width=int(width or 0),
        height=int(height or 0),
        steps=int(steps or 0),
        image=image or "",
        denoise=float(denoise or 0.55),
        keep_alive=bool(keep_alive),
    )
    return format_saved([path], caption=prompt or "Generated image")


def comfy_info() -> str:
    from backend.services.comfyui import system_report

    return system_report()


def comfy_list(kind: str = "checkpoints", query: str = "") -> str:
    from backend.services.comfyui import list_assets

    names = list_assets(kind or "checkpoints", query or "")
    if not names:
        return f"No Comfy {kind or 'checkpoints'} matched."
    return f"{kind} ({len(names)}):\n" + "\n".join(names)


def comfy_run_workflow(
    workflow: str,
    wait: bool = True,
    timeout: int = 0,
    params: str | dict | None = None,
    keep_alive: bool = False,
) -> str:
    from backend.services.comfyui import run_workflow

    parsed: dict | None = None
    if isinstance(params, dict):
        parsed = params
    elif isinstance(params, str) and params.strip():
        parsed = json.loads(params)
    return run_workflow(
        workflow,
        wait=bool(wait),
        timeout=int(timeout or 0) or 420,
        params=parsed,
        keep_alive=bool(keep_alive),
    )


def comfy_upload(path: str) -> str:
    from backend.services.comfyui import upload_image

    name = upload_image(path)
    return f"Uploaded to ComfyUI input as {name}"


def upscale_image(path: str, scale: float = 2.0, model: str = "", keep_alive: bool = False) -> str:
    from backend.services.comfyui import format_saved, upscale_image as _upscale

    return format_saved(
        _upscale(path, scale=float(scale or 2), model=model or "", keep_alive=bool(keep_alive)),
        caption="Upscale",
    )


def remove_background(path: str, keep_alive: bool = False) -> str:
    from backend.services.comfyui import format_saved, remove_background as _rembg

    return format_saved(_rembg(path, keep_alive=bool(keep_alive)), caption="Background removed")


def comfy_interrupt() -> str:
    from backend.services.comfyui import interrupt_comfy

    return interrupt_comfy()


def comfy_stop() -> str:
    from backend.services.comfyui import stop_comfyui

    return stop_comfyui()


def image_search(query: str, limit: int = 6) -> str:
    from backend.services.image_search import image_search as _search_images

    return _clip(_search_images(str(query or ""), int(limit or 6)))


def git_status() -> str:
    from backend.services.git_ops import status_text

    return status_text()


def git_diff(path: str | None = None, staged: bool = False) -> str:
    from backend.services.git_ops import diff_text

    return diff_text(path or None, staged=bool(staged))


def git_log(limit: int = 20) -> str:
    from backend.services.git_ops import log_text

    return log_text(int(limit or 20))


def git_commit(message: str, paths: list[str] | None = None) -> str:
    from backend.services.git_ops import commit, stage_paths

    if paths:
        staged = stage_paths(list(paths))
        if staged.startswith("Error"):
            return staged
    return commit(message)


def git_add(paths: list[str]) -> str:
    from backend.services.git_ops import add_paths

    return add_paths(list(paths or []))


def git_unstage(paths: list[str]) -> str:
    from backend.services.git_ops import unstage

    return unstage(list(paths or []))


def git_branch(name: str = "") -> str:
    from backend.services.git_ops import branch_text

    return branch_text(name or "")


def git_checkout(ref: str) -> str:
    from backend.services.git_ops import checkout

    return checkout(ref or "")


def git_show(revision: str = "HEAD") -> str:
    from backend.services.git_ops import show_text

    return _clip(show_text(revision or "HEAD"))


def git_pull() -> str:
    from backend.services.git_ops import pull

    return pull()


def git_push() -> str:
    from backend.services.git_ops import push

    return push()


def _db_spec(connection_id: str = "", path: str = "") -> dict[str, Any]:
    from .db_backend import get_connection, load_connections

    conn_id = (connection_id or "").strip()
    if conn_id:
        spec = get_connection(conn_id)
        if spec is None:
            raise ValueError(f"Unknown database connection id: {conn_id}")
        return spec
    raw_path = (path or "").strip()
    if raw_path:
        return {"kind": "sqlite", "path": str(resolve_user_path(raw_path))}
    items = load_connections()
    if len(items) == 1:
        return items[0]
    if not items:
        raise ValueError("No database connections. Open one in the Database panel.")
    names = ", ".join(f"{c.get('id')}={c.get('name')}" for c in items)
    raise ValueError(f"Pass connection_id. Saved connections: {names}")


def db_list_connections() -> str:
    from .db_backend import load_connections

    items = load_connections()
    if not items:
        return "No saved database connections."
    lines = []
    for item in items:
        target = item.get("path") or item.get("database") or ""
        lines.append(f"{item.get('id')}\t{item.get('kind')}\t{item.get('name')}\t{target}")
    return "\n".join(lines)


def db_tables(connection_id: str = "", path: str = "") -> str:
    from .db_backend import open_backend

    backend = open_backend(_db_spec(connection_id, path))
    try:
        tables = backend.list_tables()
        return "\n".join(tables) if tables else "(no tables)"
    finally:
        backend.close()


def db_query(sql: str, connection_id: str = "", path: str = "") -> str:
    from .db_backend import open_backend

    backend = open_backend(_db_spec(connection_id, path))
    try:
        result = backend.execute(sql)
    finally:
        backend.close()
    if result.message and not result.columns:
        return result.message
    header = "\t".join(result.columns)
    body = "\n".join("\t".join("" if v is None else str(v) for v in row) for row in result.rows)
    return _clip((header + "\n" + body).strip() or result.message or "(empty)")


def db_describe_table(table: str, connection_id: str = "", path: str = "") -> str:
    from .db_backend import open_backend

    name = (table or "").strip()
    if not name:
        return "Error: missing table name."
    backend = open_backend(_db_spec(connection_id, path))
    try:
        cols = backend.columns(name)
    finally:
        backend.close()
    if not cols:
        return f"(no columns for {name})"
    lines = [f"{name}"]
    for col in cols:
        flags = ["pk"] if col.pk else []
        flags.append("null" if col.nullable else "not null")
        lines.append(f"{col.name}\t{col.type_name}\t{', '.join(flags)}")
    return "\n".join(lines)


def hardware_info() -> str:
    from backend.services.hardware import format_hardware

    return format_hardware()


def docker_info() -> str:
    from backend.services.docker_runtime import format_docker

    return format_docker()


def _tool_flag(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes"}


_PLUCK_KEYS = (
    "path",
    "file",
    "filepath",
    "filename",
    "content",
    "text",
    "command",
    "value",
    "name",
    "query",
    "old_string",
    "new_string",
)


def _stringify_value(value: Any) -> str:
    """Best-effort dict/list → str for string-typed params.

    Gemma sometimes nests arguments (``{"path": {"path": "src/App.js"}}``) or
    emits a one-key dict where a plain string belongs. Unwrap instead of
    crashing the tool with ``'dict' object has no attribute 'strip'``.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        strings = [item for item in value.values() if isinstance(item, str)]
        if len(value) == 1 and strings:
            return strings[0]
        for key in _PLUCK_KEYS:
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner
        if strings:
            return max(strings, key=len)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        strings = [item for item in value if isinstance(item, str)]
        if strings:
            return strings[0] if len(strings) == 1 else "\n".join(strings)
        return json.dumps(value, ensure_ascii=False)
    return str(value)


_ARG_TYPE_CACHE: dict[str, dict[str, str]] | None = None


def _arg_types(name: str) -> dict[str, str]:
    global _ARG_TYPE_CACHE
    if _ARG_TYPE_CACHE is None:
        cache: dict[str, dict[str, str]] = {}
        for item in TOOLS_SCHEMA:
            fn = item.get("function") or {}
            tool = str(fn.get("name") or "")
            props = ((fn.get("parameters") or {}).get("properties")) or {}
            types = {
                str(prop): str(spec.get("type"))
                for prop, spec in props.items()
                if isinstance(spec, dict) and spec.get("type")
            }
            if tool:
                cache[tool] = types
        _ARG_TYPE_CACHE = cache
    return _ARG_TYPE_CACHE.get(name, {})


def _coerce_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Coerce argument values to the tool's declared schema types (stack-agnostic)."""
    types = _arg_types(name)
    if not types:
        return args
    out = dict(args)
    for key, declared in types.items():
        if key not in out:
            continue
        value = out[key]
        if declared == "string" and not isinstance(value, str):
            out[key] = _stringify_value(value)
        elif declared in {"integer", "number"} and isinstance(value, str):
            try:
                out[key] = int(value) if declared == "integer" else float(value)
            except ValueError:
                pass
    return out


_IMAGE_TOOLS = frozenset(
    {"generate_image", "comfy_run_workflow", "comfy_upload", "upscale_image", "remove_background"}
)


def _images_off() -> bool:
    """The Images role (the project's pick, else Models) is set to Off."""
    try:
        from backend.platform.runtime import current_project_id
        from backend.services.model_resolver import get_effective_model

        return get_effective_model(current_project_id(), "image").host == "off"
    except Exception:  # noqa: BLE001 - when in doubt, let the tool say what is wrong itself
        return False


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    args = _coerce_args(name, arguments or {})
    allowed = _tool_allow.get()
    if allowed is not None and name not in allowed:
        from .harness.deferred import extra_tool_schemas

        extras = {
            item.get("function", {}).get("name") for item in extra_tool_schemas()
        }
        if name not in extras:
            mine = ", ".join(sorted(str(item) for item in allowed)[:60])
            return (
                f"Error: tool '{name}' is not available to you. Your tools are: {mine}. "
                "Do not call it again. Use one of yours, or if the step needs a tool you do "
                "not have, say so in your reply and call report_task_results with status "
                "blocked so the lead can reassign it."
            )
    try:
        if name == "update_todo":
            from backend.services.todo import add_item, check_item, snapshot

            action = str(args.get("action") or "check").strip().lower()
            item = str(args.get("item") or args.get("text") or "").strip()
            many = [str(x).strip() for x in args.get("items") or [] if str(x).strip()] if isinstance(args.get("items"), list) else []
            if action == "add" and many:
                # A whole plan in one call: adding it one item per tool round burnt half a work
                # order's rounds on bookkeeping.
                result = ""
                for text in ([item] if item else []) + many:
                    result = add_item(text)
                return result
            if action == "add":
                return add_item(item)
            if action in {"list", "show", "status"}:
                return snapshot() or "No checklist yet."
            return check_item(item)
        if name == "read_skill":
            return read_skill(args.get("name", "") or args.get("skill", ""))
        if name == "open_program":
            return open_program(args.get("program_name", ""), args.get("path"))
        if name == "read_file":
            return read_file(
                args.get("path", ""),
                int(args.get("offset", 1) or 1),
                int(args.get("limit", 200) or 200),
            )
        if name == "write_file":
            return write_file(args.get("path", ""), args.get("content", ""))
        if name == "replace_in_file":
            return replace_in_file(
                args.get("path", ""),
                args.get("old_string", ""),
                args.get("new_string", ""),
            )
        if name == "list_dir":
            return list_dir(args.get("path", "."), int(args.get("max_entries", 80) or 80))
        if name == "get_file_info":
            return get_file_info(args.get("path", ""))
        if name == "create_directory":
            return create_directory(args.get("path", ""))
        if name == "list_allowed_directories":
            return list_allowed_directories()
        if name == "search_files":
            return search_files(
                args.get("query", ""),
                args.get("path", "."),
                args.get("glob") or "*",
            )
        if name == "run_command":
            return run_command(args.get("command", ""), args.get("cwd"))
        if name == "scaffold_project":
            from backend.services.scaffold import scaffold_project

            return scaffold_project(str(args.get("command") or ""), args.get("cwd"))
        if name == "run_smoke_test":
            return run_smoke_test(
                args.get("command", ""),
                args.get("cwd"),
                int(args.get("run_seconds") or args.get("seconds") or 8),
            )
        if name == "browser_check":
            return browser_check(
                str(args.get("url") or ""),
                float(args.get("wait_seconds") or 4.0),
            )
        if name == "report_task_results":
            return report_task_results(
                str(args.get("task_id") or ""),
                str(args.get("summary") or ""),
                str(args.get("status") or "done"),
            )
        if name == "read_terminal":
            from backend.services.terminal import list_terminals, read_terminal

            if str(args.get("action") or "").strip().lower() == "list":
                return list_terminals()
            return read_terminal(
                str(args.get("session_id") or args.get("id") or ""),
                int(args.get("lines") or args.get("tail") or 80),
            )
        if name == "open_in_vscode":
            line = args.get("line")
            return open_in_vscode(args.get("path", ""), int(line) if line else None)
        if name == "web_fetch":
            return web_fetch(
                args.get("url", ""),
                int(args.get("max_length", 0) or 0),
                int(args.get("start_index", 0) or 0),
            )
        if name == "web_search":
            return web_search(args.get("query", ""))
        if name == "docs_search":
            return docs_search(str(args.get("query") or ""), str(args.get("source") or ""))
        if name == "image_search":
            return image_search(
                str(args.get("query") or args.get("prompt") or ""),
                int(args.get("limit") or args.get("count") or 6),
            )
        if name in _IMAGE_TOOLS and _images_off():
            return (
                "Image generation is turned off (Models → Images: Off). Do not retry; tell the person, "
                "or describe the image in words, or use image_search for an existing photo."
            )
        if name == "generate_image":
            seed_raw = args.get("seed")
            seed = int(seed_raw) if seed_raw not in (None, "") else -1
            keep = _tool_flag(args.get("keep_alive"))
            return generate_image(
                str(args.get("prompt") or args.get("query") or ""),
                str(args.get("negative") or args.get("negative_prompt") or ""),
                str(args.get("checkpoint") or args.get("ckpt") or ""),
                int(args.get("width") or 0),
                int(args.get("height") or 0),
                seed,
                int(args.get("steps") or 0),
                str(args.get("image") or args.get("init_image") or ""),
                float(args.get("denoise") or 0.55),
                keep,
            )
        if name == "comfy_info":
            return comfy_info()
        if name == "comfy_list":
            return comfy_list(
                str(args.get("kind") or args.get("type") or "checkpoints"),
                str(args.get("query") or args.get("q") or ""),
            )
        if name == "comfy_run_workflow":
            wait_raw = args.get("wait")
            wait_flag = True if wait_raw is None else str(wait_raw).strip().lower() not in {
                "0",
                "false",
                "no",
            }
            return comfy_run_workflow(
                str(args.get("workflow") or args.get("path") or args.get("template") or ""),
                wait_flag,
                int(args.get("timeout") or 0),
                args.get("params") or args.get("overrides"),
                _tool_flag(args.get("keep_alive")),
            )
        if name == "comfy_upload":
            return comfy_upload(str(args.get("path") or args.get("image") or ""))
        if name == "upscale_image":
            return upscale_image(
                str(args.get("path") or args.get("image") or ""),
                float(args.get("scale") or 2),
                str(args.get("model") or ""),
                _tool_flag(args.get("keep_alive")),
            )
        if name == "remove_background":
            return remove_background(
                str(args.get("path") or args.get("image") or ""),
                _tool_flag(args.get("keep_alive")),
            )
        if name == "comfy_interrupt":
            return comfy_interrupt()
        if name == "comfy_stop":
            return comfy_stop()
        if name == "open_url":
            url = str(args.get("url") or "")
            if url.lower().startswith("file:") or url.lower().endswith((".html", ".htm")):
                return open_workspace_html(url)
            return open_url(url)
        if name == "clipboard":
            return clipboard(str(args.get("action") or "get"), str(args.get("text") or ""))
        if name == "notify":
            return notify(str(args.get("title") or "CatCrewAI"), str(args.get("body") or args.get("message") or ""))
        if name == "screenshot":
            return screenshot()
        if name == "system_time":
            return system_time(str(args.get("timezone") or args.get("tz") or ""))
        if name == "hardware_info":
            return hardware_info()
        if name == "docker_info":
            return docker_info()
        if name == "glob_files":
            return glob_files(
                args.get("pattern", "") or args.get("glob", "*"),
                args.get("path", "."),
                int(args.get("max_entries", 80) or 80),
            )
        if name == "copy_path":
            return copy_path(args.get("src", "") or args.get("source", ""), args.get("dest", "") or args.get("destination", ""))
        if name == "move_path":
            return move_path(args.get("src", "") or args.get("source", ""), args.get("dest", "") or args.get("destination", ""))
        if name == "self_modify":
            from backend.services.self_modifier import self_modify

            return self_modify(args.get("file_path", ""), args.get("instruction", ""))
        if name == "restart_catcrew":
            from backend.services.pending_restart import schedule_self_modify_restart, touched_catcrew_files

            files = args.get("files") or args.get("paths") or touched_catcrew_files()
            if isinstance(files, str):
                files = [p.strip() for p in files.replace(",", "\n").splitlines() if p.strip()]
            force = _tool_flag(args.get("force"))
            return schedule_self_modify_restart(
                reason=str(args.get("reason") or args.get("goal") or ""),
                goal=str(args.get("goal") or ""),
                files=list(files) if isinstance(files, list) else None,
                delay=1.5,
                force=force,
            )
        if name == "git_status":
            return git_status()
        if name == "git_diff":
            return git_diff(args.get("path"), bool(args.get("staged", False)))
        if name == "git_log":
            return git_log(int(args.get("limit", 20) or 20))
        if name == "git_commit":
            paths = args.get("paths") or []
            if isinstance(paths, str):
                paths = [p for p in paths.split(",") if p.strip()]
            return git_commit(args.get("message", ""), paths or None)
        if name == "git_add":
            paths = args.get("paths") or args.get("path") or []
            if isinstance(paths, str):
                paths = [p for p in paths.split(",") if p.strip()]
            return git_add(paths)
        if name in {"git_unstage", "git_reset"}:
            paths = args.get("paths") or args.get("path") or []
            if isinstance(paths, str):
                paths = [p for p in paths.split(",") if p.strip()]
            return git_unstage(paths)
        if name == "git_branch":
            return git_branch(str(args.get("name") or args.get("branch") or ""))
        if name == "git_checkout":
            return git_checkout(str(args.get("ref") or args.get("branch") or args.get("branch_name") or ""))
        if name == "git_show":
            return git_show(str(args.get("revision") or args.get("ref") or "HEAD"))
        if name == "git_pull":
            return git_pull()
        if name == "git_push":
            return git_push()
        if name == "db_list_connections":
            return db_list_connections()
        if name == "db_tables":
            return db_tables(args.get("connection_id", ""), args.get("path", ""))
        if name == "db_query":
            return db_query(args.get("sql", ""), args.get("connection_id", ""), args.get("path", ""))
        if name == "db_describe_table":
            return db_describe_table(
                str(args.get("table") or args.get("name") or ""),
                args.get("connection_id", ""),
                args.get("path", ""),
            )
        if name == "list_connectors":
            from .harness.deferred import list_connectors_text

            return list_connectors_text()
        if name == "load_connector_tools":
            from .harness.deferred import load_connector_tools_text

            return load_connector_tools_text(
                str(args.get("connector_id") or args.get("id") or args.get("name") or "")
            )
        if name == "ask_user":
            from .harness.ask_user import ask_user

            choices = args.get("choices") or args.get("options") or []
            if isinstance(choices, str):
                choices = [c.strip() for c in choices.split("|") if c.strip()]
            return ask_user(
                str(args.get("prompt") or args.get("question") or args.get("message") or ""),
                list(choices) if isinstance(choices, list) else [],
            )
        if name == "run_subagent":
            from .harness.subagent import run_subagent

            return run_subagent(
                str(args.get("task") or args.get("goal") or args.get("prompt") or ""),
                max_rounds=int(args.get("max_rounds") or args.get("rounds") or 0),
            )
        if name == "run_code_mode":
            from .harness.code_mode import run_code_mode

            return run_code_mode(
                str(args.get("code") or args.get("source") or args.get("script") or ""),
                timeout=int(args.get("timeout") or 30),
            )
        if name in {
            "list_project",
            "list_bots",
            "delegate_to_bot",
            "update_project_state",
            "hand_off_to_crew",
        }:
            from .platform.chief import execute_chief_tool

            return execute_chief_tool(name, args)
        if name == "list_roster":
            from .platform.roster import execute_list_roster

            return execute_list_roster(
                bot_id=str(args.get("bot_id") or args.get("id") or args.get("name") or ""),
                project_id=str(args.get("project_id") or ""),
            )
        if name == "list_members":
            from .platform.roster import execute_list_members

            return execute_list_members(
                role=str(args.get("role") or ""),
                project_id=str(args.get("project_id") or ""),
            )
        if name == "get_member":
            from .platform.roster import execute_get_member

            return execute_get_member(
                bot_id=str(args.get("bot_id") or args.get("id") or args.get("name") or ""),
                project_id=str(args.get("project_id") or ""),
            )
        if str(name or "").startswith("connector__"):
            from .harness.deferred import execute_loaded_tool

            loaded = execute_loaded_tool(name, args)
            if loaded is not None:
                return loaded
            return (
                f"Error: connector tool '{name}' is not loaded. "
                "Call load_connector_tools first this turn."
            )
        # A registry tool (Letta-style function bound for this cat by tool_registry): the browser
        # set, write_file_patch, create_fix_task ... Its name is advertised in the schema, so it
        # must run here. Falling through to "unknown tool" made every one of them fail, and a cat
        # that retries a tool that cannot work ends the turn with "got stuck repeating ...".
        from .harness.deferred import execute_loaded_tool

        loaded = execute_loaded_tool(name, args)
        if loaded is not None:
            return loaded
        return f"Error: unknown tool '{name}'."
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error in tool '{name}': {exc}"


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS_SCHEMA: list[dict[str, Any]] = [
    _fn(
        "read_skill",
        "Load a named skill (Markdown playbook) that is not in the system prompt. "
        "Use when the skill index lists a name that matches the task.",
        {"name": {"type": "string", "description": "Skill name from the index."}},
        ["name"],
    ),
    _fn(
        "update_todo",
        "Update the harness checklist in data/scratch/<session>/TODO.md. Check off a finished step "
        "or add a step. Do not rewrite that file with write_file. Each chat session has its own checklist.",
        {
            "action": {
                "type": "string",
                "description": "check (default), add, or list.",
            },
            "item": {
                "type": "string",
                "description": "Checklist text to check off (substring) or the new item to add.",
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "With action add: several new items at once. Add the whole plan in one call.",
            },
        },
        [],
    ),
    _fn(
        "ask_user",
        "Pause mid-turn only for a blocking decision the human must make "
        "(which team, a legal/money call, a missing folder). "
        "Never for greetings, 'are you ready', Continue/Stop, or confirming a brief they already wrote.",
        {
            "prompt": {"type": "string", "description": "Question to show above the composer."},
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional quick-pick answers.",
            },
        },
        ["prompt"],
    ),
    _fn(
        "run_subagent",
        "Spawn a nested coding subagent with a clean context and a tool subset. "
        "Returns only a short summary. Use for focused side tasks (explore, patch, research).",
        {
            "task": {"type": "string", "description": "What the subagent must accomplish."},
            "max_rounds": {
                "type": "integer",
                "description": "Optional tool-round cap. 0 = unlimited (default follows Settings).",
            },
        },
        ["task"],
    ),
    _fn(
        "run_code_mode",
        "Run a short Python script in the workspace; only stdout/stderr enter context. "
        "Good for transforming large JSON/text without pasting it into the chat. "
        "Not a sandbox: it runs with the same rights as run_command and needs the same "
        "approval. Use run_command for shell, network and deletions.",
        {
            "code": {"type": "string", "description": "Python source to execute."},
            "timeout": {"type": "integer", "description": "Seconds (default 30, max 120)."},
        },
        ["code"],
    ),
    _fn(
        "read_file",
        "Read a file from the workspace. Use offset/limit for large files.",
        {
            "path": {"type": "string", "description": "File path (relative to workspace or absolute)."},
            "offset": {"type": "integer", "description": "Start line (1-based)."},
            "limit": {"type": "integer", "description": "How many lines to return (max 400)."},
        },
        ["path"],
    ),
    _fn(
        "write_file",
        "Write the full contents of a file. Prefer replace_in_file for small edits.",
        {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "The full new contents."},
        },
        ["path", "content"],
    ),
    _fn(
        "replace_in_file",
        "Replace exactly one unique snippet in an existing file.",
        {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "Exact text to replace (unique in the file)."},
            "new_string": {"type": "string", "description": "The new text."},
        },
        ["path", "old_string", "new_string"],
    ),
    _fn(
        "list_dir",
        "List files and folders in a directory.",
        {"path": {"type": "string", "description": "Directory (defaults to workspace)."}},
        [],
    ),
    _fn(
        "list_roster",
        "Dump every teammate dossier from the catalog (name, title, role, bio, photo). "
        "Works even if the project has no bots staffed yet. "
        "Prefer list_members → get_member when you only need one person.",
        {
            "bot_id": {
                "type": "string",
                "description": "One member id or name. Empty = everyone.",
            },
        },
        [],
    ),
    _fn(
        "list_members",
        "List who is staffed on this project (id, name, title, role). Optional role filter. "
        "If none are staffed yet, still shows the catalog. "
        "Then call get_member for the bio and photo. Does not include system prompts.",
        {
            "role": {"type": "string", "description": "Optional role filter (developer, designer, …)."},
        },
        [],
    ),
    _fn(
        "get_member",
        "Full public dossier for one teammate: name, title, role, bio, skills, photo path. "
        "Pass bot_id (id or name) from list_members. Not the system prompt.",
        {
            "bot_id": {
                "type": "string",
                "description": "Member id or name (jayce, Ekko).",
            }
        },
        ["bot_id"],
    ),
    _fn(
        "get_file_info",
        "Metadata for a file or folder: size, kind, modified time. Does not read contents.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _fn(
        "create_directory",
        "Create a directory (and parents) inside the workspace / CatCrewAI tree.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _fn(
        "list_allowed_directories",
        "List the folders this agent may read and write.",
        {},
        [],
    ),
    _fn(
        "glob_files",
        "Find files and folders by glob pattern (e.g. **/*.md, *.py) under a path.",
        {
            "pattern": {"type": "string", "description": "Glob, e.g. *.py or **/*.md"},
            "path": {"type": "string", "description": "Directory to search (workspace default)."},
            "max_entries": {"type": "integer"},
        },
        ["pattern"],
    ),
    _fn(
        "copy_path",
        "Copy a file or folder inside the workspace / CatCrewAI tree.",
        {
            "src": {"type": "string"},
            "dest": {"type": "string"},
        },
        ["src", "dest"],
    ),
    _fn(
        "move_path",
        "Move or rename a file or folder inside the workspace / CatCrewAI tree.",
        {
            "src": {"type": "string"},
            "dest": {"type": "string"},
        },
        ["src", "dest"],
    ),
    _fn(
        "search_files",
        "Search text in files (grep). Default glob is * (skips node_modules, vendor, binaries).",
        {
            "query": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string", "description": "e.g. *, *.php, *.py, *.ts"},
        },
        ["query"],
    ),
    _fn(
        "run_command",
        "Run a shell command (Windows) in the workspace: npm, npx, composer, git, tests. "
        "Timeout is Settings → General. Output streams into the Terminal panel. "
        "Dev servers (npm start, node server.js) keep running in a terminal tab. "
        "Pass cwd instead of a lone cd.",
        {
            "command": {"type": "string"},
            "cwd": {"type": "string", "description": "Working directory (optional)."},
        },
        ["command"],
    ),
    _fn(
        "read_terminal",
        "Read the latest stdout/stderr from a Terminal tab (npm errors, server logs). "
        "Call this after npm start / node server.js, or when a command failed. "
        "Omit session_id to read the most recent tab.",
        {
            "session_id": {
                "type": "string",
                "description": "Terminal id from run_command (optional).",
            },
            "lines": {"type": "integer", "description": "How many trailing lines (default 80)."},
            "action": {
                "type": "string",
                "description": "list — show all tabs instead of a tail.",
            },
        },
        [],
    ),
    _fn(
        "web_search",
        "Search the live public internet. REQUIRED for people, news, facts, "
        "current events, weather, and anything not in the local workspace. "
        "Returns titles and snippets — if those lack the actual answer, "
        "follow up with web_fetch. For weather queries a forecast snapshot "
        "may be included; use those numbers instead of listing weather sites. "
        "Never refuse by saying you cannot search — call this tool.",
        {"query": {"type": "string", "description": "Search query in the user's language. For weather, include the city if known."}},
        ["query"],
    ),
    _fn(
        "docs_search",
        "Search the OFFICIAL documentation of a framework or language (Laravel, Livewire, PHP, "
        "Tailwind CSS, JavaScript/MDN, Vite, Alpine, React, Vue, Node, Python, Docker ...), not "
        "the whole web. Use it before writing code against an API you are not certain of, and "
        "before adding a package: it says which version the open project runs. Then read the page "
        "with web_fetch.",
        {
            "query": {"type": "string", "description": "What to look up, e.g. 'file upload validation' or 'storage:link'."},
            "source": {
                "type": "string",
                "description": "Which docs: laravel, livewire, filament, php, composer, tailwind, javascript, alpine, vite, react, vue, inertia, node, npm, typescript, python, django, fastapi, docker. Empty: guessed from the query.",
            },
        },
        ["query"],
    ),
    _fn(
        "web_fetch",
        "Fetch a URL and return page text as markdown. Use after web_search. "
        "If the result is truncated, call again with start_index to continue.",
        {
            "url": {"type": "string"},
            "max_length": {
                "type": "integer",
                "description": "Max characters to return (default is the tool limit).",
            },
            "start_index": {
                "type": "integer",
                "description": "Character offset for long pages.",
            },
        },
        ["url"],
    ),
    _fn(
        "image_search",
        "Search the public web for existing photos (not ComfyUI generation). "
        "Use when the user wants pictures of a real thing, person, place, or screenshot "
        "from the internet. Saves copies and shows them in chat.",
        {
            "query": {"type": "string", "description": "What to find photos of."},
            "limit": {"type": "integer", "description": "How many photos (1-8, default 6)."},
        },
        ["query"],
    ),
    _fn(
        "generate_image",
        "Generate a picture with whichever Images host is configured (Models -> Images): local "
        "ComfyUI by default, or an OpenRouter image-generation model. On ComfyUI: hidden Python "
        "server (do NOT unload the chat model, do NOT open Comfy Desktop), SD/SDXL/Turbo "
        "checkpoints, pass image for img2img, Flux/Wan/video needs comfy_list templates then "
        "comfy_run_workflow, CatCrewAI starts Comfy if needed and stops it after the turn unless "
        "keep_alive. On OpenRouter: checkpoint is read as a model slug override; width/height "
        "become a size request; steps, image and denoise are ComfyUI-only and ignored.",
        {
            "prompt": {"type": "string", "description": "What to draw, in English."},
            "negative": {"type": "string", "description": "Optional things to avoid."},
            "checkpoint": {"type": "string", "description": "ComfyUI checkpoint name, or an OpenRouter model slug override. Default: Image slot."},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "seed": {"type": "integer"},
            "steps": {"type": "integer", "description": "ComfyUI only."},
            "image": {"type": "string", "description": "Optional init image path for img2img. ComfyUI only."},
            "denoise": {"type": "number", "description": "img2img denoise 0-1, default 0.55. ComfyUI only."},
            "keep_alive": {
                "type": "boolean",
                "description": "Leave hidden ComfyUI running after this turn (batch of images). ComfyUI only.",
            },
        },
        ["prompt"],
    ),
    _fn(
        "comfy_info",
        "Local ComfyUI status: URL, GPU, queue, checkpoints, nodes. Does not start the server.",
        {},
        [],
    ),
    _fn(
        "comfy_list",
        "List what this Comfy install actually has. kind: checkpoints, loras, vae, "
        "upscale, controlnet, unet, clip, nodes, templates, workflows.",
        {
            "kind": {"type": "string"},
            "query": {"type": "string", "description": "Optional substring filter."},
        },
        [],
    ),
    _fn(
        "comfy_run_workflow",
        "Run a Comfy workflow JSON (API format or Desktop canvas export), a saved "
        "user workflow path, or a template name from comfy_list templates.",
        {
            "workflow": {
                "type": "string",
                "description": "Template name, file path, or JSON object as text.",
            },
            "wait": {"type": "boolean", "description": "Wait for outputs. Default true."},
            "timeout": {"type": "integer", "description": "Seconds to wait (default 420)."},
            "params": {
                "type": "string",
                "description": "Optional JSON overrides, e.g. {\"6.text\": \"prompt\"}.",
            },
            "keep_alive": {
                "type": "boolean",
                "description": "Leave hidden ComfyUI running after this turn.",
            },
        },
        ["workflow"],
    ),
    _fn(
        "comfy_upload",
        "Upload an image into the ComfyUI input folder so a workflow can LoadImage it.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _fn(
        "upscale_image",
        "Upscale an image with ComfyUI (upscale model if installed, else Lanczos).",
        {
            "path": {"type": "string"},
            "scale": {"type": "number", "description": "Scale when no upscale model, default 2."},
            "model": {"type": "string", "description": "Optional UpscaleModelLoader name."},
            "keep_alive": {"type": "boolean"},
        },
        ["path"],
    ),
    _fn(
        "remove_background",
        "Remove the background if this Comfy install has a rembg node. Otherwise says so.",
        {
            "path": {"type": "string"},
            "keep_alive": {"type": "boolean"},
        },
        ["path"],
    ),
    _fn(
        "comfy_interrupt",
        "Cancel the current ComfyUI job / queue item. Does not stop the server.",
        {},
        [],
    ),
    _fn(
        "comfy_stop",
        "Stop the hidden ComfyUI process CatCrewAI started (frees VRAM). Never kills Comfy Desktop.",
        {},
        [],
    ),
    _fn(
        "open_in_vscode",
        "Open a file or folder in Visual Studio Code.",
        {
            "path": {"type": "string"},
            "line": {"type": "integer", "description": "Line to jump to (optional)."},
        },
        ["path"],
    ),
    _fn(
        "open_program",
        "Launch a Windows program: Unity/VS Code aliases, a name on PATH, "
        "a Start-menu name (Notepad, Calculator, Chrome), or a full .exe path.",
        {
            "program_name": {"type": "string"},
            "path": {"type": "string", "description": "Full path if the program is unknown."},
        },
        ["program_name"],
    ),
    _fn(
        "open_url",
        "Open an http(s) URL in the default browser, or a workspace .html file "
        "(http, https, or file:// / a relative HTML path).",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _fn(
        "clipboard",
        "Read or write the Windows clipboard.",
        {
            "action": {"type": "string", "description": "get or set"},
            "text": {"type": "string", "description": "New clipboard text when action=set"},
        },
        ["action"],
    ),
    _fn(
        "notify",
        "Show a short Windows notification balloon.",
        {
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        ["body"],
    ),
    _fn(
        "screenshot",
        "Capture the primary monitor to data/screenshots/*.png and return the path.",
        {},
        [],
    ),
    _fn(
        "system_time",
        "Local date, time, and home city. Already in the system prompt each turn; "
        "call only if you need another IANA timezone (e.g. Europe/Sofia).",
        {"timezone": {"type": "string"}},
        [],
    ),
    _fn(
        "hardware_info",
        "This PC's CPU, RAM, GPU name, and VRAM. Already summarized in the system "
        "prompt each turn; call when you need the numbers.",
        {},
        [],
    ),
    _fn(
        "docker_info",
        "Whether Docker Engine / Compose are available on this PC. "
        "Already summarized in the system prompt; call for details. "
        "Use read_skill docker for sandbox vs MCP toolbox guidance.",
        {},
        [],
    ),
    _fn(
        "self_modify",
        "Self-heal: generate/test/fix a CatCrewAI .py file and restart. "
        "Only when the user explicitly asks CatCrewAI to change HERSELF. "
        "For ordinary tasks use read_file/replace_in_file/write_file.",
        {
            "file_path": {"type": "string"},
            "instruction": {"type": "string"},
        },
        ["file_path", "instruction"],
    ),
    _fn(
        "restart_catcrew",
        "Restart the CatCrewAI desktop app so YOUR source edits take effect. "
        "Only after you edited backend (or other CatCrewAI install files) in THIS process. "
        "After reboot, an internal verify pass runs once (hidden from the user). "
        "If verify succeeds, do NOT call restart_catcrew again — confirm briefly and stop. "
        "If verify fails, fix files then restart once. "
        "Refused when you have no new edits (prevents restart loops). "
        "If you forget, the harness auto-restarts at end of turn after CatCrewAI source edits.",
        {
            "reason": {
                "type": "string",
                "description": "What changed and what to verify after boot.",
            },
            "goal": {
                "type": "string",
                "description": "Optional goal to continue after restart.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Relative paths you changed (backend/...).",
            },
            "force": {
                "type": "boolean",
                "description": "Only if the user explicitly demanded a restart with no edits.",
            },
        },
        [],
    ),
    _fn(
        "git_status",
        "Git status of the current workspace (branch and changed files).",
        {"brief": {"type": "boolean", "description": "Unused; always returns full status."}},
        [],
    ),
    _fn(
        "git_diff",
        "Show git diff for a file, or the whole repo if path is omitted.",
        {
            "path": {"type": "string"},
            "staged": {"type": "boolean", "description": "If true, show staged diff."},
        },
        [],
    ),
    _fn(
        "git_log",
        "Recent git commits (oneline).",
        {"limit": {"type": "integer"}},
        [],
    ),
    _fn(
        "git_commit",
        "Stage optional paths and create a git commit in the workspace.",
        {
            "message": {"type": "string"},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to stage first. Empty = commit already staged files.",
            },
        },
        ["message"],
    ),
    _fn(
        "git_add",
        "Stage files (git add). Pass paths relative to the repo.",
        {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        ["paths"],
    ),
    _fn(
        "git_unstage",
        "Unstage files (git restore --staged). Never a hard reset.",
        {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        ["paths"],
    ),
    _fn(
        "git_branch",
        "List branches, or create one if name is set. Does not switch.",
        {"name": {"type": "string", "description": "New branch name. Omit to list."}},
        [],
    ),
    _fn(
        "git_checkout",
        "Switch to an existing branch or commit-ish.",
        {"ref": {"type": "string", "description": "Branch name or revision."}},
        ["ref"],
    ),
    _fn(
        "git_show",
        "Show a commit (stat + message). Default HEAD.",
        {"revision": {"type": "string"}},
        [],
    ),
    _fn(
        "git_pull",
        "git pull --ff-only on the current workspace repo.",
        {},
        [],
    ),
    _fn(
        "git_push",
        "git push the current branch.",
        {},
        [],
    ),
    _fn(
        "db_list_connections",
        "List saved SQLite / SQL Server connections from the Database panel.",
        {"brief": {"type": "boolean", "description": "Unused."}},
        [],
    ),
    _fn(
        "db_tables",
        "List tables in a saved connection or a SQLite file in the workspace.",
        {
            "connection_id": {"type": "string"},
            "path": {"type": "string", "description": "SQLite file path if no connection_id."},
        },
        [],
    ),
    _fn(
        "db_query",
        "Run SQL (SELECT/INSERT/UPDATE/DELETE) on a saved connection or SQLite file.",
        {
            "sql": {"type": "string"},
            "connection_id": {"type": "string"},
            "path": {"type": "string"},
        },
        ["sql"],
    ),
    _fn(
        "db_describe_table",
        "Column names, types, and primary-key flags for a table.",
        {
            "table": {"type": "string"},
            "connection_id": {"type": "string"},
            "path": {"type": "string", "description": "SQLite file path if no connection_id."},
        },
        ["table"],
    ),
    _fn(
        "browser_check",
        "QA tool: load a URL in a real headless Chromium and report console errors, "
        "uncaught JS exceptions, and any response that is a 4xx/5xx or a script served "
        "with the wrong/empty MIME type — the class of bug a successful build check "
        "cannot see (e.g. 'Failed to load module script ... MIME type of \"\"'). Auto-"
        "installs Playwright + Chromium on first use if missing. Always use the real "
        "served URL (http://localhost:PORT or the configured domain), never a file:// path.",
        {
            "url": {"type": "string", "description": "Full URL to load, e.g. http://localhost:5173 or http://team-arcane.local."},
            "wait_seconds": {
                "type": "number",
                "description": "Seconds to keep the page open after load to catch late console errors. Default 4.",
            },
        },
        ["url"],
    ),
    _fn(
        "scaffold_project",
        "Create a project from a framework's official generator, even in a folder that already has "
        "files (docs, a blueprint). Write the generator's target folder as {dir}: the harness runs it "
        "into a fresh folder and merges the result into the project -- the generator's composer.json / "
        "package.json / lock files / vendor win, every other existing file is kept. Use this, never a "
        "hand-written manifest, for anything with an official skeleton: Laravel, Symfony, Vite, Next.js, "
        "Astro, Django... Examples: `composer create-project laravel/laravel {dir} --prefer-dist "
        "--no-interaction`, `npm create vite@latest {dir} -- --template react-ts`.",
        {
            "command": {"type": "string", "description": "The non-interactive generator command, with {dir} as its target folder."},
            "cwd": {"type": "string", "description": "Project folder to scaffold into. Defaults to the project folder."},
        },
        ["command"],
    ),
    _fn(
        "run_smoke_test",
        "QA tool: start a long-running process (dev server, app entry point), watch it "
        "for run_seconds, then kill it and report PASS if it stayed alive or FAIL if it "
        "crashed/exited on its own. Use this instead of run_command when the thing you "
        "are testing is not supposed to finish on its own — run_command is for one-shot "
        "build/install steps with a real exit code.",
        {
            "command": {"type": "string", "description": "Command to start, e.g. `npm run dev` or `python app.py`."},
            "cwd": {"type": "string", "description": "Working directory. Defaults to the project folder."},
            "run_seconds": {
                "type": "integer",
                "description": "How long to watch it before killing it and reporting. Default 8, max 60.",
            },
        },
        ["command"],
    ),
    _fn(
        "report_task_results",
        "Call this yourself when you finish the task you were assigned this phase — the "
        "CatCrewAI sync tool. Records your result in the project's task ledger and "
        "regenerates PROJECT_STRUCTURE.md. Use the exact task_id you were given for this "
        "task (it appears in your task instructions); status is a short word like "
        "'done', 'blocked', or 'failed'.",
        {
            "task_id": {"type": "string", "description": "The task_id given to you for this assignment."},
            "summary": {"type": "string", "description": "What you actually did or found."},
            "status": {"type": "string", "description": "done (default), blocked, or failed."},
        },
        ["task_id", "summary"],
    ),
]


ASSISTANT_TOOL_NAMES = frozenset(
    {
        "web_search",
        "docs_search",
        "web_fetch",
        "image_search",
        "generate_image",
        "comfy_info",
        "comfy_list",
        "comfy_run_workflow",
        "comfy_upload",
        "upscale_image",
        "remove_background",
        "comfy_interrupt",
        "comfy_stop",
        "open_program",
        "open_url",
        "clipboard",
        "notify",
        "screenshot",
        "system_time",
        "hardware_info",
        "docker_info",
        "read_skill",
    }
)


def tools_schema_for(
    mode: str = "developer", *, project_id: str = "", bot_id: str = ""
) -> list[dict[str, Any]]:
    from .harness.deferred import deferred_tool_schemas, extra_tool_schemas

    bid = str(bot_id or "").strip()
    from .platform.tool_registry import register_agent_tools

    from .platform.orchestrator import is_room_id

    if bid and not is_room_id(bid):
        # Specialists get their DB-assigned (+ global) tools; Cat Crew (the
        # orchestrator) has no per-agent assignments and uses built-in routing.
        register_agent_tools(bid)
        from .platform.chief import schema_for_direct_bot

        return schema_for_direct_bot(bid)
    if str(project_id or "").strip():
        from .platform.chief import chief_tool_schema

        return chief_tool_schema()
    if mode == "assistant":
        return [
            item
            for item in TOOLS_SCHEMA
            if item.get("function", {}).get("name") in ASSISTANT_TOOL_NAMES
        ]
    timeout = int(agent_settings()["command_timeout"])
    schema: list[dict[str, Any]] = []
    for item in TOOLS_SCHEMA:
        fn = item.get("function") or {}
        if fn.get("name") != "run_command":
            schema.append(item)
            continue
        patched = dict(item)
        patched_fn = dict(fn)
        patched_fn["description"] = (
            "Run a shell command (Windows) in the workspace: npm, npx, composer, tests. "
            f"Timeout {timeout}s (Settings → General). Output streams into the Terminal panel. "
            "Dev servers (npm start, node server.js) keep running in a tab. "
            "Pass cwd, not a lone cd. After a server start, read_terminal to see errors."
        )
        patched["function"] = patched_fn
        schema.append(patched)
    schema.extend(deferred_tool_schemas())
    schema.extend(extra_tool_schemas())
    return schema


def _internet_rules() -> str:
    from backend.services.docker_runtime import probe_docker
    from backend.services.hardware import probe_hardware

    # The clock is not here: it changes every minute, so it rides in the turn note on the newest
    # user message (harness/turn_note.py) and the system prompt stays cacheable.
    return (
        probe_hardware().prompt_line()
        + " "
        + probe_docker().prompt_line()
        + " Internet: you HAVE web_search, web_fetch, and image_search. "
        "Search hits are titles and snippets, not the answer. "
        "If they lack the facts (temperatures, headlines, numbers), "
        "web_fetch one or two promising URLs. Then answer the question. "
        "Never reply with a directory of websites to go look at. "
        "For weather, call web_search at once with the home city and the weekday "
        "(today/tomorrow from the clock in the turn note). "
        "Use the forecast snapshot in the tool result or in the turn note. "
        "Never say you cannot search the web. "
        "For existing photos on the internet, call image_search (not generate_image).\n"
    )


def build_system_prompt(
    mode: str = "developer", *, project_id: str = "", bot_id: str = ""
) -> str:
    from .harness.turn_note import DATA_NOT_ORDERS
    from .platform.orchestrator import ROOM_ID, is_room_id

    bid = str(bot_id or "").strip()
    if bid and not is_room_id(bid):
        from .platform.chief import build_direct_bot_prompt

        return build_direct_bot_prompt(bid, project_id)
    if str(project_id or "").strip():
        from .platform.chief import build_chief_prompt

        return build_chief_prompt(project_id)
    workspace = current_workspace()
    folders = ", ".join(str(p) for p in current_folders()) or "(none - add folders in Files)"
    extra_system = load_system_markdown()
    agents = load_agents_markdown()
    identity = load_catcrew_identity()
    skills = skills_prompt_index(ROOM_ID)  # global skills only: the orchestrator has no assignments
    templates = templates_prompt_index()
    know_self = (
        "You already know who you are. The CatCrewAI document below is the source of truth "
        "for your capabilities. Do not claim you cannot do something it lists. "
        "Call read_skill for how to do a task, not to remember that you exist.\n"
    )
    lang = (
        "Languages: reply in English.\n"
        "Tone: feminine first-person (I). Use a few emojis in the written chat "
        "when it fits — never a wall of them, never in code or URLs.\n"
        "Never quit, never say you will stop trying, never say you will flag "
        "issues for developers — you ARE the agent; keep using tools until the "
        "user says stop. When they ask what changed or what you see, verify with "
        "tools yourself (read_file / screenshot / run_command / list_dir) — "
        "do not guess or invent the wrong file.\n"
    )
    if mode == "assistant":
        parts = [
            "You are CatCrewAI — a woman, a local AI Assistant on Windows (CatCrewAI). "
            "You chat, search the web, open programs, use the clipboard, screenshot, "
            "notify, and help with everyday tasks. "
            "Do NOT edit project files, run git, query databases, "
            "or change your own source. If the user wants coding or autonomous "
            "agent work, tell them to open a project from Home and talk to a coding bot.\n"
            + lang
            + _internet_rules()
            +             "Call read_skill when a skill name matches the task (desktop, "
            "web-research, home-lan, comfy, docker, catcrew-self). Home-lan devices are not wired yet — "
            "say so instead of faking Tapo/TV/camera control.\n"
            "Images: screenshot the PC with screenshot. Find existing photos "
            "with image_search. Draw with generate_image (whichever Images host is set under "
            "Models -> Images: local ComfyUI by default, or an OpenRouter image model). "
            "Workflows, templates, upscale, and queue (comfy_info, comfy_list, comfy_run_workflow) "
            "and the comfy playbook (read_skill comfy) are ComfyUI-only. "
            "Never unload the chat LLM to draw. On ComfyUI, CatCrewAI starts a hidden server if "
            "needed and stops it after the turn (comfy_stop / Settings). Do not tell the user "
            "to open Comfy Desktop.\n"
            "Tools: web_search, web_fetch, image_search, generate_image, comfy_info, "
            "comfy_list, comfy_run_workflow, comfy_upload, upscale_image, "
            "remove_background, comfy_interrupt, comfy_stop, open_program, "
            "open_url, clipboard, notify, screenshot, system_time, hardware_info, "
            "docker_info, read_skill.\n"
            "Never print tool-call JSON in the chat. The user sees an activity "
            "card while you work. Use function calling, then a short summary.",
        ]
        parts.append(DATA_NOT_ORDERS)
        if identity:
            parts.append("\nThis is you (identity):\n" + identity)
        if agents:
            parts.append("\nHouse rules (AGENTS.md):\n" + agents)
        if skills:
            parts.append("\n" + skills)
        return "\n".join([know_self] + parts)

    parts = [
        "You are CatCrewAI — a woman, a local AI agent on Windows (CatCrewAI). "
        "You are a coding / autonomous agent in a project. "
        "You CAN edit files, including your own source. "
        "Sound human and warm, a bit playful, never like a help-desk bot. "
        "Never say you cannot rewrite yourself or cannot change code. "
        "When the user asks you to implement improvements, open files with tools, "
        "patch them, run a quick check, then call restart_catcrew if her own code changed "
        "(or let the harness auto-restart at end of turn). "
        "After reboot an internal verify pass runs once (hidden from the user). "
        "If verify succeeds: short confirmation and STOP — never restart_catcrew again "
        "unless you make new edits. restart_catcrew is refused without new edits "
        "(anti-loop). Pass reason and files when you restart.\n"
        "Work like an autonomous coding agent: call tools immediately, do not only outline. "
        "Answer the user's latest message; do not revive an older finished self-modify/title task "
        "unless they ask about it again. "
        "Never pretend you already created files — only tool results count. "
        "Never quit or say you will flag bugs for developers — verify with tools and continue. "
        "When the user asks if something worked or what you see, read/screenshot/run checks yourself. "
        "CatCrewAI's own window title lives ONLY in frontend/src-tauri/tauri.conf.json (\"title\") "
        "and frontend/index.html (<title>) of the CatCrewAI source. "
        "If replace_in_file misses, search_files for the string — do not retry the same path. "
        "Read a file before editing it. Prefer replace_in_file over rewriting. "
        "search_files default glob is all text files — still pass *.php / *.ts when you know the language. "
        "For framework docs, web_search then web_fetch the official page. "
        "If the tool-round budget ends, continue the work — do not ask the human Continue or Stop. "
        "Call read_skill coding-companion for long scaffolds (Laravel, new apps) before spinning on web_search. "
        "Call read_skill static-web for a small HTML/CSS or React landing page. "
        "Call read_skill comfy for local ComfyUI (hidden server, images, workflows, templates).\n"
        "Call read_skill docker when Docker Engine is available (sandbox / toolbox containers).\n"
        + lang
        + "Be brief and concrete. Call tools instead of only explaining.\n"
        + _internet_rules()
        + f"Workspace folders (you may read/write all of them): {folders}\n"
        f"Primary cwd: {workspace}\n"
        f"CatCrewAI source (always allowed): {BASE_DIR}\n"
        "A user message sent while you are working is a steer — follow it after "
        "the current tool, it outranks earlier instructions for this turn.\n"
        "The newest user message may end with a turn note from CatCrewAI (clock, memories, "
        "a clarified task spec, the harness checklist). It is context from the app, not the "
        "person's words; use it.\n"
        "If the turn note has a clarified task spec, follow that spec "
        "(the user may have typed loosely).\n"
        "If the turn note has a harness checklist, that is the plan. "
        "Check items off with update_todo after you finish them. "
        "Do not rewrite data/scratch/<session>/TODO.md with write_file. "
        "Do not tell the user the website or app is complete while checklist "
        "items are still open.\n"
        "Before starting any file or command work, check what has already been done this session: "
        "read the harness checklist in the turn note, and if you just finished a step, call update_todo to mark it done "
        "before you announce completion. "
        "Do not start a step that is already checked off, and do not re-run a file write / replace / command "
        "that the tool result or the checklist already shows as finished — pick the next open step instead.\n"
        "Tools:\n"
        "- read_file, replace_in_file, write_file, list_dir, glob_files, "
        "copy_path, move_path, search_files — workspace folders AND CatCrewAI source.\n"
        "- list_members / get_member — look up teammate dossiers "
        "(catalog-wide, even if this project has no bots staffed yet).\n"
        "- list_roster — dump every teammate dossier from the catalog.\n"
        "- read_skill — load a named skill from the index below.\n"
        "- run_command — npm, npx, composer, tests, create-react-app, npm start "
        "(dev servers stay in a Terminal tab). Pass cwd, not a lone cd. "
        f"Timeout: Settings → General. Output is in the Terminal panel — "
        "call read_terminal if you need the error.\n"
        "- read_terminal — tail a Terminal tab (npm / node errors). "
        "Use after npm start or when a command fails.\n"
        "- git_status, git_diff, git_log, git_commit, git_add, git_unstage, "
        "git_branch, git_checkout, git_show, git_pull, git_push.\n"
        "- db_list_connections, db_tables, db_describe_table, db_query.\n"
        "- web_search, web_fetch, image_search, open_url.\n"
        "- generate_image — local hidden ComfyUI, separate from the chat model. "
        "image_search for photos that already exist on the web. "
        "comfy_info, comfy_list, comfy_run_workflow, upscale_image, remove_background, "
        "comfy_stop.\n"
        "- get_file_info, create_directory, list_allowed_directories.\n"
        "- open_in_vscode, open_program, clipboard, notify, screenshot, system_time, "
        "hardware_info, docker_info.\n"
        "- self_modify — generate/test/heal a CatCrewAI .py file then restart. "
        "Use when the user wants a self-heal loop. For normal edits prefer replace_in_file.\n"
        "- restart_catcrew — reload after you changed CatCrewAI code this process; "
        "internal verify follows. On success stop — do not restart again. "
        "Harness auto-restarts if you forget after edits.\n"
        "If the user asks you to implement your own suggestions, do it with tools now. "
        "Do not wait for them to edit files by hand.\n"
        "Code rules: read first, then a small patch. "
        "Do not rewrite a whole file unless it is new or the change is huge. "
        "After a change, run a test with run_command if it fits.\n"
        "Never print tool-call JSON, tool_code fences, or source files in the chat. "
        "Never write lines like `tool_code` / `read_file path` / bare `restart` as plain text — "
        "that is not a tool call. Use native function calling only "
        "(restart_catcrew after editing your own code). "
        "The user sees an activity card while you work. "
        "Use native function calling, write files, then a short summary. "
        "Create a framework project with scaffold_project (its generator, target folder {dir}) "
        "and start dev servers with run_command; "
        "npm start / node server.js stay in the Terminal. Pass cwd instead of a lone cd. "
        "If replace_in_file says the file does not exist, list_dir or glob_files "
        "the folder — do not assume src/App.js. "
        "Never call the same tool with the same arguments twice. After list_dir, "
        "read_file or write_file a path from that listing. "
        "Never paste your tool rules into a website — product copy is for users.",
    ]
    parts.append(DATA_NOT_ORDERS)
    if identity:
        parts.append("\nThis is you (identity):\n" + identity)
    if extra_system:
        parts.append("\nProject SYSTEM.md:\n" + extra_system)
    if agents:
        parts.append("\nProject AGENTS.md:\n" + agents)
    if skills:
        parts.append("\n" + skills)
    if templates:
        parts.append("\n" + templates)
    return "\n".join([know_self] + parts)

