"""The harness's own health check of a project after a cat changed it: no model, only the project's
real toolchain.

A cat that edits a Blade view and forgets an ``@endif`` leaves the whole site on "syntax error,
unexpected end of file" -- and nothing in the loop noticed: the cat said done, the next cat built
on top of it. This module runs the checks a careful developer runs before saying done, and the
SOP hands any failure straight back to the cat that made it (``platform/sop.py``).

Checks, each only when the project has that stack:

* PHP: ``php -l`` on the project's own PHP files (app, routes, config, database, bootstrap).
* Laravel views: ``php artisan view:cache`` compiles every Blade template, then each compiled file
  is linted; an error is reported against the ``.blade.php`` it came from.
* Assets: ``npm run build`` for a project that has a build script and installed dependencies.
* Pages: ``GET`` of the site (APP_URL) and the given paths; a 5xx comes with the tail of
  ``storage/logs/laravel.log``.
* Python: every ``.py`` file parses (in process, nothing written), then ``ruff`` for syntax errors
  and undefined names (``E9,F63,F7,F82``) when ruff is installed. No style rules.
* TypeScript: the project's ``typecheck`` script, or its own ``tsc --noEmit`` (skipped when the
  build script already runs tsc).
* Go: ``go build ./...``.

``check_project(changed=...)`` runs only the checks the changed files call for (a CSS edit does not
run the Python check); without it everything that applies runs. ``verify_turn`` is the end-of-turn
check of the agent loop (``harness/loop.py``): a turn that changed files is checked before it may
say done, and a failure goes back to the same agent. The pipeline runs its own check with fix runs
(``platform/sop.py``) and turns this one off with ``checks_by_caller()``.

Never raises; a check whose tool is missing is skipped, not failed.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_PHP_DIRS = ("app", "routes", "config", "database", "bootstrap")
_MAX_PHP_FILES = 600
_ENDPATH = re.compile(r"/\*\*PATH (.+?) ENDPATH\*\*/")
_TIMEOUT = 120
_BUILD_TIMEOUT = 300
_SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv", "env", "__pycache__", "build", "dist", "site-packages", ".tox", ".mypy_cache", ".cat-crew-ai", "storage"}
_MAX_PY_FILES = 3000
_WEB_EXTS = {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".css", ".scss", ".sass", ".less", ".html", ".mjs", ".cjs"}
_TS_EXTS = {".ts", ".tsx", ".vue", ".mts", ".cts"}


@dataclass
class CheckResult:
    problems: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def report(self) -> str:
        lines = [f"FAIL {p}" for p in self.problems] + [f"ok   {p}" for p in self.passed]
        head = "Harness project check: " + ("all passed" if self.ok else f"{len(self.problems)} problem(s)")
        return head + "\n" + "\n".join(lines)


def _which(name: str) -> str | None:
    """The program on the host; called by name inside a sandbox (the same programs, read-only)."""
    from backend.services import sandbox

    return name if sandbox.current() is not None else shutil.which(name)


def _run(args: list[str], cwd: Path, timeout: int = _TIMEOUT) -> tuple[int, str]:
    from backend.services import sandbox
    from backend.tools import subprocess_env

    box = sandbox.current()
    if box is not None:  # the project's own code (artisan, npm scripts) never runs on the server
        try:
            args = box.exec_args(args, cwd)
        except RuntimeError as exc:
            return 127, str(exc)
    try:
        done = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=subprocess_env(),
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 127, str(exc)
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def _lint(php: str, path: Path, cwd: Path) -> str:
    """'' when the file parses, else PHP's parse error (one line)."""
    code, out = _run([php, "-l", str(path)], cwd, timeout=30)
    if code == 0:
        return ""
    line = next((ln for ln in out.splitlines() if "error" in ln.lower()), out.splitlines()[0] if out else "parse error")
    return line.strip()


def _php_files(folder: Path) -> list[Path]:
    files: list[Path] = []
    for name in _PHP_DIRS:
        root = folder / name
        if root.is_dir():
            files.extend(p for p in root.rglob("*.php") if "cache" not in p.parts)
    return files[:_MAX_PHP_FILES]


def _check_php(folder: Path, php: str, result: CheckResult) -> None:
    bad = 0
    files = _php_files(folder)
    for path in files:
        error = _lint(php, path, folder)
        if error:
            bad += 1
            result.problems.append(f"PHP syntax in {path.relative_to(folder)}: {error}")
    if files and not bad:
        result.passed.append(f"PHP syntax ({len(files)} files)")


def _check_blade(folder: Path, php: str, result: CheckResult) -> None:
    code, out = _run([php, "artisan", "view:cache"], folder)
    if code != 0:
        result.problems.append(f"php artisan view:cache failed: {out[-800:]}")
        return
    compiled_dir = folder / "storage" / "framework" / "views"
    views_root = (folder / "resources" / "views").resolve()
    checked = 0
    for compiled in compiled_dir.glob("*.php"):
        try:
            tail = compiled.read_text(encoding="utf-8", errors="replace")[-600:]
        except OSError:
            continue
        match = _ENDPATH.search(tail)
        source = Path(match.group(1)) if match else None
        if source is None:
            continue
        try:
            source.resolve().relative_to(views_root)
        except ValueError:
            continue  # a vendor package's view: not this project's to fix
        checked += 1
        error = _lint(php, compiled, folder)
        if error:
            error = error.replace(str(compiled), "the compiled view")
            result.problems.append(
                f"Blade view {source.resolve().relative_to(folder.resolve())}: {error} "
                "(check that every @if/@foreach/@section/@push has its @endif/@endforeach/@endsection/@endpush)"
            )
    if checked and not any(p.startswith("Blade view") for p in result.problems):
        result.passed.append(f"Blade views compile and parse ({checked} views)")


def _check_build(folder: Path, result: CheckResult) -> None:
    try:
        data = json.loads((folder / "package.json").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict) or not scripts.get("build") or not (folder / "node_modules").is_dir():
        return
    npm = _which("npm") or shutil.which("npm.cmd")
    if not npm:
        return
    code, out = _run([npm, "run", "build"], folder, timeout=_BUILD_TIMEOUT)
    if code == 0:
        result.passed.append("npm run build")
    else:
        result.problems.append(f"npm run build failed (exit {code}): {out[-1200:]}")


def app_url(folder: Path) -> str:
    try:
        env = (folder / ".env").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^APP_URL\s*=\s*[\"']?([^\"'\s#]+)", env, re.MULTILINE)
    return match.group(1).rstrip("/") if match else ""


def _log_tail(folder: Path, lines: int = 25) -> str:
    log = folder / "storage" / "logs" / "laravel.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # The newest entry, without its stack frames: the message and where it was thrown.
    entries = [m.start() for m in re.finditer(r"^\[\d{4}-\d\d-\d\d", text, re.MULTILINE)]
    entry = text[entries[-1] if entries else 0 :].splitlines()
    kept = [ln for ln in entry if not ln.lstrip().startswith("#") and ln.strip() not in ("[stacktrace]", '"}')]
    return "\n".join(kept[:lines])[:1500]


def _check_pages(folder: Path, paths: list[str], result: CheckResult) -> None:
    base = app_url(folder)
    if not base:
        return
    from backend.services import sandbox

    base = sandbox.reachable_url(base).rstrip("/")
    import requests

    bad = 0
    for path in dict.fromkeys(["/", *paths]):
        try:
            response = requests.get(base + path, timeout=20, allow_redirects=False)
        except requests.RequestException as exc:
            result.problems.append(f"GET {base}{path}: not reachable ({type(exc).__name__})")
            return
        if response.status_code >= 500:
            bad += 1
            tail = _log_tail(folder)
            result.problems.append(f"GET {path}: HTTP {response.status_code}" + (f"\n--- laravel.log ---\n{tail}" if tail else ""))
    if not bad:
        result.passed.append(f"pages answer without a server error ({base})")


def _walk(folder: Path, suffix: str, limit: int) -> list[Path]:
    found: list[Path] = []
    stack = [folder]
    while stack and len(found) < limit:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                if child.name not in _SKIP_DIRS and not child.name.startswith("."):
                    stack.append(child)
            elif child.suffix == suffix:
                found.append(child)
    return found[:limit]


def _check_python(folder: Path, result: CheckResult, changed: list[Path] | None) -> None:
    files = [p for p in changed if p.suffix == ".py" and p.is_file()] if changed is not None else _walk(folder, ".py", _MAX_PY_FILES)
    if not files:
        return
    bad = 0
    for path in files:
        try:
            ast.parse(path.read_bytes(), filename=str(path))
        except SyntaxError as exc:
            bad += 1
            result.problems.append(f"Python syntax in {_rel(path, folder)}:{exc.lineno}: {exc.msg}")
        except (OSError, ValueError):
            continue
    ruff = _which("ruff") or next((str(p) for p in (folder / ".venv" / "bin" / "ruff", folder / ".venv" / "Scripts" / "ruff.exe") if p.is_file()), "")
    if ruff and not bad:
        targets = [str(p) for p in files] if changed is not None else ["."]
        code, out = _run([ruff, "check", "--select", "E9,F63,F7,F82", "--output-format", "concise", "--no-cache", *targets], folder)
        if code not in (0, 127, 124):
            errors = [ln for ln in out.splitlines() if re.search(r":\d+:\d+: [A-Z]+\d+", ln)]
            prefix = str(folder)
            for line in errors[:25]:
                short = line.replace(prefix, "").lstrip("/\\")
                result.problems.append(f"Python (ruff) {short}")
            bad += len(errors) or 1
    if not bad:
        result.passed.append(f"Python parses ({len(files)} files)" + (" and ruff finds no errors" if ruff else ""))


def _rel(path: Path, folder: Path) -> str:
    try:
        return path.resolve().relative_to(folder.resolve()).as_posix()
    except ValueError:
        return str(path)


def _scripts(folder: Path) -> dict:
    try:
        data = json.loads((folder / "package.json").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    return scripts if isinstance(scripts, dict) else {}


def _check_typescript(folder: Path, result: CheckResult, build_runs: bool) -> None:
    if not (folder / "tsconfig.json").is_file() or not (folder / "node_modules").is_dir():
        return
    scripts = _scripts(folder)
    npm = _which("npm") or shutil.which("npm.cmd")
    if scripts.get("typecheck") and npm:
        args, label = [npm, "run", "typecheck"], "npm run typecheck"
    elif build_runs and "tsc" in str(scripts.get("build") or ""):
        return  # the build already type-checks
    else:
        tsc = next((p for p in (folder / "node_modules" / ".bin" / "tsc", folder / "node_modules" / ".bin" / "tsc.cmd") if p.is_file()), None)
        if tsc is None:
            return
        args, label = [str(tsc), "--noEmit", "-p", "."], "tsc --noEmit"
    code, out = _run(args, folder, timeout=_BUILD_TIMEOUT)
    if code == 0:
        result.passed.append(label)
        return
    errors = [ln.strip() for ln in out.splitlines() if "error TS" in ln]
    if not errors:
        errors = [out[-1200:]]
    result.problems.append(f"{label} failed ({len(errors)} error(s)):\n" + "\n".join(errors[:25]))


def _check_go(folder: Path, result: CheckResult) -> None:
    go = _which("go")
    if not go or not (folder / "go.mod").is_file():
        return
    code, out = _run([go, "build", "./..."], folder, timeout=_BUILD_TIMEOUT)
    if code == 0:
        result.passed.append("go build ./...")
    else:
        result.problems.append(f"go build failed: {out[-1500:]}")


def check_project(folder: str | Path, *, paths: list[str] | None = None, changed: list[str] | None = None) -> CheckResult | None:
    """Run the checks that apply to ``folder``. ``changed`` (paths relative to it, or absolute)
    limits them to what those files call for; ``None`` runs everything that applies. Returns
    ``None`` when nothing applies (no known stack, or nothing relevant changed)."""
    root = Path(folder)
    if not root.is_dir():
        return None
    files = None if changed is None else [Path(c) if Path(c).is_absolute() else root / c for c in changed]
    exts = None if files is None else {p.suffix.lower() for p in files}

    def wants(*kinds: str) -> bool:
        return exts is None or any(k in exts for k in kinds)

    result = CheckResult()
    php = _which("php")
    laravel = (root / "artisan").is_file()
    if php and (laravel or (root / "composer.json").is_file()) and wants(".php"):
        _check_php(root, php, result)
        if laravel:
            _check_blade(root, php, result)
    build_runs = wants(*_WEB_EXTS, ".php")
    if build_runs:
        _check_build(root, result)
    if wants(*_TS_EXTS):
        _check_typescript(root, result, build_runs)
    if wants(".py"):
        _check_python(root, result, files)
    if wants(".go"):
        _check_go(root, result)
    if laravel and wants(".php", *_WEB_EXTS):
        _check_pages(root, list(paths or []), result)
    if not result.problems and not result.passed:
        return None
    return result


# -- the agent loop's end-of-turn check -------------------------------------------------------

_caller_checks: ContextVar[bool] = ContextVar("catcrew_caller_checks", default=False)


@contextmanager
def checks_by_caller() -> Iterator[None]:
    """Inside this block the loop does not check at the end of a turn: the caller does."""
    token = _caller_checks.set(True)
    try:
        yield
    finally:
        _caller_checks.reset(token)


def verify_turn(workspace: str | Path, changed: list[str]) -> str | None:
    """The nudge for an agent whose turn broke the project, or ``None`` when it is fine (or there
    is nothing to check)."""
    if _caller_checks.get() or not changed:
        return None
    try:
        result = check_project(workspace, changed=changed)
    except Exception as exc:  # noqa: BLE001 - a broken check must never break the turn
        print(f"[check] skipped: {exc}", flush=True)
        return None
    if result is None or result.ok:
        return None
    return (
        "Before you finish: the harness checked the project after your changes and found "
        "problems. Fix them now, then check again yourself (run the same command). If a problem is "
        "in code you did not touch and was already there before this task, do not rewrite it: say "
        "so in your answer.\n\n" + result.report()
    )
