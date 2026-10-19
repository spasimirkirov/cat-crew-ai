"""Forge's GitHub tools and ssh_command, with GitHub, git and ssh replaced by fakes."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from backend.tool_functions import forge_tools, github_tools

TOKEN = "ghp_secret_token_value"


@pytest.fixture
def gh(monkeypatch):
    """Record every REST call and answer from ``replies`` keyed by (method, path-suffix)."""
    calls: list[tuple[str, str, dict | None]] = []
    replies: dict[tuple[str, str], object] = {}

    def fake_call(method, path, body=None, query=None):
        calls.append((method, path, body))
        for (m, suffix), value in replies.items():
            if m == method and path.endswith(suffix):
                if isinstance(value, Exception):
                    raise value
                return value, {"X-OAuth-Scopes": "repo, workflow"}
        return {}, {}

    monkeypatch.setattr(github_tools, "_call", fake_call)
    monkeypatch.setattr(github_tools, "_origin_repo", lambda: "acme/site")
    return SimpleNamespace(calls=calls, replies=replies)


def test_the_repository_comes_from_the_origin_remote_or_the_argument(gh) -> None:
    gh.replies[("GET", "/repos/acme/site")] = {"default_branch": "main"}
    gh.replies[("POST", "/pulls")] = {"number": 7, "html_url": "https://github.com/acme/site/pull/7"}
    out = github_tools.github_create_pull_request("Add contact page", head="feature/contact")
    assert out == "Opened PR #7: https://github.com/acme/site/pull/7 (feature/contact -> main)"
    assert gh.calls[-1] == ("POST", "/repos/acme/site/pulls", {"title": "Add contact page", "head": "feature/contact", "base": "main", "body": "", "draft": False})
    github_tools.github_create_pull_request("x", head="b", base="develop", repo="other/repo")
    assert gh.calls[-1][1] == "/repos/other/repo/pulls"


def test_remote_urls_in_every_form_name_the_repository() -> None:
    for url in ("https://github.com/acme/site.git", "git@github.com:acme/site.git", "https://github.com/acme/site"):
        match = github_tools._REMOTE.search(url)
        assert match and (match.group(1), match.group(2)) == ("acme", "site"), url


def test_merge_refuses_failing_or_running_checks_and_conflicts(gh) -> None:
    pr = {"state": "open", "merged": False, "mergeable": True, "head": {"sha": "abc"}, "base": {"ref": "main"}}
    gh.replies[("GET", "/pulls/3")] = pr
    gh.replies[("GET", "/check-runs")] = {"check_runs": [{"name": "tests", "status": "completed", "conclusion": "failure"}]}
    assert "checks failing on PR #3: tests" in github_tools.github_merge_pull_request(3)
    gh.replies[("GET", "/check-runs")] = {"check_runs": [{"name": "tests", "status": "in_progress", "conclusion": None}]}
    assert "checks still running" in github_tools.github_merge_pull_request(3)
    assert not any(m == "PUT" for m, _p, _b in gh.calls)
    gh.replies[("GET", "/pulls/3")] = {**pr, "mergeable": False}
    assert "merge conflicts" in github_tools.github_merge_pull_request(3)
    assert "method must be" in github_tools.github_merge_pull_request(3, method="octopus")


def test_merge_goes_through_when_checks_pass(gh) -> None:
    gh.replies[("GET", "/pulls/3")] = {"state": "open", "merged": False, "mergeable": True, "head": {"sha": "abc"}}
    gh.replies[("GET", "/check-runs")] = {"check_runs": [{"name": "tests", "status": "completed", "conclusion": "success"}]}
    gh.replies[("PUT", "/pulls/3/merge")] = {"sha": "def4567890", "message": "Pull Request successfully merged"}
    out = github_tools.github_merge_pull_request(3)
    assert out.startswith("Merged PR #3 (squash): def4567890")
    assert gh.calls[-1] == ("PUT", "/repos/acme/site/pulls/3/merge", {"merge_method": "squash", "sha": "abc"})


def test_release_lets_github_write_the_notes_when_none_are_given(gh) -> None:
    gh.replies[("POST", "/releases")] = {"tag_name": "v1.2.0", "html_url": "https://github.com/acme/site/releases/tag/v1.2.0"}
    assert "Release v1.2.0 published" in github_tools.github_create_release("v1.2.0")
    assert gh.calls[-1][2] == {"tag_name": "v1.2.0", "name": "v1.2.0", "draft": False, "prerelease": False, "generate_release_notes": True}


def test_branch_from_the_default_branch_and_workflow_dispatch(gh) -> None:
    gh.replies[("GET", "/repos/acme/site")] = {"default_branch": "main"}
    gh.replies[("GET", "/commits/main")] = {"sha": "a" * 40}
    assert github_tools.github_create_branch("release/1.2").startswith("Created branch release/1.2 at aaaaaaaaaa")
    assert gh.calls[-1] == ("POST", "/repos/acme/site/git/refs", {"ref": "refs/heads/release/1.2", "sha": "a" * 40})
    out = github_tools.github_run_workflow("deploy.yml", inputs='{"environment": "production"}')
    assert out.startswith("Started deploy.yml on main")
    assert gh.calls[-1][2] == {"ref": "main", "inputs": {"environment": "production"}}
    assert "JSON object" in github_tools.github_run_workflow("deploy.yml", inputs="not json")


def test_without_a_token_every_tool_says_where_to_put_one(monkeypatch) -> None:
    monkeypatch.setattr(github_tools, "_token", lambda: "")
    monkeypatch.setattr(github_tools, "_origin_repo", lambda: "acme/site")
    assert github_tools.github_status().startswith("Error: no GitHub token")
    assert github_tools.github_push().startswith("Error: no GitHub token")


def test_push_hands_the_token_to_git_through_the_environment_only(monkeypatch, tmp_path) -> None:
    seen: dict = {}

    def fake_run(args, **kw):
        seen["args"], seen["env"] = args, kw["env"]
        return subprocess.CompletedProcess(args, 0, stdout="", stderr=f"remote: using {TOKEN}\nTo github.com:acme/site.git")

    from backend.services import git_ops

    monkeypatch.setattr(github_tools, "_token", lambda: TOKEN)
    monkeypatch.setattr(git_ops, "git_root", lambda cwd=None: tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = github_tools.github_push("feature/contact")
    assert seen["args"] == ["git", "push", "-u", "origin", "feature/contact"]
    assert TOKEN not in " ".join(seen["args"])
    assert seen["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert seen["env"]["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")
    assert TOKEN not in out and "***" in out
    assert "--force" not in seen["args"]
    assert github_tools.github_push("bad name; rm").startswith("Error: invalid branch name")


def test_ssh_command_runs_in_batch_mode_and_refuses_what_is_refused_locally(monkeypatch) -> None:
    seen: dict = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="Migrated", stderr="")

    monkeypatch.setattr(forge_tools.shutil, "which", lambda name: "/usr/bin/ssh")
    monkeypatch.setattr(forge_tools.subprocess, "run", fake_run)
    out = forge_tools.ssh_command("vps.example.com", "php artisan migrate --force", user="deploy", cwd="/var/www/my site")
    assert out == "exit 0 on vps.example.com\nMigrated"
    assert seen["args"][:3] == ["ssh", "-o", "BatchMode=yes"]
    assert "deploy@vps.example.com" in seen["args"]
    assert seen["args"][-1] == "cd '/var/www/my site' && php artisan migrate --force"
    assert forge_tools.ssh_command("-oProxyCommand=x", "ls").startswith("Error: invalid host")
    assert forge_tools.ssh_command("vps", "rm -rf /").startswith("Refused")


def test_every_github_tool_is_forges_alone() -> None:
    from backend.platform.essentials import GITHUB, essential_agents_for_slug

    for slug in (*GITHUB, "ssh_command"):
        assert essential_agents_for_slug(slug) == {"forge"}, slug


def test_pricing_briefs_go_to_blaze_without_an_accountant(isolated) -> None:
    from backend.platform import sop
    from backend.platform.catcrew import seed_catcrew
    from backend.platform.models import Project

    registry = seed_catcrew(replace_catalog=True)
    project = Project(id="p1", name="Shop")
    assert sop._bot_for_role(registry, project, "accountant").id == "blaze"
    extra = sop._spontaneous_bots(registry, project, "SEO and pricing plans for the shop")
    assert [b.id for b in extra] == ["blaze"]  # once, not once per keyword family
    assert json.dumps([b.id for b in sop._spontaneous_bots(registry, project, "a plain page")]) == "[]"
