"""Agent isolation for remote users: each regular user's commands run in a bubblewrap sandbox.

Remote access lets people other than the server's owner instruct the cats, and the cats run shell
commands. Path checks in our own file tools cannot stop a shell (`cat ../../other-user/.env`, an npm
script, `php artisan`), so a regular user's commands run under bubblewrap (``bwrap``, the sandbox
Flatpak uses): no daemon, no image, next to no memory. Inside, the command sees

* the system's programs read-only (``/usr`` and the few ``/etc`` files toolchains need), so the
  server's own PHP, Node, Composer and Python are used;
* its user's workspace (``data/users/<id>/workspace``) and a home for caches
  (``data/users/<id>/home``) read-write, at their real paths;
* a private ``/tmp``, its own process tree, and a cleared environment (no server secrets).

Nothing else of the server exists for it: not the other users' folders, not ``data/`` with the keys
and the accounts, not ``/root`` or ``/home``. Killing the command kills the whole sandbox
(``--die-with-parent`` and a private PID namespace). Admins are trusted with the server and run on
the host, as on the desktop.

* ``status()`` says whether isolation works here (bwrap installed and able to start). Until it
  does, the API refuses invites for regular users and their terminal stays closed.
* A regular user's turn binds its ``Sandbox`` (``bind``); while bound, every command the harness
  starts goes through bwrap (``services/terminal._popen``, ``harness/project_check._run``, the
  project terminal).
* ``guard_tool`` is the before-tool hook for such turns: it refuses tools that act with the admin's
  access (GitHub, SSH, MCP, connectors, databases, the desktop) and URLs into the server's own
  network (cloud metadata, private ranges, loopback), except a loopback port that one of this
  user's own sandboxed processes is listening on: their dev server.

The network is shared with the server by default (installs need the internet). Set
``CATCREW_SANDBOX_NETWORK=none`` for no network at all. Extra read-only folders (a Node installed
under ``/opt``, say): ``CATCREW_SANDBOX_RO_PATHS=/opt/node:/opt/other``.
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
from urllib.parse import urlparse

_STATUS_TTL = 30.0
_status_cache: tuple[float, dict[str, Any]] | None = None
_lock = threading.Lock()
# Host pids of the sandboxes each user has running (for "is this dev server theirs?").
_live: dict[str, set[int]] = {}

_SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32")
_ETC = (
    "/etc/alternatives", "/etc/ssl", "/etc/ca-certificates", "/etc/resolv.conf", "/etc/hosts",
    "/etc/nsswitch.conf", "/etc/host.conf", "/etc/gai.conf", "/etc/localtime", "/etc/mime.types",
    "/etc/php", "/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d",
)
_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

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
        # Writes its script to the server's scratch area, outside the sandbox.
        "run_code_mode",
    }
)
# Tools that fetch a URL from the server's side.
URL_TOOLS = frozenset(
    {"web_fetch", "http_endpoint_audit", "seo_page_audit", "browser_navigate", "browser_check", "browser_mcp_screenshot"}
)


def _bwrap() -> str | None:
    return shutil.which("bwrap")


def _base_args() -> list[str]:
    """What every sandbox gets: system programs read-only, its own /proc, /dev and /tmp."""
    args = [
        "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--unshare-cgroup-try", "--hostname", "catcrew",
    ]
    if os.environ.get("CATCREW_SANDBOX_NETWORK", "").strip().lower() == "none":
        args.append("--unshare-net")
    for path in _SYSTEM_DIRS:
        p = Path(path)
        if p.is_symlink():
            args += ["--symlink", os.readlink(p), path]  # merged-/usr systems: /bin -> usr/bin
        elif p.exists():
            args += ["--ro-bind", path, path]
    for path in (*_ETC, *[x for x in os.environ.get("CATCREW_SANDBOX_RO_PATHS", "").split(":") if x.strip()]):
        args += ["--ro-bind-try", path, path]
    return [*args, "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]


def status(*, refresh: bool = False) -> dict[str, Any]:
    """``{"ready": bool, "detail": str}``: can regular users' agents run isolated here?"""
    global _status_cache
    with _lock:
        if not refresh and _status_cache and time.monotonic() - _status_cache[0] < _STATUS_TTL:
            return dict(_status_cache[1])
    bwrap = _bwrap()
    if bwrap is None:
        result = {"ready": False, "detail": "bubblewrap is not installed: sudo apt install bubblewrap"}
    else:
        try:
            done = subprocess.run([bwrap, *_base_args(), "--", "/bin/sh", "-c", "true"], capture_output=True, text=True, timeout=15)
            ok, out = done.returncode == 0, (done.stderr or done.stdout or "").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok, out = False, str(exc)
        result = (
            {"ready": True, "detail": "bubblewrap works on this server."}
            if ok
            else {
                "ready": False,
                "detail": "bubblewrap cannot start a sandbox here"
                + (f" ({out[:160]})" if out else "")
                + ". On Ubuntu 24.04+ allow it: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0",
            }
        )
    with _lock:
        _status_cache = (time.monotonic(), result)
    return dict(result)


@dataclass(frozen=True)
class Sandbox:
    """One user's sandbox: their workspace and home, nothing else of the server."""

    user_id: str
    root: Path

    @property
    def home(self) -> Path:
        return self.root.parent / "home"

    @property
    def name(self) -> str:
        return f"catcrew-user-{self.user_id}"

    def ensure(self) -> None:
        if _bwrap() is None:
            raise RuntimeError("bubblewrap is not installed on this server (sudo apt install bubblewrap).")
        self.root.mkdir(parents=True, exist_ok=True)
        self.home.mkdir(parents=True, exist_ok=True)

    def _workdir(self, cwd: str | Path | None) -> str:
        if cwd:
            try:
                Path(cwd).resolve().relative_to(self.root.resolve())
                return str(Path(cwd).resolve())
            except ValueError:
                pass  # anything outside the workspace does not exist in the sandbox
        return str(self.root.resolve())

    def exec_argv(self, command: str, cwd: str | Path | None = None, *, interactive: bool = False, tag: str = "") -> list[str]:
        """The bwrap command line that runs ``command`` (a shell line) in ``cwd`` inside the sandbox.

        ``interactive`` and ``tag`` exist for the callers' sake: stdin passes through either way, and
        killing the returned process ends everything inside (private PID namespace)."""
        self.ensure()
        root, home = str(self.root.resolve()), str(self.home.resolve())
        env = {
            "PATH": _PATH, "HOME": home, "TMPDIR": "/tmp", "LANG": "C.UTF-8", "TERM": "xterm-256color",
            "COMPOSER_HOME": f"{home}/.composer", "npm_config_cache": f"{home}/.npm", "npm_config_update_notifier": "false",
        }
        args = [_bwrap() or "bwrap", *_base_args(), "--bind", root, root, "--bind", home, home, "--chdir", self._workdir(cwd), "--clearenv"]
        for key, value in env.items():
            args += ["--setenv", key, value]
        return [*args, "--", "/bin/sh", "-c", command]

    def exec_args(self, args: list[str], cwd: str | Path | None = None) -> list[str]:
        """The same for an argument list (a program found on the host is called by name)."""
        program = Path(args[0]).name if args else ""
        return self.exec_argv(shlex.join([program, *[str(a) for a in args[1:]]]), cwd)

    def translate(self, arg: str) -> str:
        return arg  # the workspace has the same path inside and out

    def kill(self, tag: str) -> None:
        """Nothing to do: killing the bwrap process tears the sandbox down with everything in it."""


def track(box: Sandbox, pid: int) -> None:
    """Remember a running sandbox of ``box``'s user (its dev servers count as theirs)."""
    with _lock:
        _live.setdefault(box.user_id, set()).add(int(pid))


def _alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def _listener_pid(port: int) -> int | None:
    """The host pid listening on loopback ``port`` (Linux), or ``None``."""
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) > 9 and parts[3] == "0A" and int(parts[1].rsplit(":", 1)[1], 16) == port:
                inodes.add(parts[9])
    if not inodes:
        return None
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / "fd").iterdir():
                link = os.readlink(fd)
                if link.startswith("socket:[") and link[8:-1] in inodes:
                    return int(proc.name)
        except OSError:
            continue
    return None


def _ancestors(pid: int) -> list[int]:
    chain: list[int] = []
    while pid > 1 and len(chain) < 64:
        chain.append(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return chain


def owns_port(box: Sandbox, port: int) -> bool:
    """Is a process of ``box``'s user's own sandboxes listening on loopback ``port``?"""
    with _lock:
        mine = {p for p in _live.get(box.user_id, set()) if _alive(p)}
        _live[box.user_id] = mine
    if not mine:
        return False
    listener = _listener_pid(port)
    return listener is not None and any(p in mine for p in _ancestors(listener))


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
    """The URL the server reaches a sandboxed app at: the same one (it shares the host's loopback)."""
    return url


def guard_tool(name: str, arguments: dict[str, Any]) -> str | None:
    """Refuse (with the reason) what a sandboxed turn may not do."""
    box = current()
    if box is None:
        return None
    if name in DENIED_TOOLS:
        return f"Refused: {name} is not available to your account on this server (it acts with the admin's access)."
    if name in URL_TOOLS and arguments.get("url"):
        raw = str(arguments["url"]).strip()
        parsed = urlparse(raw if "://" in raw else "http://" + raw)
        host = (parsed.hostname or "").lower()
        if host in _LOOPBACK_NAMES or (host and _blocked_ip(host)):
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if host in _LOOPBACK_NAMES and owns_port(box, port):
                return None  # the user's own dev server
            return (
                f"Refused: {raw} points into the server's own network. Open public sites, or your own app "
                "at 127.0.0.1:<port> once you started it (php artisan serve / npm run dev) in this project."
            )
    return None
