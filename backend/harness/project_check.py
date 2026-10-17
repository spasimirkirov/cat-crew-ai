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

Never raises; a check whose tool is missing is skipped, not failed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_PHP_DIRS = ("app", "routes", "config", "database", "bootstrap")
_MAX_PHP_FILES = 600
_ENDPATH = re.compile(r"/\*\*PATH (.+?) ENDPATH\*\*/")
_TIMEOUT = 120
_BUILD_TIMEOUT = 300


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


def _run(args: list[str], cwd: Path, timeout: int = _TIMEOUT) -> tuple[int, str]:
    from backend.tools import subprocess_env

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
    npm = shutil.which("npm") or shutil.which("npm.cmd")
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


def check_project(folder: str | Path, *, paths: list[str] | None = None) -> CheckResult | None:
    """Run every check that applies to ``folder``. ``None`` when nothing applies (no known stack)."""
    root = Path(folder)
    if not root.is_dir():
        return None
    result = CheckResult()
    php = shutil.which("php")
    laravel = (root / "artisan").is_file()
    if php and (laravel or (root / "composer.json").is_file()):
        _check_php(root, php, result)
        if laravel:
            _check_blade(root, php, result)
    _check_build(root, result)
    if laravel:
        _check_pages(root, list(paths or []), result)
    if not result.problems and not result.passed:
        return None
    return result
