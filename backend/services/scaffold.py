"""``scaffold_project``: run a framework's official generator into a folder that already has files.

Generators refuse a non-empty folder (``composer create-project laravel/laravel .`` stops with
"Project directory is not empty"), and in a CatCrewAI project the folder is never empty by the
time DevOps runs: the blueprint and architecture phases have written their documents. A strong
model works around that on its own; a local one wrote composer.json by hand instead (no artisan,
no config/, no routes/ -- not a Laravel project), or tried to delete the folder.

So the harness does the dance: the generator runs into a fresh hidden subfolder, then its output is
merged into the project. The generator owns the manifests, lock files and dependency folders
(composer.json, package.json, vendor/, node_modules/...): its versions replace hand-written ones.
Every other file already in the project is kept, and the generator's copy of it is dropped and
listed, so nothing the crew wrote is lost.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

# Top-level names the generator owns: a hand-written copy is replaced by the generator's.
_OWNED_FILES = frozenset(
    {"composer.json", "composer.lock", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"}
)
_OWNED_DIRS = frozenset({"vendor", "node_modules"})
_TRAILING_DOT = re.compile(r"""\s+(?:\.|"\."|'\.'|\./)\s*(?=(?:\s+-|\s*$))""")
_EXIT = re.compile(r"^exit (-?\d+) @ ", re.M)
_MIN_TIMEOUT = 900  # downloading a framework and its dependencies takes minutes


def _with_dir_placeholder(command: str) -> str | None:
    """``command`` with its target folder as ``{dir}``. ``laravel/laravel .`` becomes
    ``laravel/laravel {dir}`` -- the form models naturally write."""
    if "{dir}" in command:
        return command
    replaced, count = _TRAILING_DOT.subn(" {dir}", command, count=1)
    return replaced if count else None


def _merge(src: Path, dst: Path, rel: str, top: bool, report: dict[str, list[str]]) -> None:
    for item in sorted(src.iterdir(), key=lambda p: p.name):
        target = dst / item.name
        name = f"{rel}{item.name}"
        if not target.exists():
            shutil.move(str(item), str(target))
            report["added"].append(name + ("/" if target.is_dir() else ""))
        elif top and item.is_dir() and item.name in _OWNED_DIRS:
            shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(item), str(target))
            report["replaced"].append(name + "/")
        elif item.is_dir() and target.is_dir():
            _merge(item, target, name + "/", False, report)
        elif top and item.is_file() and item.name in _OWNED_FILES:
            target.unlink()
            shutil.move(str(item), str(target))
            report["replaced"].append(name)
        else:
            report["kept"].append(name)


def scaffold_project(command: str, cwd: str | None = None) -> str:
    from backend.services.settings_store import agent_settings
    from backend.services.terminal import run_foreground
    from backend.tools import _clip, _command_env, blocked_command_reason, current_workspace, resolve_user_path

    raw = (command or "").strip()
    if not raw:
        return "Error: scaffold_project needs the generator command, e.g. composer create-project laravel/laravel {dir} --no-interaction"
    blocked = blocked_command_reason(raw)
    if blocked:
        return f"Refused: this command {blocked}, so agents may not run it: {raw}"
    templated = _with_dir_placeholder(raw)
    if templated is None:
        return (
            "Error: write the folder the generator creates as {dir}, e.g. "
            "`composer create-project laravel/laravel {dir} --prefer-dist --no-interaction` or "
            "`npm create vite@latest {dir} -- --template react`. The harness points {dir} at a fresh "
            "folder and merges the result into the project."
        )
    try:
        project = resolve_user_path(cwd) if cwd else current_workspace()
    except ValueError as exc:
        return f"Error: {exc}"
    if not project.is_dir():
        return f"Error: not a folder: {project}"

    staging = project / f".scaffold-{uuid.uuid4().hex[:8]}"
    rendered = templated.replace("{dir}", f'"{staging}"')
    env = _command_env()
    env["COMPOSER_NO_INTERACTION"] = "1"
    timeout = max(int(agent_settings().get("command_timeout") or 0), _MIN_TIMEOUT)
    try:
        output = run_foreground(rendered, project, timeout=timeout, env=env) or ""
        exit_match = _EXIT.search(output)
        exit_code = int(exit_match.group(1)) if exit_match else None
        if not staging.is_dir() or not any(staging.iterdir()):
            return _clip(f"Error: the generator created nothing.\nCommand: {rendered}\n{output}")
        report: dict[str, list[str]] = {"added": [], "replaced": [], "kept": []}
        _merge(staging, project, "", True, report)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    lines = [
        f"Scaffolded {project} with: {raw}",
        f"Added {len(report['added'])} entries: {', '.join(report['added'][:25])}"
        + (" …" if len(report["added"]) > 25 else ""),
    ]
    if report["replaced"]:
        lines.append(f"Replaced with the generator's version: {', '.join(report['replaced'])}")
    if report["kept"]:
        lines.append(
            f"Kept the project's existing files (the generator's copies were dropped): {', '.join(report['kept'][:25])}"
        )
    if exit_code:
        lines.append(f"Note: the generator exited {exit_code}; read its output below before going on.")
    lines.append("Next: check the result with the framework's own command (e.g. php artisan --version, npm run build).")
    tail = output.strip()[-1500:]
    return _clip("\n".join(lines) + ("\n--- generator output (end) ---\n" + tail if tail else ""))
