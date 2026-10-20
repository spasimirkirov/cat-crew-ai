#!/usr/bin/env bash
# Update CatCrewAI on a VPS set up with setup.sh: new code, packages, web UI, restart.
#
#   sudo /opt/cat-crew-ai/scripts/vps/update.sh             # git pull in the install folder
#   sudo scripts/vps/update.sh --from /home/me/cat-crew-ai   # copy from another checkout
#
# The data (accounts, projects, keys in data/ and .env) is never touched.
set -euo pipefail

DIR="/opt/cat-crew-ai"
APP_USER="catcrew"
FROM=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="${2:-}"; shift 2 ;;
    --from) FROM="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "run it with sudo" >&2; exit 1; }
[[ -f "$DIR/backend/__main__.py" ]] || { echo "no CatCrewAI in $DIR" >&2; exit 1; }
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
as_app() { sudo -u "$APP_USER" -H "$@"; }

if [[ -n "$FROM" ]]; then
  say "Copying the code from $FROM"
  rsync -a --delete --exclude '/data/' --exclude '/.env' --exclude '/.venv/' \
    --exclude 'node_modules/' --exclude '/frontend/dist/' "$FROM/" "$DIR/"
  chown -R "$APP_USER:$APP_USER" "$DIR"
elif [[ -d "$DIR/.git" ]]; then
  say "git pull"
  as_app git -C "$DIR" pull --ff-only
else
  echo "no git checkout in $DIR: pass --from <checkout>" >&2; exit 1
fi

say "Python packages"
as_app "$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
say "Web UI"
as_app sh -c "cd '$DIR/frontend' && npm ci --no-audit --no-fund && npm run build"
say "Restart"
systemctl restart catcrew
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$(systemctl show catcrew -p ExecStart --value | grep -o -- '--port [0-9]*' | awk '{print $2}')/api/health" >/dev/null 2>&1 && { say "Running."; exit 0; }
  sleep 1
done
echo "the app did not come back: journalctl -u catcrew -n 50" >&2
exit 1
