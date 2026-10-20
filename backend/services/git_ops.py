"""Git operations for the current workspace (git CLI)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from backend.services.workspace import current_workspace

GIT_TIMEOUT = 45


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    from backend.services.workspace import is_confined

    if is_confined():
        # A regular user's repository on a server: its hooks, fsmonitor and ext:: remotes would run
        # their commands on the server itself, outside the user's sandbox.
        for i, (key, value) in enumerate((("core.hooksPath", "/dev/null"), ("core.fsmonitor", "false"), ("protocol.ext.allow", "never"))):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
        env["GIT_CONFIG_COUNT"] = "3"
    return env


def _dot_git_root(start: Path) -> Path | None:
    """Walk parents for .git — no subprocess, so the UI can stay responsive."""
    try:
        folder = start.resolve()
    except OSError:
        return None
    for _ in range(24):
        try:
            if (folder / ".git").exists():
                return folder
        except OSError:
            return None
        parent = folder.parent
        if parent == folder:
            break
        folder = parent
    return None


def git_root(cwd: Path | None = None) -> Path | None:
    if cwd is not None:
        candidates = [Path(cwd).resolve()]
    else:
        from backend.services.workspace import current_folders

        candidates = [folder.resolve() for folder in current_folders()]
    seen: set[Path] = set()
    for folder in candidates:
        if folder in seen or not folder.is_dir():
            continue
        seen.add(folder)
        found = _dot_git_root(folder)
        if found is not None:
            return found
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(folder),
                capture_output=True,
                text=True,
                timeout=4,
                encoding="utf-8",
                errors="replace",
                env=_git_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        root = (completed.stdout or "").strip()
        if root:
            return Path(root)
    return None


def run_git(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    folder = (cwd or git_root() or current_workspace()).resolve()
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(folder),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
        )
    except FileNotFoundError:
        return 127, "Error: git was not found on PATH."
    except subprocess.TimeoutExpired:
        return 124, f"Error: git timed out after {GIT_TIMEOUT}s."
    except OSError as exc:
        return 1, f"Error running git: {exc}"
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode == 0:
        return 0, stdout or "(no output)"
    return completed.returncode, stderr or stdout or "(no output)"


def has_commits(cwd: Path | None = None) -> bool:
    root = git_root(cwd)
    if root is None:
        return False
    code, out = run_git(["rev-list", "-n", "1", "--all"], root)
    return code == 0 and bool(out) and out not in {"(no output)"} and not out.startswith("fatal")


def status_text(cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return f"Not a git repository: {cwd or current_workspace()}"
    info = parse_status(cwd)
    if not info["ok"]:
        return info["message"]
    lines = [f"repo: {root}", f"branch: {info['branch']}"]
    files = info["files"]
    if not files:
        lines.append("clean working tree")
    else:
        lines.extend(f"{item['xy']} {item['path']}" for item in files)
    if not info.get("has_commits"):
        lines.append("(repository has no commits yet)")
    return "\n".join(lines)


def parse_status(cwd: Path | None = None) -> dict:
    root = git_root(cwd)
    if root is None:
        return {
            "ok": False,
            "root": None,
            "branch": "",
            "files": [],
            "has_commits": False,
            "message": f"Not a git repository: {cwd or current_workspace()}",
        }
    _code, porcelain = run_git(["status", "--porcelain=v1", "-b", "-uall"], root)
    if porcelain.startswith("Error:"):
        return {
            "ok": False,
            "root": root,
            "branch": "",
            "files": [],
            "has_commits": False,
            "message": porcelain,
        }
    branch = ""
    committed = True
    files: list[dict[str, str]] = []
    if porcelain not in ("(no output)",):
        for line in porcelain.splitlines():
            if line.startswith("fatal:"):
                continue
            if line.startswith("##"):
                rest = line[2:].strip()
                if rest.lower().startswith("no commits yet"):
                    committed = False
                    parts = rest.split(" on ", 1)
                    branch = parts[-1].strip() if len(parts) == 2 else "(no commits yet)"
                else:
                    branch = rest.split("...")[0].strip()
                continue
            if len(line) < 4:
                continue
            xy, path = line[:2], line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[-1]
            files.append({"xy": xy, "path": path.strip(), "staged": xy[0] not in (" ", "?")})
    return {
        "ok": True,
        "root": root,
        "branch": branch or "(no commits yet)",
        "files": files,
        "has_commits": committed,
        "message": "",
    }


def split_status_files(files: list[dict]) -> tuple[list[dict], list[dict]]:
    """VS Code lists: staged (index) vs changes (worktree / untracked)."""
    staged: list[dict] = []
    changes: list[dict] = []
    for item in files:
        xy = str(item.get("xy") or "  ")
        if len(xy) < 2:
            xy = (xy + "  ")[:2]
        row = dict(item)
        row["xy"] = xy
        if xy[0] not in (" ", "?"):
            staged.append({**row, "letter": status_letter(xy, staged_list=True)})
        if xy[1] != " " or xy == "??":
            changes.append({**row, "letter": status_letter(xy, staged_list=False)})
    return staged, changes


def status_letter(xy: str, *, staged_list: bool) -> str:
    code = (xy or "  ")[:2]
    if code == "??":
        return "U"
    ch = code[0] if staged_list else (code[1] if code[1] != " " else code[0])
    return {"M": "M", "A": "A", "D": "D", "R": "R", "C": "C", "U": "U", "?": "U"}.get(ch, ch or "M")


def diff_text(path: str | None = None, staged: bool = False, cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    if not has_commits(root) and staged:
        return "(no commits yet — nothing staged against HEAD)"
    args = ["diff"]
    if staged:
        args.append("--cached")
    if path:
        args.extend(["--", path])
    _code, out = run_git(args, root)
    return out


def log_text(limit: int = 20, cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    if not has_commits(root):
        return "No commits yet."
    _code, out = run_git(
        ["log", f"-n{max(1, min(int(limit), 50))}", "--oneline", "--decorate"],
        root,
    )
    return out


def stage_paths(paths: list[str], cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    if not paths:
        return "Error: no paths to stage."
    code, out = run_git(["add", "--", *paths], root)
    return out if code != 0 else f"Staged {len(paths)} path(s)."


def unstage_paths(paths: list[str], cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    if not paths:
        return "Error: no paths to unstage."
    code, out = run_git(["restore", "--staged", "--", *paths], root)
    return out if code != 0 else f"Unstaged {len(paths)} path(s)."


def commit(message: str, cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    msg = (message or "").strip()
    if not msg:
        return "Error: empty commit message."
    code, out = run_git(["commit", "-m", msg], root)
    return out if out != "(no output)" else ("Committed." if code == 0 else "Commit failed.")


def pull(cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    _code, out = run_git(["pull", "--ff-only"], root)
    return out


def push(cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    _code, out = run_git(["push"], root)
    return out


def add_paths(paths: list[str], cwd: Path | None = None) -> str:
    return stage_paths(paths, cwd)


def unstage(paths: list[str], cwd: Path | None = None) -> str:
    return unstage_paths(paths, cwd)


def branch_text(name: str = "", cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    label = (name or "").strip()
    if not label:
        _code, out = run_git(["branch", "-a", "--list"], root)
        return out
    if any(ch in label for ch in (" ", "..", "\\", "\n", "\r")):
        return "Error: invalid branch name."
    code, out = run_git(["branch", label], root)
    return out if code != 0 else f"Created branch {label}."


def checkout(ref: str, cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    target = (ref or "").strip()
    if not target:
        return "Error: missing branch or ref."
    if target.startswith("-") or any(ch in target for ch in ("\n", "\r", " ")):
        return "Error: invalid branch or ref."
    code, out = run_git(["checkout", target], root)
    return out if out != "(no output)" else (f"Checked out {target}." if code == 0 else "Checkout failed.")


def show_text(revision: str = "HEAD", cwd: Path | None = None) -> str:
    root = git_root(cwd)
    if root is None:
        return "Not a git repository."
    if not has_commits(root):
        return "No commits yet."
    rev = (revision or "HEAD").strip() or "HEAD"
    if rev.startswith("-") or any(ch in rev for ch in ("\n", "\r", " ")):
        return "Error: invalid revision."
    _code, out = run_git(["show", "--stat", "--format=medium", rev], root)
    return out
