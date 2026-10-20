"""Agent isolation for remote users: each regular user's commands run in their own container.

Remote access lets people other than the server's owner instruct the cats, and the cats run shell
commands. A regular user's work therefore runs in a Docker container that sees only that user's
workspace (``data/users/<id>/workspace`` mounted at ``/workspace``), with memory, CPU and process
limits, no Linux capabilities and no privilege escalation. Admins are trusted with the server and
run on the host, as on the desktop.

* ``status()`` says whether isolation is ready (Docker running, the image built). Until it is, the
  API refuses invites for regular users and their terminal stays closed (``api/routes/auth.py``).
* A regular user's turn binds its ``Sandbox`` (``bind``); while bound, every command the harness
  starts goes through ``docker exec`` (``services/terminal._popen``, ``harness/project_check._run``,
  the project terminal), with host paths under the workspace translated to ``/workspace/...``.
* ``guard_tool`` is the before-tool hook for such turns: it refuses the tools that act with the
  admin's credentials or on the server itself (GitHub, SSH, MCP, connectors, the desktop), and
  URLs that point into the server's own network (cloud metadata, loopback, private ranges) other
  than the user's container, which a loopback URL is pointed at instead (a dev server started in
  the container is reached there; it must listen on 0.0.0.0).

Configuration (environment): ``CATCREW_SANDBOX_IMAGE`` (default ``catcrew-sandbox:latest``, built
from ``docker/sandbox/Dockerfile``), ``CATCREW_SANDBOX_NETWORK`` (``bridge``), and the limits
``CATCREW_SANDBOX_MEMORY`` (``2g``), ``CATCREW_SANDBOX_CPUS`` (``2``), ``CATCREW_SANDBOX_PIDS`` (``512``).
"""

from __future__ import annotations

import ipaddress
import os
import shlex
import shutil
import socket
import subprocess
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

CONTAINER_ROOT = "/workspace"
_STATUS_TTL = 30.0
_status_cache: tuple[float, dict[str, Any]] | None = None
_lock = threading.Lock()

# Tools a sandboxed turn may not use: they act with the admin's credentials (GitHub, SSH keys, MCP
# servers, connectors, database connections) or on the server's own desktop.
DENIED_TOOLS = frozenset(
    {
        "github_status", "github_push", "github_create_branch", "github_create_pull_request",
        "github_merge_pull_request", "github_create_release", "github_actions_runs", "github_run_workflow",
        "ssh_command", "vps_health_check", "docker_container_status", "docker_info",
        "mcp_call_tool", "mcp_find_tools", "load_connector_tools", "list_connectors",
        "open_program", "open_url", "open_in_vscode", "clipboard", "screenshot", "notify",
        "self_modify", "restart_catcrew", "db_query", "db_execute", "db_tables", "db_schema",
        # Writes its script to the server's scratch area, which the container cannot see.
        "run_code_mode",
    }
)
# Tools that fetch a URL from the server's side: checked (and loopback pointed at the container).
URL_TOOLS = frozenset(
    {"web_fetch", "http_endpoint_audit", "seo_page_audit", "browser_navigate", "browser_check", "browser_mcp_screenshot"}
)


def image() -> str:
    return os.environ.get("CATCREW_SANDBOX_IMAGE", "catcrew-sandbox:latest")


def _docker() -> str | None:
    return shutil.which("docker")


def _run(args: list[str], timeout: float = 20.0) -> tuple[int, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def status(*, refresh: bool = False) -> dict[str, Any]:
    """``{"ready": bool, "detail": str}``: can regular users' agents run isolated?"""
    global _status_cache
    with _lock:
        if not refresh and _status_cache and time.monotonic() - _status_cache[0] < _STATUS_TTL:
            return dict(_status_cache[1])
    docker = _docker()
    if docker is None:
        result = {"ready": False, "detail": "Docker is not installed on this server."}
    else:
        code, out = _run([docker, "info", "--format", "{{.ServerVersion}}"])
        if code != 0:
            result = {"ready": False, "detail": f"the Docker engine is not running ({out[:120]})."}
        else:
            code, _ = _run([docker, "image", "inspect", image()])
            result = (
                {"ready": True, "detail": f"Docker {out.strip()}, image {image()}."}
                if code == 0
                else {"ready": False, "detail": f"build the sandbox image first: docker build -t {image()} docker/sandbox"}
            )
    with _lock:
        _status_cache = (time.monotonic(), result)
    return dict(result)


@dataclass(frozen=True)
class Sandbox:
    """One user's container: their workspace at ``/workspace``, nothing else of the server."""

    user_id: str
    root: Path

    @property
    def name(self) -> str:
        return f"catcrew-user-{self.user_id}".replace("_", "-").lower()

    def container_path(self, host_path: str | Path) -> str:
        try:
            rel = Path(host_path).resolve().relative_to(self.root.resolve())
        except ValueError:
            return CONTAINER_ROOT  # anything outside the workspace is not in the container
        return f"{CONTAINER_ROOT}/{rel.as_posix()}".rstrip("/") if rel.parts else CONTAINER_ROOT

    def translate(self, arg: str) -> str:
        """A host path inside the workspace becomes its container path; anything else stays."""
        root = str(self.root.resolve())
        return CONTAINER_ROOT + arg[len(root):].replace("\\", "/") if arg.startswith(root) else arg

    def ensure(self) -> None:
        """Start the container if it is not running (idempotent)."""
        docker = _docker()
        if docker is None:
            raise RuntimeError("Docker is not installed on this server.")
        code, out = _run([docker, "inspect", "-f", "{{.State.Running}}", self.name])
        if code == 0 and out.strip() == "true":
            return
        if code == 0:
            _run([docker, "rm", "-f", self.name])
        self.root.mkdir(parents=True, exist_ok=True)
        args = [
            docker, "run", "-d", "--name", self.name, "--restart", "unless-stopped",
            "--memory", os.environ.get("CATCREW_SANDBOX_MEMORY", "2g"),
            "--cpus", os.environ.get("CATCREW_SANDBOX_CPUS", "2"),
            "--pids-limit", os.environ.get("CATCREW_SANDBOX_PIDS", "512"),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--network", os.environ.get("CATCREW_SANDBOX_NETWORK", "bridge"),
            "--label", "catcrew.sandbox=1", "--label", f"catcrew.user={self.user_id}",
            "-v", f"{self.root.resolve()}:{CONTAINER_ROOT}", "-w", CONTAINER_ROOT,
        ]
        if hasattr(os, "getuid"):  # files the commands write stay owned by the server's user
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        code, out = _run([*args, image(), "sleep", "infinity"], timeout=120)
        if code != 0:
            raise RuntimeError(f"could not start the sandbox container: {out[-300:]}")

    def exec_argv(self, command: str, cwd: str | Path | None = None, *, interactive: bool = False, tag: str = "") -> list[str]:
        """``docker exec`` for a shell command line, in the container path of ``cwd``.

        With ``tag`` the command records its process id, so ``kill(tag)`` can stop it and everything
        it started: killing the ``docker exec`` client alone leaves the process running in the
        container. (A ``docker exec`` process leads its own process group.)"""
        self.ensure()
        workdir = self.container_path(cwd) if cwd else CONTAINER_ROOT
        head = [_docker() or "docker", "exec", *(["-i"] if interactive else []), "-w", workdir, self.name]
        if tag:
            return [*head, "sh", "-lc", f'echo $$ > /tmp/catcrew-{tag}.pid; exec sh -c "$1"', "catcrew", command]
        return [*head, "sh", "-lc", command]

    def kill(self, tag: str) -> None:
        """Stop a tagged command and its whole process group inside the container."""
        _run([_docker() or "docker", "exec", self.name, "sh", "-c", f"p=$(cat /tmp/catcrew-{tag}.pid 2>/dev/null) && kill -TERM -- -$p; rm -f /tmp/catcrew-{tag}.pid"], timeout=10)

    def exec_args(self, args: list[str], cwd: str | Path | None = None) -> list[str]:
        """``docker exec`` for an argument list (a program found on the host is called by name)."""
        program = Path(args[0]).name if args else ""
        line = shlex.join([program, *[self.translate(str(a)) for a in args[1:]]])
        return self.exec_argv(line, cwd)

    def ip(self) -> str:
        code, out = _run([_docker() or "docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", self.name])
        return out.split()[0] if code == 0 and out.split() else ""


_current: ContextVar[Sandbox | None] = ContextVar("catcrew_turn_sandbox", default=None)


def bind(box: Sandbox | None):
    return _current.set(box)


def reset(token) -> None:
    try:
        _current.reset(token)
    except (ValueError, LookupError):
        _current.set(None)


def current() -> Sandbox | None:
    return _current.get()


def for_user(user_id: str) -> Sandbox:
    from backend.api.access import USERS_DIR

    return Sandbox(user_id=user_id, root=(USERS_DIR / user_id / "workspace"))


# -- the before-tool guard for sandboxed turns ------------------------------------------------

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _blocked_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # unresolvable: the fetch itself will fail
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
            return True
    return False


def reachable_url(url: str, box: Sandbox | None = None) -> str:
    """``url`` with a loopback host pointed at the user's container (where their dev server runs)."""
    box = box or current()
    parsed = urlparse(url)
    if box is None or (parsed.hostname or "").lower() not in _LOOPBACK_NAMES:
        return url
    ip = box.ip()
    if not ip:
        return url
    netloc = ip + (f":{parsed.port}" if parsed.port else "")
    return urlunparse(parsed._replace(netloc=netloc))


def guard_tool(name: str, arguments: dict[str, Any]) -> str | None:
    """Refuse (with the reason) what a sandboxed turn may not do; may rewrite a loopback URL."""
    box = current()
    if box is None:
        return None
    if name in DENIED_TOOLS:
        return f"Refused: {name} is not available to your account on this server (it acts with the admin's access)."
    if name in URL_TOOLS and arguments.get("url"):
        raw = str(arguments["url"]).strip()
        target = raw if "://" in raw else "http://" + raw
        rewritten = reachable_url(target, box)
        host = (urlparse(rewritten).hostname or "").lower()
        own = box.ip()
        if host != own and (host in _LOOPBACK_NAMES or _blocked_ip(host)):
            return f"Refused: {raw} points into the server's own network. Open public sites, or your app at 127.0.0.1:<port> (it runs in your container; start it with --host 0.0.0.0)."
        arguments["url"] = rewritten
    return None
