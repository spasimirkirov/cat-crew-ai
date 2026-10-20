"""The per-user sandbox: commands, checks and the terminal of a regular user run in their container."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.services import sandbox


class FakeDocker:
    """Records docker calls; the container counts as running once `docker run` happened."""

    def __init__(self, *, engine: bool = True, image: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.engine, self.image, self.running = engine, image, False

    def __call__(self, args: list[str], timeout: float = 20.0) -> tuple[int, str]:
        self.calls.append(list(args))
        verb = args[1]
        if verb == "info":
            return (0, "27.0.1") if self.engine else (1, "Cannot connect to the Docker daemon")
        if verb == "image":
            return (0, "[]") if self.image else (1, "No such image")
        if verb == "inspect" and "{{.State.Running}}" in args:
            return (0, "true") if self.running else (1, "No such container")
        if verb == "inspect":
            return 0, "172.17.0.5 "
        if verb == "run":
            self.running = True
            return 0, "cid"
        return 0, ""


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(sandbox, "_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(sandbox, "_run", fake)
    monkeypatch.setattr(sandbox, "_status_cache", None)
    return fake


@pytest.fixture
def box(tmp_path) -> sandbox.Sandbox:
    root = tmp_path / "users" / "u_bob" / "workspace"
    (root / "shop").mkdir(parents=True)
    return sandbox.Sandbox(user_id="u_bob", root=root)


def test_status_says_what_is_missing(monkeypatch, docker) -> None:
    assert sandbox.status(refresh=True)["ready"]
    docker.image = False
    assert "docker build -t" in sandbox.status(refresh=True)["detail"]
    docker.engine = False
    assert "not running" in sandbox.status(refresh=True)["detail"]
    monkeypatch.setattr(sandbox, "_docker", lambda: None)
    assert sandbox.status(refresh=True) == {"ready": False, "detail": "Docker is not installed on this server."}


def test_the_container_sees_only_the_workspace_with_limits_and_no_privileges(docker, box) -> None:
    argv = box.exec_argv("npm run build", box.root / "shop")
    run = next(c for c in docker.calls if c[1] == "run")
    assert ["--cap-drop", "ALL"] == run[run.index("--cap-drop"):run.index("--cap-drop") + 2]
    assert "no-new-privileges" in run and "--memory" in run and "--pids-limit" in run
    assert f"{box.root.resolve()}:/workspace" in run
    assert not any(":/" in a and str(box.root) not in a for a in run if a.startswith("/")), "no other host folder is mounted"
    assert argv[-4:] == [box.name, "sh", "-lc", "npm run build"]
    assert argv[argv.index("-w") + 1] == "/workspace/shop"
    box.exec_argv("ls")  # already running: no second `docker run`
    assert sum(1 for c in docker.calls if c[1] == "run") == 1


def test_paths_outside_the_workspace_never_map_into_the_container(box) -> None:
    assert box.container_path("/etc") == "/workspace"
    assert box.translate(str(box.root / "shop" / "node_modules" / ".bin" / "tsc")) == "/workspace/shop/node_modules/.bin/tsc"
    assert box.translate("/usr/bin/php") == "/usr/bin/php"


def test_a_bound_turn_runs_commands_through_docker_exec(monkeypatch, docker, box) -> None:
    from backend.services import terminal

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
    assert seen["shell"] is False
    assert seen["args"][:2] == ["/usr/bin/docker", "exec"] and seen["args"][-1] == "php artisan migrate"


def test_the_project_check_runs_the_projects_code_in_the_container(monkeypatch, docker, box) -> None:
    from backend.harness import project_check

    seen: list = []
    monkeypatch.setattr(project_check.subprocess, "run", lambda args, **kw: seen.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    token = sandbox.bind(box)
    try:
        project_check._run(["/usr/local/bin/npm", "run", "build"], box.root / "shop")
        assert project_check._which("php") == "php"  # the image has it, whatever the host has
    finally:
        sandbox.reset(token)
    assert seen[0][:2] == ["/usr/bin/docker", "exec"] and seen[0][-1] == "npm run build"


def test_the_guard_refuses_admin_tools_and_the_servers_network(monkeypatch, docker, box) -> None:
    assert sandbox.guard_tool("github_push", {}) is None  # not bound: admins and the desktop
    token = sandbox.bind(box)
    try:
        assert "not available to your account" in sandbox.guard_tool("github_push", {})
        assert "not available" in sandbox.guard_tool("ssh_command", {"host": "x", "command": "ls"})
        assert "server's own network" in sandbox.guard_tool("web_fetch", {"url": "http://169.254.169.254/latest/meta-data/"})
        assert "server's own network" in sandbox.guard_tool("browser_navigate", {"url": "http://10.0.0.7:5432"})
        args = {"url": "127.0.0.1:8000/admin"}
        assert sandbox.guard_tool("browser_navigate", args) is None
        assert args["url"] == "http://172.17.0.5:8000/admin"  # the user's own dev server, in the container
        monkeypatch.setattr(sandbox, "_blocked_ip", lambda host: False)
        public = {"url": "https://laravel.com/docs"}
        assert sandbox.guard_tool("web_fetch", public) is None and public["url"] == "https://laravel.com/docs"
    finally:
        sandbox.reset(token)


def test_git_in_a_confined_turn_runs_no_hooks() -> None:
    from backend.services import git_ops
    from backend.services.workspace import bind_confined, reset_confined

    assert "GIT_CONFIG_COUNT" not in git_ops._git_env() or git_ops._git_env().get("GIT_CONFIG_KEY_0") != "core.hooksPath"
    token = bind_confined(True)
    try:
        env = git_ops._git_env()
    finally:
        reset_confined(token)
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs == {"core.hooksPath": "/dev/null", "core.fsmonitor": "false", "protocol.ext.allow": "never"}


def test_a_sandboxed_terminal_runs_in_the_container_and_stays_in_the_workspace(monkeypatch, docker, box) -> None:
    from backend.services import terminal_service
    from backend.services.terminal_service import TerminalSession

    seen: dict = {}
    class Proc:
        pass

    monkeypatch.setattr(terminal_service.subprocess, "Popen", lambda **kw: seen.update(kw) or Proc())
    events: list[dict] = []
    session = TerminalSession("p1", box.root / "shop", events.append)
    session.root = box.root
    session.sandbox = box
    proc = session._spawn("ls -la")
    argv = seen["args"]
    assert argv[:2] == ["/usr/bin/docker", "exec"] and "-i" in argv and argv[-1] == "ls -la"
    assert proc.catcrew_sandbox[0] is box  # Stop kills it inside the container too
    assert argv[argv.index("-w") + 1] == "/workspace/shop"
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
        monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"ready": False, "detail": "no docker"})
        assert client.post("/api/users/invites", json={"role": "user"}).status_code == 409
        monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"ready": True, "detail": "ok"})
        res = client.post("/api/users/invites", json={"role": "user"})
        assert res.status_code == 201 and "/#/invite/" in res.json()["url"]


def test_a_regular_users_turn_is_confined_and_sandboxed(isolated, monkeypatch) -> None:
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
