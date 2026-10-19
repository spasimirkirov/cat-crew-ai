"""Forge (devops) role-specific tools."""

from __future__ import annotations

import re
import shutil
import subprocess
import time


def execute_terminal_cmd(command: str, cwd: str = "") -> str:
    """Run a shell command in the workspace (or ``cwd``) and return its output.

    Thin wrapper over the sandboxed ``run_command`` in ``backend/tools.py``
    - reuses its destructive-command blocklist, timeout handling and
    background-process detection rather than re-implementing them.
    Registered separately here so it can be assigned/unassigned per bot
    through the Tools matrix.

    Args:
        command: Shell command to run.
        cwd: Optional working directory, relative to the workspace.

    Returns:
        Combined stdout/stderr (clipped), or an ``Error:``/refusal message.
    """
    from .. import tools as _tools

    return _tools.run_command(command, cwd or None)


def docker_container_status(name: str = "") -> str:
    """Report running Docker containers, optionally filtered by name.

    Args:
        name: Optional substring to filter container names by. Empty lists all.

    Returns:
        A table of container name/status/ports, or a message explaining
        why Docker is unavailable.
    """
    if shutil.which("docker") is None:
        return "Docker is not installed (or not on PATH) in this environment."
    cmd = ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Image}}"]
    if name:
        cmd += ["--filter", f"name={name}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return "docker ps timed out - is the Docker daemon running?"
    except Exception as exc:
        return f"Error running docker ps: {exc}"
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        if "Cannot connect" in err or "daemon" in err.lower():
            return f"Docker daemon is not reachable: {err[:400]}"
        return f"docker ps failed: {err[:400] or '(no error output)'}"
    lines = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return "No containers found" + (f" matching {name!r}." if name else ".")
    header = f"{'NAME':<24}{'STATUS':<28}{'PORTS':<28}IMAGE"
    rows = [header]
    for ln in lines:
        parts = ln.split("\t")
        while len(parts) < 4:
            parts.append("")
        cname, status, ports, image = parts[:4]
        rows.append(f"{cname:<24}{status:<28}{ports:<28}{image}")
    return "\n".join(rows)


def vps_health_check(host: str = "") -> str:
    """Check basic health of a host: for a remote ``host``, TCP-connect
    latency to common ports; always also reports local disk, memory and
    load-average for the machine CatCrewAI is running on.

    This is a lightweight reachability/resource check, not a full
    monitoring solution - it never modifies anything and never shells out
    to the remote host.

    Args:
        host: Optional hostname or IP to check reachability for (e.g. a VPS).

    Returns:
        A short health report.
    """
    import socket

    lines: list[str] = []
    if host:
        lines.append(f"Reachability check for {host}:")
        for port, label in ((22, "ssh"), (80, "http"), (443, "https")):
            start = time.monotonic()
            try:
                with socket.create_connection((host, port), timeout=4):
                    ms = (time.monotonic() - start) * 1000
                    lines.append(f"  port {port} ({label}): open, {ms:.0f}ms")
            except OSError as exc:
                lines.append(f"  port {port} ({label}): closed/unreachable ({exc})")
        lines.append("")
    lines.append("Local machine resources (where CatCrewAI is running):")
    try:
        total, used, free = shutil.disk_usage(".")
        pct = used / total * 100 if total else 0.0
        lines.append(
            f"  disk: {used // (1024**3)}GB used / {total // (1024**3)}GB total ({pct:.1f}% used)"
        )
    except OSError as exc:
        lines.append(f"  disk: could not read ({exc})")
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        lines.append(
            f"  memory: {vm.used // (1024**2)}MB used / {vm.total // (1024**2)}MB total ({vm.percent}% used)"
        )
        lines.append(f"  cpu: {psutil.cpu_percent(interval=0.3)}%")
    except ImportError:
        try:
            load1, load5, load15 = __import__("os").getloadavg()
            lines.append(f"  load average: {load1:.2f} {load5:.2f} {load15:.2f} (1/5/15 min)")
        except (AttributeError, OSError):
            lines.append("  memory/cpu: psutil not installed and no load-average available on this OS.")
    return "\n".join(lines)


_SSH_TARGET = re.compile(r"[A-Za-z0-9_.-]+")


def ssh_command(host: str, command: str, user: str = "", port: int = 22, cwd: str = "", timeout: int = 300) -> str:
    """Run one command on a remote server over SSH (deploys, migrations, restarts, logs).

    Uses the SSH keys of the machine CatCrewAI runs on (``~/.ssh``) in batch mode: it never asks
    for a password, so a server without key access answers "Permission denied" instead of hanging.
    A host seen for the first time is trusted and remembered (``accept-new``). Commands that are
    refused locally (``rm -rf /``, disk formatting...) are refused here too.

    Args:
        host: Server name or IP, or an alias from ``~/.ssh/config``.
        command: The remote shell command, e.g. ``git pull && php artisan migrate --force``.
        user: Login user. Empty uses ``~/.ssh/config`` or the local user name.
        port: SSH port.
        cwd: Remote folder to run the command in, e.g. ``/var/www/site``.
        timeout: Seconds before the command is stopped (10-1800).

    Returns:
        The exit code and combined output (clipped), or an ``Error:``/refusal message.
    """
    import shlex

    from .. import tools as _tools

    host = (host or "").strip()
    user = (user or "").strip()
    cmd = (command or "").strip()
    if not host or not _SSH_TARGET.fullmatch(host) or host.startswith("-"):
        return f"Error: invalid host {host!r}."
    if user and (not _SSH_TARGET.fullmatch(user) or user.startswith("-")):
        return f"Error: invalid user {user!r}."
    if not cmd:
        return "Error: empty command."
    blocked = _tools.blocked_command_reason(cmd)
    if blocked:
        return f"Refused: this command {blocked}, so agents may not run it: {cmd}"
    if shutil.which("ssh") is None:
        return "Error: ssh is not installed on this machine."
    remote = f"cd {shlex.quote(cwd.strip())} && {cmd}" if (cwd or "").strip() else cmd
    args = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
        "-p", str(int(port or 22)), f"{user}@{host}" if user else host, remote,
    ]
    limit = max(10, min(int(timeout or 300), 1800))
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=limit, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"Error: the remote command did not finish within {limit}s: {cmd}"
    except OSError as exc:
        return f"Error: could not start ssh: {exc}"
    out = ((done.stdout or "") + ("\n" + done.stderr if done.stderr else "")).strip()
    if len(out) > 12000:
        out = out[:4000] + "\n... [clipped] ...\n" + out[-7000:]
    return f"exit {done.returncode} on {host}\n{out or '(no output)'}"
