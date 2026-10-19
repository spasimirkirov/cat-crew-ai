"""GitHub tools for Forge (devops): push, branches, pull requests, merges, releases and Actions.

They authenticate with the token saved for the GitHub connector (Settings -> Connectors ->
GitHub), so there is one place to put it. The token never appears in a tool result, a command
line or a log: the REST calls send it as a header, and ``github_push`` hands it to git through
environment variables. The repository defaults to the project's ``origin`` remote.

Token scopes: a classic token needs ``repo`` (and ``workflow`` to push files under
``.github/workflows``); a fine-grained token needs Contents, Pull requests and Actions read/write
(and Workflows write) on the repository.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

_API = "https://api.github.com"
_REMOTE = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_MERGE_METHODS = ("squash", "merge", "rebase")
_NO_TOKEN = (
    "Error: no GitHub token. Save one under Settings -> Connectors -> GitHub (classic: repo + "
    "workflow scopes; fine-grained: Contents, Pull requests, Actions and Workflows read/write)."
)


class _GitHubError(RuntimeError):
    pass


def _token() -> str:
    from backend.connectors.store import get_connector

    cfg = get_connector("github")
    token = (cfg.token if cfg else "").strip()
    return token[7:].strip() if token.lower().startswith("bearer ") else token


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _call(method: str, path: str, body: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
    token = _token()
    if not token:
        raise _GitHubError(_NO_TOKEN)
    url = f"{_API}{path}"
    clean = {k: v for k, v in (query or {}).items() if v not in (None, "")}
    if clean:
        url += "?" + urlencode(clean)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "CatCrewAI",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed GitHub API host
            raw = resp.read().decode("utf-8", errors="replace")
            return (json.loads(raw) if raw.strip() else {}), dict(resp.headers.items())
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        try:
            detail = json.loads(detail).get("message") or detail
        except ValueError:
            pass
        raise _GitHubError(f"Error: GitHub answered HTTP {exc.code} for {method} {path}: {_scrub(str(detail), token)}") from exc
    except URLError as exc:
        raise _GitHubError(f"Error: could not reach GitHub: {exc.reason}") from exc


def _origin_repo() -> str:
    from backend.services.git_ops import git_root, run_git

    root = git_root()
    if root is None:
        return ""
    code, out = run_git(["remote", "get-url", "origin"], root)
    match = _REMOTE.search(out.strip()) if code == 0 else None
    return f"{match.group(1)}/{match.group(2)}" if match else ""


def _repo(repo: str) -> str:
    name = (repo or "").strip().strip("/") or _origin_repo()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
        raise _GitHubError(
            "Error: no repository. Pass repo as owner/name, or add a github.com origin remote to "
            "the project (git remote add origin https://github.com/owner/name.git)."
        )
    return name


def _repo_path(repo: str) -> str:
    owner, name = _repo(repo).split("/", 1)
    return f"/repos/{quote(owner)}/{quote(name)}"


def _run(fn) -> str:
    try:
        return fn()
    except _GitHubError as exc:
        return str(exc)


def github_status(repo: str = "") -> str:
    """Check the GitHub connection before working on a repository: the account the token belongs
    to, its scopes, the repository's default branch and whether the token may push and administer.

    Args:
        repo: ``owner/name``. Empty uses the project's ``origin`` remote.

    Returns:
        A short status report, or an ``Error:`` message saying what is missing.
    """

    def go() -> str:
        user, headers = _call("GET", "/user")
        data, _ = _call("GET", _repo_path(repo))
        perms = data.get("permissions") or {}
        scopes = headers.get("X-OAuth-Scopes", headers.get("x-oauth-scopes", ""))
        return "\n".join(
            [
                f"account: {user.get('login')}",
                f"token scopes: {scopes or '(fine-grained token: scopes are per repository)'}",
                f"repository: {data.get('full_name')} ({'private' if data.get('private') else 'public'})",
                f"default branch: {data.get('default_branch')}",
                f"may push: {bool(perms.get('push'))}; admin: {bool(perms.get('admin'))}",
                "pushing .github/workflows files needs the workflow scope"
                + (" (present)" if "workflow" in scopes else " (not listed)" if scopes else ""),
            ]
        )

    return _run(go)


def github_push(branch: str = "") -> str:
    """Push the current (or the named) local branch to ``origin`` on GitHub, setting its upstream.

    Commit first (git_commit). Never force-pushes. The token goes to git through environment
    variables, so it is not in the command or the output.

    Args:
        branch: Local branch to push. Empty pushes the branch that is checked out.

    Returns:
        git's output, or an ``Error:`` message.
    """
    from backend.services.git_ops import _git_env, git_root

    import subprocess

    token = _token()
    if not token:
        return _NO_TOKEN
    root = git_root()
    if root is None:
        return "Error: the project is not a git repository."
    name = (branch or "").strip()
    if name and not re.fullmatch(r"[A-Za-z0-9._/-]+", name):
        return f"Error: invalid branch name {name!r}."
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = _git_env()
    env.update(
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
        GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}",
    )
    try:
        done = subprocess.run(
            ["git", "push", "-u", "origin", name or "HEAD"],
            cwd=str(root), capture_output=True, text=True, timeout=120, env=env,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Error: git push failed: {exc}"
    out = _scrub(((done.stdout or "") + "\n" + (done.stderr or "")).strip(), basic)
    out = _scrub(out, token)
    if done.returncode != 0:
        hint = " (pushing workflow files needs the workflow scope)" if "workflow" in out.lower() else ""
        return f"Error: git push exited {done.returncode}{hint}:\n{out}"
    return out or "Pushed."


def github_create_branch(name: str, from_ref: str = "", repo: str = "") -> str:
    """Create a branch on GitHub from another branch, tag or commit.

    Args:
        name: New branch name, e.g. ``release/1.2``.
        from_ref: Branch, tag or commit SHA to start from. Empty uses the default branch.
        repo: ``owner/name``. Empty uses the project's ``origin`` remote.

    Returns:
        The new branch and the commit it points at, or an ``Error:`` message.
    """

    def go() -> str:
        base = _repo_path(repo)
        start = (from_ref or "").strip()
        if not start:
            start = (_call("GET", base)[0]).get("default_branch") or "main"
        if re.fullmatch(r"[0-9a-f]{40}", start):
            sha = start
        else:
            sha = (_call("GET", f"{base}/commits/{quote(start, safe='')}")[0]).get("sha")
        _call("POST", f"{base}/git/refs", {"ref": f"refs/heads/{name.strip()}", "sha": sha})
        return f"Created branch {name.strip()} at {str(sha)[:10]} (from {start})."

    return _run(go)


def github_create_pull_request(title: str, head: str, base: str = "", body: str = "", draft: bool = False, repo: str = "") -> str:
    """Open a pull request.

    Args:
        title: Pull request title.
        head: Branch with the changes (push it first with github_push).
        base: Branch to merge into. Empty uses the default branch.
        body: Description in Markdown: what changed and how it was checked.
        draft: Open as a draft.
        repo: ``owner/name``. Empty uses the project's ``origin`` remote.

    Returns:
        The pull request number and URL, or an ``Error:`` message.
    """

    def go() -> str:
        path = _repo_path(repo)
        target = (base or "").strip() or (_call("GET", path)[0]).get("default_branch") or "main"
        pr, _ = _call("POST", f"{path}/pulls", {"title": title, "head": head, "base": target, "body": body, "draft": bool(draft)})
        return f"Opened PR #{pr.get('number')}: {pr.get('html_url')} ({head} -> {target})"

    return _run(go)


def github_merge_pull_request(number: int, method: str = "squash", commit_title: str = "", repo: str = "") -> str:
    """Merge a pull request once it is mergeable and its checks pass.

    Refuses when GitHub reports a conflict or failing checks, and says which. Merging changes
    the target branch for everyone: do it when the work order or the user asks for it.

    Args:
        number: Pull request number.
        method: ``squash`` (default), ``merge`` or ``rebase``.
        commit_title: Optional title of the merge commit.
        repo: ``owner/name``. Empty uses the project's ``origin`` remote.

    Returns:
        The merge commit, or why it was not merged.
    """

    def go() -> str:
        how = (method or "squash").strip().lower()
        if how not in _MERGE_METHODS:
            return f"Error: method must be one of {', '.join(_MERGE_METHODS)}."
        path = _repo_path(repo)
        pr, _ = _call("GET", f"{path}/pulls/{int(number)}")
        if pr.get("merged"):
            return f"PR #{number} is already merged."
        if pr.get("state") != "open":
            return f"Error: PR #{number} is {pr.get('state')}."
        if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
            return f"Error: PR #{number} has merge conflicts with {pr.get('base', {}).get('ref')}; resolve them first."
        sha = (pr.get("head") or {}).get("sha", "")
        runs, _ = _call("GET", f"{path}/commits/{sha}/check-runs", query={"per_page": 50})
        failing = [r.get("name") for r in runs.get("check_runs") or [] if r.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")]
        pending = [r.get("name") for r in runs.get("check_runs") or [] if r.get("status") != "completed"]
        if failing:
            return f"Error: not merged, checks failing on PR #{number}: {', '.join(failing)}."
        if pending:
            return f"Not merged yet: checks still running on PR #{number}: {', '.join(pending)}. Check again with github_actions_runs."
        body: dict[str, Any] = {"merge_method": how, "sha": sha}
        if commit_title:
            body["commit_title"] = commit_title
        done, _ = _call("PUT", f"{path}/pulls/{int(number)}/merge", body)
        return f"Merged PR #{number} ({how}): {str(done.get('sha', ''))[:10]} {done.get('message', '')}".strip()

    return _run(go)


def github_create_release(tag: str, name: str = "", notes: str = "", target: str = "", draft: bool = False, prerelease: bool = False, repo: str = "") -> str:
    """Publish a GitHub release, creating its tag when it does not exist yet.

    Args:
        tag: Tag name, e.g. ``v1.2.0`` (semantic versioning).
        name: Release title. Empty uses the tag.
        notes: Release notes in Markdown. Empty lets GitHub generate them from the merged PRs.
        target: Branch or commit the new tag points at. Empty uses the default branch.
        draft: Save as a draft instead of publishing.
        prerelease: Mark as a pre-release.
        repo: ``owner/name``. Empty uses the project's ``origin`` remote.

    Returns:
        The release URL, or an ``Error:`` message.
    """

    def go() -> str:
        body: dict[str, Any] = {
            "tag_name": tag.strip(),
            "name": (name or tag).strip(),
            "draft": bool(draft),
            "prerelease": bool(prerelease),
        }
        if target:
            body["target_commitish"] = target.strip()
        if notes.strip():
            body["body"] = notes
        else:
            body["generate_release_notes"] = True
        rel, _ = _call("POST", f"{_repo_path(repo)}/releases", body)
        state = "draft" if rel.get("draft") else "pre-release" if rel.get("prerelease") else "published"
        return f"Release {rel.get('tag_name')} {state}: {rel.get('html_url')}"

    return _run(go)


def github_actions_runs(workflow: str = "", branch: str = "", limit: int = 5, repo: str = "") -> str:
    """List recent GitHub Actions runs and their result, newest first.

    Args:
        workflow: Workflow file name (``ci.yml``) or id. Empty lists runs of every workflow.
        branch: Only runs for this branch.
        limit: How many runs (1-20).
        repo: ``owner/name``. Empty uses the project's ``origin`` remote.

    Returns:
        One line per run: workflow, branch, status/conclusion, commit and URL.
    """

    def go() -> str:
        path = _repo_path(repo)
        where = f"{path}/actions/workflows/{quote(workflow.strip(), safe='')}/runs" if workflow.strip() else f"{path}/actions/runs"
        data, _ = _call("GET", where, query={"branch": branch, "per_page": max(1, min(int(limit or 5), 20))})
        runs = data.get("workflow_runs") or []
        if not runs:
            return "No workflow runs found."
        return "\n".join(
            f"{r.get('name')} #{r.get('run_number')} [{r.get('head_branch')}] "
            f"{r.get('conclusion') or r.get('status')} {str(r.get('head_sha', ''))[:7]} {r.get('html_url')}"
            for r in runs
        )

    return _run(go)


def github_run_workflow(workflow: str, ref: str = "", inputs: str = "", repo: str = "") -> str:
    """Start a GitHub Actions workflow that has a ``workflow_dispatch`` trigger (a deploy, a release
    build...). Follow it with github_actions_runs.

    Args:
        workflow: Workflow file name (``deploy.yml``) or id.
        ref: Branch or tag to run on. Empty uses the default branch.
        inputs: The workflow's ``workflow_dispatch`` inputs as a JSON object, e.g.
            ``{"environment": "production"}``. Empty for none.
        repo: ``owner/name``. Empty uses the project's ``origin`` remote.

    Returns:
        A confirmation, or an ``Error:`` message.
    """

    def go() -> str:
        path = _repo_path(repo)
        target = (ref or "").strip() or (_call("GET", path)[0]).get("default_branch") or "main"
        try:
            given = json.loads(inputs) if isinstance(inputs, str) and inputs.strip() else (inputs or {})
        except ValueError:
            return "Error: inputs must be a JSON object, e.g. {\"environment\": \"production\"}."
        if not isinstance(given, dict):
            return "Error: inputs must be a JSON object."
        body = {"ref": target, "inputs": {str(k): str(v) for k, v in given.items()}}
        _call("POST", f"{path}/actions/workflows/{quote(workflow.strip(), safe='')}/dispatches", body)
        return f"Started {workflow} on {target}. Check its result with github_actions_runs(workflow={workflow!r})."

    return _run(go)
