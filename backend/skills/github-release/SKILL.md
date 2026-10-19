---
name: github-release
description: Ship through GitHub with Forge's tools - branch, commit, push, pull request, CI in GitHub Actions, merge, and a tagged release - plus ready workflow files for Laravel and Node.
---

# Shipping through GitHub

Your GitHub tools use the token saved under Settings -> Connectors -> GitHub. Start every session
with `github_status`: it names the account, the repository (from the `origin` remote), the
default branch and whether you may push. No token or no push rights: say so and stop.

## The flow

1. **Branch.** Never commit straight to the default branch. Locally:
   `run_command git switch -c feature/<short-name>` (or `fix/...`, `release/...`).
   `github_create_branch` makes one on GitHub without a checkout (a release branch from a tag).
2. **Commit.** `git_status`, then `git_commit` with only the files of this change and a message in
   the style of `commit-style`. Never commit `.env`, `vendor/`, `node_modules/`, build output.
3. **Push.** `github_push` (the checked-out branch, upstream set). It never force-pushes. If the
   push is rejected because the remote moved: `git pull --rebase` on your own branch, then push.
4. **Pull request.** `github_create_pull_request` with a body that says what changed, how it was
   checked (tests, build) and anything the reviewer must do (new env keys, migrations).
5. **CI.** `github_actions_runs(branch=...)` until the run for your last commit is `success`.
   A failure: read the run in the URL, fix, commit, push again. Do not merge red.
6. **Merge** only when the work order or the person asked for it: `github_merge_pull_request`
   (squash by default). It refuses on conflicts or failing checks and tells you which.
7. **Release.** `github_create_release` with a semantic-version tag (`v1.4.0`: major for breaking
   changes, minor for features, patch for fixes). Empty notes let GitHub write them from the merged
   pull requests. A deploy workflow with `workflow_dispatch` is started with `github_run_workflow`.

## Workflow files

Write them with `write_file` under `.github/workflows/`, commit, push. Pushing them needs the
token's `workflow` scope (classic) or Workflows write (fine-grained); `github_status` says.
Pin actions to a major version, keep secrets in repository secrets (`${{ secrets.NAME }}`), never
in the file.

Laravel CI (`.github/workflows/ci.yml`):

```yaml
name: CI
on: { push: { branches: [main] }, pull_request: {} }
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: shivammathur/setup-php@v2
        with: { php-version: '8.3', extensions: 'mbstring, pdo_sqlite', coverage: none }
      - uses: actions/setup-node@v4
        with: { node-version: '22', cache: npm }
      - run: composer install --no-interaction --prefer-dist
      - run: npm ci && npm run build
      - run: cp .env.example .env && php artisan key:generate
      - run: php artisan test
        env: { DB_CONNECTION: sqlite, DB_DATABASE: ':memory:' }
```

Match `php-version` to `composer.json` and `node-version` to the project. Node only: drop the PHP
steps and run `npm test`. Release on tag (`release.yml`): `on: push: tags: ['v*']`, build, then
upload the artifacts with `softprops/action-gh-release@v2`.

## Rules

- Never force-push, delete branches you did not create, or rewrite the default branch's history.
- Never print or commit the token; never put it in a workflow file or a remote URL.
- Merging and releasing are visible to everyone: report the PR number, the merge commit and the
  release URL in your reply.
