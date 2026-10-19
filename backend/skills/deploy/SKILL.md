---
name: deploy
description: Deploy a site to a VPS over SSH with ssh_command - first-time server setup, the release steps for Laravel and Node, a health check after, and how to roll back.
---

# Deploying over SSH

`ssh_command` runs one command on the server with this machine's SSH keys, never a password.
"Permission denied (publickey)" means the server does not have this machine's key: tell the
person to add `~/.ssh/id_ed25519.pub` to the server's `~/.ssh/authorized_keys`, and stop.

## Before you deploy

1. The build is green locally: tests pass, `npm run build` works, and the security checks are
   done (`security-checks`).
2. The code is on GitHub (`github-release`): the server pulls a pushed commit or tag, never files
   copied from this machine.
3. `vps_health_check(host)` shows port 22 open; `ssh_command(host, "uname -a && df -h / && free -m")`
   shows the server and its free disk and memory.
4. Write down the commit that is live now (`ssh_command(host, "git rev-parse HEAD", cwd=APP)`): that is
   your rollback point.

## Laravel release (APP = the app folder, e.g. /var/www/site)

Run as one `ssh_command` per step, with `cwd=APP`, and stop at the first failure:

1. `php artisan down --retry=60`
2. `git fetch --tags && git checkout <tag-or-branch> && git pull --ff-only`
3. `composer install --no-dev --optimize-autoloader --no-interaction`
4. `npm ci && npm run build` (or build in CI and ship the assets)
5. `php artisan migrate --force`
6. `php artisan config:cache && php artisan route:cache && php artisan view:cache`
7. `php artisan storage:link` (first deploy only), `php artisan queue:restart` if queues run
8. `php artisan up`

First deploy on a fresh server: clone the repository, `cp .env.example .env`, fill the production
values (`APP_ENV=production`, `APP_DEBUG=false`, `APP_URL`, database, mail) with the person, never
from your own guesses, then `php artisan key:generate`. The web server's document root is
`APP/public`; `storage/` and `bootstrap/cache/` are writable by the web user.

## Node app

`git pull --ff-only && npm ci --omit=dev && npm run build`, then restart the process manager
(`pm2 reload <name>` or `sudo systemctl restart <service>`).

## After

- `http_endpoint_audit` the live URL and every acceptance-check path: 200, not a stack trace.
- `ssh_command(host, "tail -n 50 storage/logs/laravel.log", cwd=APP)` for new errors.
- Report the commit or tag now live, the commands run and the health check.

## Rollback

`git checkout <previous-commit>`, then steps 3-8 again. A migration that cannot be undone is a
reason to ask before deploying, not after. Never `migrate:fresh` or `migrate:reset` on production.
