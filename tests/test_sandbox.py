"""The per-user sandbox (bubblewrap): a regular user's commands, checks and terminal see only their
own workspace. The ``live`` tests run real bwrap when the machine has it."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from backend.services import sandbox

live = pytest.mark.skipif(
    shutil.which("bwrap") is None or not sandbox.status(refresh=True)["ready"], reason="bubblewrap cannot run here"
)


@pytest.fixture
def box(tmp_path) -> sandbox.Sandbox:
    root = tmp_path / "users" / "u_bob" / "workspace"
    (root / "shop").mkdir(parents=True)
    return sandbox.Sandbox(user_id="u_bob", root=root)


def test_status_says_what_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(sandbox, "_status_cache", None)
    monkeypatch.setattr(sandbox, "_bwrap", lambda: None)
    assert sandbox.status(refresh=True) == {"ready": False, "detail": "bubblewrap is not installed: sudo apt install bubblewrap"}
    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "setting up uid map: Permission denied"))
    detail = sandbox.status(refresh=True)["detail"]
    assert "cannot start a sandbox" in detail and "Permission denied" in detail and "apparmor_restrict_unprivileged_userns" in detail


def test_the_sandbox_binds_only_the_workspace_and_home_and_clears_the_environment(monkeypatch, box) -> None:
    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
    argv = box.exec_argv("npm run build", box.root / "shop")
    root, home = str(box.root.resolve()), str(box.home.resolve())
    rw = [argv[i + 1] for i, a in enumerate(argv) if a == "--bind"]
    assert rw == [root, home], "nothing else is writable"
    ro = {argv[i + 1] for i, a in enumerate(argv) if a in ("--ro-bind", "--ro-bind-try")}
    assert not any(p.startswith(("/home", "/root", str(box.root.parent.parent))) for p in ro)
    for flag in ("--clearenv", "--die-with-parent", "--unshare-pid", "--new-session"):
        assert flag in argv
    assert argv[argv.index("--chdir") + 1] == str((box.root / "shop").resolve())
    assert argv[-3:] == ["/bin/sh", "-c", "npm run build"]
    assert box.exec_argv("ls", "/etc")[box.exec_argv("ls", "/etc").index("--chdir") + 1] == root  # never outside


def test_no_network_when_configured(monkeypatch, box) -> None:
    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
    assert "--unshare-net" not in box.exec_argv("ls")
    monkeypatch.setenv("CATCREW_SANDBOX_NETWORK", "none")
    assert "--unshare-net" in box.exec_argv("ls")


def test_a_bound_turn_runs_commands_through_bwrap(monkeypatch, box) -> None:
    from backend.services import terminal

    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
    seen: dict = {}

    def fake_popen(**kwargs):
        seen.update(kwargs)
        raise OSError("stop here")

    monkeypatch.setattr(terminal.subprocess, "Popen", fake_popen)
    token = sandbox.bind(box)
    try:
        out = terminal.run_foreground("php artisan migrate", box.root / "shop", timeout=10, env={})
    finally:
        sandbox.reset(token)
    assert "stop here" in out
    assert seen["shell"] is False and seen["args"][0] == "/usr/bin/bwrap" and seen["args"][-1] == "php artisan migrate"


def test_the_project_check_runs_the_projects_code_in_the_sandbox(monkeypatch, box) -> None:
    from backend.harness import project_check

    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
    seen: list = []
    monkeypatch.setattr(project_check.subprocess, "run", lambda args, **kw: seen.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    token = sandbox.bind(box)
    try:
        project_check._run(["/usr/local/bin/npm", "run", "build"], box.root / "shop")
    finally:
        sandbox.reset(token)
    assert seen[0][0] == "/usr/bin/bwrap" and seen[0][-1] == "npm run build"


def test_the_guard_refuses_admin_tools_the_servers_network_and_other_users_servers(monkeypatch, box) -> None:
    assert sandbox.guard_tool("github_push", {}) is None  # not bound: admins and the desktop
    token = sandbox.bind(box)
    try:
        assert "not available to your account" in sandbox.guard_tool("github_push", {})
        assert "not available" in sandbox.guard_tool("ssh_command", {"host": "x", "command": "ls"})
        assert "server's own network" in sandbox.guard_tool("web_fetch", {"url": "http://169.254.169.254/latest/meta-data/"})
        assert "server's own network" in sandbox.guard_tool("browser_navigate", {"url": "http://10.0.0.7:5432"})
        monkeypatch.setattr(sandbox, "owns_port", lambda b, port: port == 8000)
        assert sandbox.guard_tool("browser_navigate", {"url": "127.0.0.1:8000/admin"}) is None  # their dev server
        assert "server's own network" in sandbox.guard_tool("browser_navigate", {"url": "127.0.0.1:8765/api/keys"})
        monkeypatch.setattr(sandbox, "_blocked_ip", lambda host: False)
        assert sandbox.guard_tool("web_fetch", {"url": "https://laravel.com/docs"}) is None
    finally:
        sandbox.reset(token)


def test_git_in_a_confined_turn_runs_no_hooks() -> None:
    from backend.services import git_ops
    from backend.services.workspace import bind_confined, reset_confined

    assert git_ops._git_env().get("GIT_CONFIG_KEY_0") != "core.hooksPath"
    token = bind_confined(True)
    try:
        env = git_ops._git_env()
    finally:
        reset_confined(token)
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs == {"core.hooksPath": "/dev/null", "core.fsmonitor": "false", "protocol.ext.allow": "never"}


def test_a_sandboxed_terminal_runs_in_the_sandbox_and_stays_in_the_workspace(monkeypatch, box) -> None:
    from backend.services import terminal_service
    from backend.services.terminal_service import TerminalSession

    monkeypatch.setattr(sandbox, "_bwrap", lambda: "/usr/bin/bwrap")

    class Proc:
        pid = 999999

    seen: dict = {}
    monkeypatch.setattr(terminal_service.subprocess, "Popen", lambda **kw: seen.update(kw) or Proc())
    events: list[dict] = []
    session = TerminalSession("p1", box.root / "shop", events.append)
    session.root = box.root
    session.sandbox = box
    proc = session._spawn("ls -la")
    assert seen["args"][0] == "/usr/bin/bwrap" and seen["args"][-1] == "ls -la"
    assert proc.catcrew_sandbox[0] is box
    session._change_dir("/etc")
    assert session.cwd == box.root / "shop" and "no such directory" in events[-2]["data"]
    session._change_dir("..")
    assert session.cwd == box.root.resolve()


def test_user_invites_open_once_the_sandbox_is_ready(isolated, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.core.chat_service import ChatService
    from backend.services import auth

    app = create_app(chat_service=ChatService(use_memory=False), remote=True)
    with TestClient(app, base_url="https://cats.example.com") as client:
        client.post("/api/auth/register", json={"username": "ada", "password": "a-long-password", "setup_code": auth.issue_setup_code()})
        monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"ready": False, "detail": "no bwrap"})
        assert client.post("/api/users/invites", json={"role": "user"}).status_code == 409
        monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"ready": True, "detail": "ok"})
        res = client.post("/api/users/invites", json={"role": "user"})
        assert res.status_code == 201 and "/#/invite/" in res.json()["url"]


def test_a_regular_users_turn_is_confined_and_sandboxed(isolated) -> None:
    from backend.api import access
    from backend.core import chat_service
    from backend.platform.models import Project
    from backend.platform.registry import load_registry, save_registry
    from backend.services import auth

    access.configure(remote=True)
    admin = auth.register_first_admin("ada", "a-long-password", auth.issue_setup_code())
    bob = auth.redeem_invite(auth.create_invite(admin.id), "bob", "bobs-long-password")
    registry = load_registry(refresh=True)
    registry.projects["pa"] = Project(id="pa", name="A", owner_id=admin.id)
    registry.projects["pb"] = Project(id="pb", name="B", owner_id=bob.id)
    save_registry(registry)
    assert chat_service._confined("pb") and not chat_service._confined("pa")
    box = chat_service._sandbox_for("pb")
    assert box.user_id == bob.id and box.root == Path(access.USERS_DIR / bob.id / "workspace")


# -- real bubblewrap ------------------------------------------------------------------------


@live
def test_live_another_users_files_and_the_servers_secrets_do_not_exist(tmp_path, monkeypatch) -> None:
    from backend.services import terminal

    users = tmp_path / "users"
    (users / "u_eve" / "workspace").mkdir(parents=True)
    (users / "u_eve" / "workspace" / ".env").write_text("EVE_SECRET=1")
    box = sandbox.Sandbox(user_id="u_bob", root=users / "u_bob" / "workspace")
    monkeypatch.setenv("SECRET_API_KEY", "sk-live-never")
    token = sandbox.bind(box)
    try:
        def run(cmd: str) -> str:
            return terminal.run_foreground(cmd, box.root, timeout=30, env={"SECRET_API_KEY": "sk-live-never"})

        run("echo made > made.txt")
        peek = run(f"cat {users}/u_eve/workspace/.env")
        listing = run(f"ls {users}").splitlines()[1:]
        env = run("env")
    finally:
        sandbox.reset(token)
    assert (box.root / "made.txt").read_text().strip() == "made"
    assert "EVE_SECRET" not in peek and "No such file" in peek
    assert listing == ["u_bob"]  # the other user's folder does not exist in here
    assert "sk-live-never" not in env and "SECRET_API_KEY" not in env


@live
def test_live_stopping_a_background_server_ends_everything_inside(tmp_path) -> None:
    from backend.services import terminal

    box = sandbox.Sandbox(user_id="u_srv", root=tmp_path / "users" / "u_srv" / "workspace")
    token = sandbox.bind(box)
    try:
        terminal.run_background("sh -c 'sleep 300' & python3 -m http.server 8791 --bind 127.0.0.1", box.root, env={})
    finally:
        sandbox.reset(token)
    for _ in range(40):
        if sandbox._listener_pid(8791):
            break
        time.sleep(0.1)
    assert sandbox.owns_port(box, 8791)
    assert not sandbox.owns_port(sandbox.Sandbox(user_id="u_other", root=tmp_path / "x"), 8791)
    hub = terminal.get_hub()
    for sid in list(getattr(hub, "_sessions", {})):
        hub.kill(sid)
    for _ in range(40):
        if sandbox._listener_pid(8791) is None:
            break
        time.sleep(0.1)
    assert sandbox._listener_pid(8791) is None
