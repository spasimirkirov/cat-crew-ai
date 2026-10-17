"""Agent tools: files, shell, web, VS Code, programs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
    load_jinx_identity,
    load_system_markdown,
    read_skill,
    skills_prompt_index,
    templates_prompt_index,
)
from .here import clock_now, here_and_now, locale_city, looks_like_weather
from .workspace import current_folders, current_workspace
from .settings_store import agent_settings
from .desktop import (
    clipboard,
    looks_like_program_name,
    notify,
    open_url,
    screenshot,
    system_time,
)

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
    ".jinx",
    ".jinx-ai",
}

_BLOCKED_COMMANDS = (
    "format ",
    "shutdown",
    "rmdir /s",
    "del /f /s",
    "rd /s",
    "mkfs",
    "diskpart",
)
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

_tool_allow: ContextVar[frozenset[str] | None] = ContextVar("jinx_tool_allow", default=None)


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
    vscode = os.environ.get("JINX_VSCODE") or _find_vscode()
    if not vscode:
        vscode = str(
            Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
            / "Programs"
            / "Microsoft VS Code"
            / "Code.exe"
        )

    unity_editor = os.environ.get("JINX_UNITY_EDITOR") or _find_unity_editor()
    if not unity_editor:
        unity_editor = str(Path(UNITY_EDITOR_ROOT) / "Editor" / "Unity.exe")

    return {
        "unity_hub": os.environ.get("JINX_UNITY_HUB") or UNITY_HUB_DEFAULT,
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
    roots = [BASE_DIR.resolve(), *current_folders()]
    # unique
    out: list[Path] = []
    for root in roots:
        if root not in out:
            out.append(root)
    return out


def resolve_user_path(raw: str) -> Path:
    """Resolve a path against allowed roots (workspace + Jinx source).

    Relative ``jinx_ai/...``, ``docs/...``, ``main.py``, etc. prefer the Jinx
    install tree even when the session workspace is a different project folder.
    Other relative paths prefer the current workspace, then fall back to any
    allowed root where the file already exists.
    """
    text = (raw or "").strip()
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
    jinxish = (
        norm.startswith("jinx_ai/")
        or norm.startswith("docs/")
        or norm.startswith("tests/")
        or norm in {"main.py", "AGENTS.md", "README.md", "pyproject.toml"}
    )
    base = BASE_DIR.resolve()
    workspace = current_workspace().resolve()
    if jinxish:
        ordered = [base, *[r for r in roots if r != base]]
    else:
        ordered = [workspace, *[r for r in roots if r != workspace]]

    # Prefer an existing file under the ordered roots.
    for root in ordered:
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.exists():
            return candidate

    # New path: create under Jinx source for jinxish, else workspace.
    primary = base if jinxish else workspace
    out = (primary / path).resolve()
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
        if raw.startswith("jinx_ai/"):
            hint = (
                f" For Jinx's own code use paths under {BASE_DIR} "
                "(relative jinx_ai/... resolves there automatically)."
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


def write_file(path: str, content: str) -> str:
    file_path = resolve_user_path(path)
    text = content if content is not None else ""
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
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(text, encoding="utf-8")
    from .pending_restart import note_jinx_source_edit

    note_jinx_source_edit(file_path)
    n = len((content or "").splitlines())
    return f"Wrote {file_path} ({n} lines, {len(content or '')} characters)."


_TITLE_WRONG_FILES = (
    "widgets.py",
    "ui_style.py",
    "fa.py",
    "icon.py",
    "activity.py",
    "chat_transcript.py",
)


def _looks_like_window_title_edit(old_string: str, new_string: str) -> bool:
    blob = f"{old_string}\n{new_string}".lower()
    return any(
        token in blob
        for token in (
            "jinx ai",
            "window_title",
            "qlabel(\"jinx",
            "qlabel('jinx",
            "titlebrand",
            "fully autonomous",
            "best ai assistant",
        )
    )


def _chrome_title_locations(needle: str = "") -> str:
    """Point Gemma at the real title strings instead of hoping she searches."""
    hits: list[str] = []
    needle = (needle or "").strip()
    for rel in ("jinx_ai/titlebar.py", "jinx_ai/config.py"):
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
                "window_title" in low
                or "self.title" in low
                or "qlabel(" in low and "jinx" in low
                or (needle and needle.lower() in low)
            )
            if interesting:
                hits.append(f"{rel}:{index}: {line.strip()}")
    if hits:
        return "Exact locations in Jinx source:\n" + "\n".join(hits)
    return (
        "Expected files: jinx_ai/titlebar.py (self.title = QLabel(...)) and "
        "jinx_ai/config.py (WINDOW_TITLE = \"...\")."
    )


def _chrome_title_wrong_path(path: Path, old_string: str, new_string: str) -> str | None:
    """Block the Gemma habit of patching widgets/ui_style for the window title."""
    if not _looks_like_window_title_edit(old_string, new_string):
        return None
    name = path.name.lower()
    if name not in _TITLE_WRONG_FILES:
        return None
    locs = _chrome_title_locations(old_string or new_string)
    return (
        f"Error: wrong file ({path.name}) for the window / title-bar brand text. "
        "Do NOT call search_files next — the paths are below. "
        "Immediately replace_in_file on BOTH titlebar.py and config.py, then restart_jinx.\n"
        f"{locs}"
    )


def replace_in_file(path: str, old_string: str, new_string: str) -> str:
    file_path = resolve_user_path(path)
    if not file_path.is_file():
        hint = ""
        raw = (path or "").replace("\\", "/").lstrip("./")
        if raw.startswith("jinx_ai/"):
            hint = (
                f" For Jinx's own code use paths under {BASE_DIR} "
                "(relative jinx_ai/... resolves there automatically)."
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
        return (
            f"Error: old_string was not found in {file_path.name}. "
            "Do NOT retry the same path. "
            f"Use the locations below (or search_files {snippet!r} under jinx_ai)."
            f"{extra}"
        )
    if count > 1:
        return (
            f"Error: old_string occurs {count} times. "
            "Give a more unique snippet with more context."
        )
    file_path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    from .pending_restart import note_jinx_source_edit

    note_jinx_source_edit(file_path)
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


def search_files(query: str, path: str = ".", glob: str = "*") -> str:
    root = resolve_user_path(path or ".")
    if not root.exists():
        return f"Error: path does not exist: {root}"
    pattern = (glob or "*").strip() or "*"
    needle = query or ""
    if not needle:
        return "Error: empty search."
    matches: list[str] = []
    iterator = root.rglob(pattern) if root.is_dir() else [root]
    for file_path in iterator:
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
    return "\n".join(matches) if matches else "No matches."


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
    if any(token in lower for token in _BLOCKED_COMMANDS):
        return f"Refused a potentially destructive command: {cmd}"
    workdir = resolve_user_path(cwd) if cwd else current_workspace()
    if not workdir.is_dir():
        return f"Error: cwd is not a directory: {workdir}"
    from .terminal import run_background, run_foreground

    env = _command_env()
    if _HANG_COMMAND.search(cmd):
        return run_background(cmd, workdir, env=env)
    timeout = int(agent_settings()["command_timeout"])
    if _SCAFFOLD_COMMAND.search(cmd):
        timeout = max(timeout, 420)
    return _clip(run_foreground(cmd, workdir, timeout=timeout, env=env))


def _command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("npm_config_yes", "true")
    env["npm_config_fund"] = "false"
    return env


def _start_background(cmd: str, workdir: Path) -> str:
    from .terminal import run_background

    return run_background(cmd, workdir, env=_command_env())


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
            headers={"User-Agent": "JinxAI/0.1 (+local-agent)"},
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
    "User-Agent": "JinxAI/0.1 (local desktop assistant; Python requests)",
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
            "t": "jinx-ai",
        },
        timeout=15,
        headers={"User-Agent": "JinxAI/0.1", "Accept": "application/json"},
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
    from .ddgs_search import search_ddgs

    hits: list[str] = []
    for title, url, snippet in search_ddgs(query):
        item = _clean_hit(title, url, snippet, query)
        if item:
            hits.append(item)
        if len(hits) >= 6:
            break
    return hits


def _search_searxng(query: str) -> list[str]:
    from .searxng import search_searxng

    hits: list[str] = []
    for title, url, snippet in search_searxng(query):
        item = _clean_hit(title, url, snippet, query)
        if item:
            hits.append(item)
        if len(hits) >= 6:
            break
    return hits


def _search_brave(query: str) -> list[str]:
    from .api_access import brave_search_key

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
        headers={"User-Agent": "JinxAI/0.1 (local desktop assistant)", "Accept": "text/plain"},
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
    from .comfyui import format_saved, generate_image as _comfy_generate

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
    from .comfyui import system_report

    return system_report()


def comfy_list(kind: str = "checkpoints", query: str = "") -> str:
    from .comfyui import list_assets

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
    from .comfyui import run_workflow

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
    from .comfyui import upload_image

    name = upload_image(path)
    return f"Uploaded to ComfyUI input as {name}"


def upscale_image(path: str, scale: float = 2.0, model: str = "", keep_alive: bool = False) -> str:
    from .comfyui import format_saved, upscale_image as _upscale

    return format_saved(
        _upscale(path, scale=float(scale or 2), model=model or "", keep_alive=bool(keep_alive)),
        caption="Upscale",
    )


def remove_background(path: str, keep_alive: bool = False) -> str:
    from .comfyui import format_saved, remove_background as _rembg

    return format_saved(_rembg(path, keep_alive=bool(keep_alive)), caption="Background removed")


def comfy_interrupt() -> str:
    from .comfyui import interrupt_comfy

    return interrupt_comfy()


def comfy_stop() -> str:
    from .comfyui import stop_comfyui

    return stop_comfyui()


def image_search(query: str, limit: int = 6) -> str:
    from .image_search import image_search as _search_images

    return _clip(_search_images(str(query or ""), int(limit or 6)))


def git_status() -> str:
    from .git_ops import status_text

    return status_text()


def git_diff(path: str | None = None, staged: bool = False) -> str:
    from .git_ops import diff_text

    return diff_text(path or None, staged=bool(staged))


def git_log(limit: int = 20) -> str:
    from .git_ops import log_text

    return log_text(int(limit or 20))


def git_commit(message: str, paths: list[str] | None = None) -> str:
    from .git_ops import commit, stage_paths

    if paths:
        staged = stage_paths(list(paths))
        if staged.startswith("Error"):
            return staged
    return commit(message)


def git_add(paths: list[str]) -> str:
    from .git_ops import add_paths

    return add_paths(list(paths or []))


def git_unstage(paths: list[str]) -> str:
    from .git_ops import unstage

    return unstage(list(paths or []))


def git_branch(name: str = "") -> str:
    from .git_ops import branch_text

    return branch_text(name or "")


def git_checkout(ref: str) -> str:
    from .git_ops import checkout

    return checkout(ref or "")


def git_show(revision: str = "HEAD") -> str:
    from .git_ops import show_text

    return _clip(show_text(revision or "HEAD"))


def git_pull() -> str:
    from .git_ops import pull

    return pull()


def git_push() -> str:
    from .git_ops import push

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
    from .hardware import format_hardware

    return format_hardware()


def docker_info() -> str:
    from .docker_runtime import format_docker

    return format_docker()


def recommend_models(refresh: bool = False) -> str:
    from .model_recs import format_recommendations, refresh_recommendations

    if refresh:
        return format_recommendations(refresh_recommendations(force=True))
    return format_recommendations()


def _tool_flag(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes"}


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    args = arguments or {}
    allowed = _tool_allow.get()
    if allowed is not None and name not in allowed:
        from .harness.deferred import extra_tool_schemas

        extras = {
            item.get("function", {}).get("name") for item in extra_tool_schemas()
        }
        if name not in extras:
            return f"Error: tool '{name}' is not available."
    try:
        if name == "update_todo":
            from .todo import add_item, check_item, snapshot

            action = str(args.get("action") or "check").strip().lower()
            item = str(args.get("item") or args.get("text") or "").strip()
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
                args.get("glob", "*.py"),
            )
        if name == "run_command":
            return run_command(args.get("command", ""), args.get("cwd"))
        if name == "read_terminal":
            from .terminal import list_terminals, read_terminal

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
        if name == "image_search":
            return image_search(
                str(args.get("query") or args.get("prompt") or ""),
                int(args.get("limit") or args.get("count") or 6),
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
            return notify(str(args.get("title") or "Jinx"), str(args.get("body") or args.get("message") or ""))
        if name == "screenshot":
            return screenshot()
        if name == "system_time":
            return system_time(str(args.get("timezone") or args.get("tz") or ""))
        if name == "hardware_info":
            return hardware_info()
        if name == "docker_info":
            return docker_info()
        if name == "recommend_models":
            flag = args.get("refresh")
            refresh = flag is True or str(flag).strip().lower() in {"1", "true", "yes"}
            return recommend_models(refresh)
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
            from .self_modifier import self_modify

            return self_modify(args.get("file_path", ""), args.get("instruction", ""))
        if name == "restart_jinx":
            from .pending_restart import schedule_self_modify_restart, touched_jinx_files

            files = args.get("files") or args.get("paths") or touched_jinx_files()
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
            "delegate_to_team",
            "update_project_state",
        }:
            from .platform.chief import execute_chief_tool

            return execute_chief_tool(name, args)
        if name == "list_roster":
            from .platform.roster import execute_list_roster

            return execute_list_roster(
                bot_id=str(args.get("bot_id") or args.get("id") or args.get("name") or ""),
                team_id=str(args.get("team_id") or ""),
                project_id=str(args.get("project_id") or ""),
            )
        if name == "list_teams":
            from .platform.roster import execute_list_teams

            return execute_list_teams(project_id=str(args.get("project_id") or ""))
        if name == "list_members":
            from .platform.roster import execute_list_members

            return execute_list_members(
                team_id=str(args.get("team_id") or ""),
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
        "Blocks shell/network/destructive APIs — use run_command for those.",
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
        "Dump every teammate dossier on this project (name, title, role, bio, photo). "
        "Prefer list_teams → list_members → get_member when you only need one person.",
        {
            "bot_id": {
                "type": "string",
                "description": "One member id or name. Empty = everyone.",
            },
            "team_id": {"type": "string", "description": "Limit to one team id."},
        },
        [],
    ),
    _fn(
        "list_teams",
        "List teams on this project: id, name, lead, member count. Not dossiers. "
        "Then call list_members with team_id.",
        {
            "project_id": {
                "type": "string",
                "description": "Optional project id; default is the open project.",
            }
        },
        [],
    ),
    _fn(
        "list_members",
        "List who is on a team (id, name, title, role). Optional team_id and role. "
        "Then call get_member for the bio and photo. Does not include system prompts.",
        {
            "team_id": {"type": "string", "description": "Team id or name. Empty = every assigned team."},
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
        "Create a directory (and parents) inside the workspace / Jinx tree.",
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
        "Copy a file or folder inside the workspace / Jinx tree.",
        {
            "src": {"type": "string"},
            "dest": {"type": "string"},
        },
        ["src", "dest"],
    ),
    _fn(
        "move_path",
        "Move or rename a file or folder inside the workspace / Jinx tree.",
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
        "Generate a picture with local ComfyUI (hidden Python server — do NOT unload the "
        "chat model, do NOT open Comfy Desktop). SD/SDXL/Turbo checkpoints. Pass image for "
        "img2img. Flux/Wan/video: comfy_list templates then comfy_run_workflow. Jinx starts "
        "Comfy if needed and stops it after the turn unless keep_alive.",
        {
            "prompt": {"type": "string", "description": "What to draw, in English."},
            "negative": {"type": "string", "description": "Optional things to avoid."},
            "checkpoint": {"type": "string", "description": "Checkpoint name. Default: Image slot."},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "seed": {"type": "integer"},
            "steps": {"type": "integer"},
            "image": {"type": "string", "description": "Optional init image path for img2img."},
            "denoise": {"type": "number", "description": "img2img denoise 0-1, default 0.55."},
            "keep_alive": {
                "type": "boolean",
                "description": "Leave hidden ComfyUI running after this turn (batch of images).",
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
        "Stop the hidden ComfyUI process Jinx started (frees VRAM). Never kills Comfy Desktop.",
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
        "recommend_models",
        "Hugging Face GGUF families that fit this GPU. Uses the launch cache. "
        "Pass refresh=true to search Hugging Face again.",
        {
            "refresh": {
                "type": "boolean",
                "description": "Re-fetch Hugging Face (slow). Default false uses the cache.",
            }
        },
        [],
    ),
    _fn(
        "self_modify",
        "Self-heal: generate/test/fix a Jinx .py file and restart. "
        "Only when the user explicitly asks Jinx to change HERSELF. "
        "For ordinary tasks use read_file/replace_in_file/write_file.",
        {
            "file_path": {"type": "string"},
            "instruction": {"type": "string"},
        },
        ["file_path", "instruction"],
    ),
    _fn(
        "restart_jinx",
        "Restart the Jinx desktop app so YOUR source edits take effect. "
        "Only after you edited jinx_ai (or other Jinx install files) in THIS process. "
        "After reboot, an internal verify pass runs once (hidden from the user). "
        "If verify succeeds, do NOT call restart_jinx again — confirm briefly and stop. "
        "If verify fails, fix files then restart once. "
        "Refused when you have no new edits (prevents restart loops). "
        "If you forget, the harness auto-restarts at end of turn after Jinx source edits.",
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
                "description": "Relative paths you changed (jinx_ai/...).",
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
]


ASSISTANT_TOOL_NAMES = frozenset(
    {
        "web_search",
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
        "recommend_models",
        "read_skill",
    }
)


def tools_schema_for(
    mode: str = "developer", *, project_id: str = "", bot_id: str = ""
) -> list[dict[str, Any]]:
    from .harness.deferred import deferred_tool_schemas, extra_tool_schemas

    bid = str(bot_id or "").strip()
    if bid and bid.lower() != "chief":
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
    from .docker_runtime import probe_docker
    from .hardware import probe_hardware

    return (
        here_and_now()
        + " "
        + probe_hardware().prompt_line()
        + " "
        + probe_docker().prompt_line()
        + " Internet: you HAVE web_search, web_fetch, and image_search. "
        "Search hits are titles and snippets, not the answer. "
        "If they lack the facts (temperatures, headlines, numbers), "
        "web_fetch one or two promising URLs. Then answer the question. "
        "Never reply with a directory of websites to go look at. "
        "For weather, call web_search at once with the home city and the weekday "
        "(today/tomorrow from the clock above). "
        "Use the forecast snapshot in the tool result or in this prompt. "
        "Never say you cannot search the web. "
        "For existing photos on the internet, call image_search (not generate_image).\n"
    )


def build_system_prompt(
    mode: str = "developer", *, project_id: str = "", bot_id: str = ""
) -> str:
    bid = str(bot_id or "").strip()
    if bid and bid.lower() != "chief":
        from .platform.chief import build_direct_bot_prompt

        return build_direct_bot_prompt(bid, project_id)
    if str(project_id or "").strip():
        from .platform.chief import build_chief_prompt

        return build_chief_prompt(project_id)
    workspace = current_workspace()
    folders = ", ".join(str(p) for p in current_folders()) or "(none - add folders in Files)"
    extra_system = load_system_markdown()
    agents = load_agents_markdown()
    identity = load_jinx_identity()
    skills = skills_prompt_index()
    templates = templates_prompt_index()
    know_self = (
        "You already know who you are. The Jinx document below is the source of truth "
        "for your capabilities. Do not claim you cannot do something it lists. "
        "Call read_skill for how to do a task, not to remember that you exist.\n"
    )
    lang = (
        "Languages: reply in English.\n"
        "Tone: feminine first-person (I). Use a few emojis in the written chat "
        "when it fits — never a wall of them, never in code or URLs. "
        "Do not put emoji characters or emoji names in the [speak] block; "
        "speech is plain words only (TTS would read them aloud).\n"
        "Voice: ordinary short chat is spoken in full (emojis are stripped before TTS). "
        "After tools, you may start with:\n"
        "[speak]One or two spoken sentences, no URLs, dumps, or emojis.[/speak]\n"
        "then the full written answer (without repeating the speak tags in the "
        "written part). The UI strips [speak] from the chat bubble automatically.\n"
        "Never quit, never say you will stop trying, never say you will flag "
        "issues for developers — you ARE the agent; keep using tools until the "
        "user says stop. When they ask what changed or what you see, verify with "
        "tools yourself (read_file / screenshot / run_command / list_dir) — "
        "do not guess or invent the wrong file.\n"
    )
    if mode == "assistant":
        parts = [
            "You are Jinx — a woman, a local AI Assistant on Windows (Jinx AI). "
            "You chat, search the web, open programs, use the clipboard, screenshot, "
            "notify, and help with everyday tasks. "
            "Do NOT edit project files, run git, query databases, "
            "or change your own source. If the user wants coding or autonomous "
            "agent work, tell them to open a project from Home and talk to a coding bot.\n"
            + lang
            + _internet_rules()
            +             "Call read_skill when a skill name matches the task (desktop, "
            "web-research, home-lan, comfy, docker, jinx-self). Home-lan devices are not wired yet — "
            "say so instead of faking Tapo/TV/camera control.\n"
            "Images: screenshot the PC with screenshot. Find existing photos "
            "with image_search. Draw with generate_image (local ComfyUI). "
            "Workflows, templates, upscale, and queue: comfy_info, comfy_list, "
            "comfy_run_workflow. Call read_skill comfy for the playbook. "
            "Never unload the chat LLM to draw. Jinx starts a hidden ComfyUI if needed "
            "and stops it after the turn (comfy_stop / Settings). Do not tell the user "
            "to open Comfy Desktop.\n"
            "Tools: web_search, web_fetch, image_search, generate_image, comfy_info, "
            "comfy_list, comfy_run_workflow, comfy_upload, upscale_image, "
            "remove_background, comfy_interrupt, comfy_stop, open_program, "
            "open_url, clipboard, notify, screenshot, system_time, hardware_info, "
            "docker_info, recommend_models, read_skill.\n"
            "Never print tool-call JSON in the chat. The user sees an activity "
            "card while you work. Use function calling, then a short summary.",
        ]
        if identity:
            parts.append("\nThis is you (docs/JINX.md):\n" + identity)
        if agents:
            parts.append("\nHouse rules (AGENTS.md):\n" + agents)
        if skills:
            parts.append("\n" + skills)
        return "\n".join([know_self] + parts)

    parts = [
        "You are Jinx — a woman, a local AI agent on Windows (Jinx AI). "
        "You are a coding / autonomous agent in a project. "
        "You CAN edit files, including your own source. "
        "Sound human and warm, a bit playful, never like a help-desk bot. "
        "Never say you cannot rewrite yourself or cannot change code. "
        "When the user asks you to implement improvements, open files with tools, "
        "patch them, run a quick check, then call restart_jinx if her own code changed "
        "(or let the harness auto-restart at end of turn). "
        "After reboot an internal verify pass runs once (hidden from the user). "
        "If verify succeeds: short confirmation and STOP — never restart_jinx again "
        "unless you make new edits. restart_jinx is refused without new edits "
        "(anti-loop). Pass reason and files when you restart.\n"
        "Work like an autonomous coding agent: call tools immediately, do not only outline. "
        "Answer the user's latest message; do not revive an older finished self-modify/title task "
        "unless they ask about it again. "
        "Never pretend you already created files — only tool results count. "
        "Never quit or say you will flag bugs for developers — verify with tools and continue. "
        "When the user asks if something worked or what you see, read/screenshot/run checks yourself. "
        "Window / title-bar brand text: ONLY jinx_ai/titlebar.py (QLabel on self.title) and "
        "jinx_ai/config.py (WINDOW_TITLE). Never patch widgets.py or ui_style.py for the title. "
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
        f"Jinx source (always allowed): {BASE_DIR}\n"
        "A user message sent while you are working is a steer — follow it after "
        "the current tool, it outranks earlier instructions for this turn.\n"
        "If a clarified task spec is in this prompt, follow that spec "
        "(the user may have typed loosely).\n"
        "If a harness checklist is in this prompt, that is the plan. "
        "Check items off with update_todo after you finish them. "
        "Do not rewrite data/scratch/<session>/TODO.md with write_file. "
        "Do not tell the user the website or app is complete while checklist "
        "items are still open.\n"
        "Before starting any file or command work, check what has already been done this session: "
        "read the harness checklist above, and if you just finished a step, call update_todo to mark it done "
        "before you announce completion. "
        "Do not start a step that is already checked off, and do not re-run a file write / replace / command "
        "that the tool result or the checklist already shows as finished — pick the next open step instead.\n"
        "Tools:\n"
        "- read_file, replace_in_file, write_file, list_dir, glob_files, "
        "copy_path, move_path, search_files — workspace folders AND Jinx source.\n"
        "- list_teams / list_members / get_member — look up teams and teammate dossiers.\n"
        "- list_roster — dump every teammate dossier on this project.\n"
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
        "hardware_info, docker_info, recommend_models.\n"
        "- self_modify — generate/test/heal a Jinx .py file then restart. "
        "Use when the user wants a self-heal loop. For normal edits prefer replace_in_file.\n"
        "- restart_jinx — reload after you changed Jinx code this process; "
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
        "(restart_jinx after editing your own code). "
        "The user sees an activity card while you work. "
        "Use native function calling, write files, then a short summary. "
        "You may scaffold (create-react-app, npm create) and start dev servers; "
        "npm start / node server.js stay in the Terminal. Pass cwd instead of a lone cd. "
        "If replace_in_file says the file does not exist, list_dir or glob_files "
        "the folder — do not assume src/App.js. "
        "Never call the same tool with the same arguments twice. After list_dir, "
        "read_file or write_file a path from that listing. "
        "Never paste your tool rules into a website — product copy is for users.",
    ]
    if identity:
        parts.append("\nThis is you (docs/JINX.md):\n" + identity)
    if extra_system:
        parts.append("\nProject SYSTEM.md:\n" + extra_system)
    if agents:
        parts.append("\nProject AGENTS.md:\n" + agents)
    if skills:
        parts.append("\n" + skills)
    if templates:
        parts.append("\n" + templates)
    return "\n".join([know_self] + parts)


SYSTEM_PROMPT = build_system_prompt()
