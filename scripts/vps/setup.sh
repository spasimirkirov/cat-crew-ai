#!/usr/bin/env bash
# CatCrewAI on a VPS (Debian 12 / Ubuntu 22.04+): one command, safe to run again.
#
#   sudo scripts/vps/setup.sh --domain cats.example.com
#   sudo scripts/vps/setup.sh --domain cats.example.com --dry-run    # show what it would do
#
# What it does, step by step (each step says what it is doing):
#   1. a swap file when the machine has little memory (npm builds need it on 2 GB),
#   2. system packages: Python, PHP + Composer, Node.js 22, git, SQLite, bubblewrap,
#   3. a system user "catcrew" that runs the app (never root),
#   4. the app in /opt/cat-crew-ai: Python venv, the built web UI,
#   5. a check that bubblewrap can sandbox users here (Ubuntu 24.04 needs one kernel setting),
#   6. a systemd service: the app on 127.0.0.1 in remote mode (logins required),
#   7. Caddy in front with automatic HTTPS for your domain,
#   8. the firewall: SSH, HTTP and HTTPS only; the app's user cannot reach the cloud metadata address,
#   9. start, wait until healthy, print the setup code for the first (admin) account.
#
# Options:
#   --domain NAME     public domain pointing at this server (required unless --no-caddy)
#   --email ADDRESS   e-mail for the HTTPS certificate notices (optional)
#   --dir PATH        install folder (default /opt/cat-crew-ai)
#   --port N          the app's local port behind Caddy (default 8765)
#   --no-caddy        you run your own reverse proxy (it must send X-Forwarded-Proto)
#   --no-firewall     leave the firewall alone
#   --no-swap         never create a swap file
#   --yes             do not ask before changing the kernel setting for bubblewrap
#   --dry-run         print every command instead of running it
set -euo pipefail

DOMAIN=""
EMAIL=""
DIR="/opt/cat-crew-ai"
PORT="8765"
APP_USER="catcrew"
CADDY=1
FIREWALL=1
SWAP=1
YES=0
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --dir) DIR="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --no-caddy) CADDY=0; shift ;;
    --no-firewall) FIREWALL=0; shift ;;
    --no-swap) SWAP=0; shift ;;
    --yes|-y) YES=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_HOME="/var/lib/${APP_USER}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
run()  {
  if [[ $DRY -eq 1 ]]; then printf '    [dry-run] %s\n' "$*"; else "$@"; fi
}
as_app() { run sudo -u "$APP_USER" -H "$@"; }
write_file() {  # write_file PATH MODE  (content on stdin)
  local path="$1" mode="$2" content
  content="$(cat)"
  if [[ $DRY -eq 1 ]]; then
    printf '    [dry-run] write %s:\n' "$path"; printf '%s\n' "$content" | sed 's/^/        | /'
  else
    printf '%s\n' "$content" > "$path"; chmod "$mode" "$path"
  fi
}
confirm() {
  [[ $YES -eq 1 || $DRY -eq 1 ]] && return 0
  read -r -p "    $1 [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

# -- 0. preflight ------------------------------------------------------------------------------
[[ $DRY -eq 1 || $EUID -eq 0 ]] || die "run it with sudo (it installs packages and a service)."
command -v apt-get >/dev/null || die "this script is for Debian / Ubuntu (apt)."
[[ -f "$SRC/backend/__main__.py" ]] || die "run it from the CatCrewAI checkout (scripts/vps/setup.sh)."
[[ $CADDY -eq 0 || -n "$DOMAIN" ]] || die "pass --domain (your domain's DNS must point at this server), or --no-caddy."
[[ "$PORT" =~ ^[0-9]+$ ]] || die "--port must be a number."
. /etc/os-release 2>/dev/null || true
say "CatCrewAI setup on ${PRETTY_NAME:-this server}: ${DOMAIN:-no domain} -> 127.0.0.1:${PORT}, app in ${DIR}"
[[ $DRY -eq 1 ]] && note "Dry run: nothing is changed."

# -- 1. swap -----------------------------------------------------------------------------------
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [[ $SWAP -eq 1 && $mem_mb -lt 3500 && -z "$(swapon --noheadings 2>/dev/null)" ]]; then
  say "1/9 Swap: ${mem_mb} MB of memory and no swap, adding a 2 GB swap file (npm builds need it)"
  run fallocate -l 2G /swapfile
  run chmod 600 /swapfile
  run mkswap /swapfile
  run swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab 2>/dev/null || run sh -c "echo '/swapfile none swap sw 0 0' >> /etc/fstab"
else
  say "1/9 Swap: not needed (${mem_mb} MB, swap: $(swapon --noheadings 2>/dev/null | wc -l) device(s))"
fi

# -- 2. packages -------------------------------------------------------------------------------
say "2/9 System packages"
run apt-get update -y
run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg git rsync sudo unzip sqlite3 bubblewrap iptables \
  python3 python3-venv python3-pip \
  php-cli php-mbstring php-xml php-curl php-zip php-sqlite3 php-bcmath php-intl php-gd php-mysql
node_major=$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || true)
if [[ -z "$node_major" || "$node_major" -lt 20 ]]; then
  note "Node.js 22 from NodeSource"
  run sh -c "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -"
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
else
  note "Node.js $(node -v) is already installed"
fi
if ! command -v composer >/dev/null; then
  note "Composer (installer checked against its published signature)"
  run sh -c 'expected="$(curl -fsSL https://composer.github.io/installer.sig)"; \
    curl -fsSL https://getcomposer.org/installer -o /tmp/composer-setup.php; \
    actual="$(php -r "echo hash_file(\"sha384\", \"/tmp/composer-setup.php\");")"; \
    [ "$expected" = "$actual" ] || { echo "Composer installer checksum mismatch" >&2; exit 1; }; \
    php /tmp/composer-setup.php --quiet --install-dir=/usr/local/bin --filename=composer; rm -f /tmp/composer-setup.php'
fi
python_ok=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 10)))')
[[ "$python_ok" == "1" ]] || die "Python 3.10 or newer is needed (found $(python3 -V))."

# -- 3. the app's user -------------------------------------------------------------------------
say "3/9 User '${APP_USER}' (runs the app; not root, no login shell)"
if id "$APP_USER" >/dev/null 2>&1; then
  note "already exists"
else
  run useradd --system --create-home --home-dir "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
fi

# -- 4. the app --------------------------------------------------------------------------------
say "4/9 The app in ${DIR}"
if [[ "$(readlink -f "$SRC")" != "$(readlink -f "$DIR" 2>/dev/null || echo "$DIR")" ]]; then
  note "copying the code from ${SRC} (data/, .env and builds in ${DIR} are kept)"
  run mkdir -p "$DIR"
  run rsync -a --delete --exclude '/data/' --exclude '/.env' --exclude '/.venv/' \
    --exclude 'node_modules/' --exclude '/frontend/dist/' "$SRC/" "$DIR/"
fi
[[ -f "$DIR/.env" || ! -f "$DIR/.env.example" ]] || run cp "$DIR/.env.example" "$DIR/.env"
run mkdir -p "$DIR/data"
run chown -R "$APP_USER:$APP_USER" "$DIR"
run chmod 750 "$DIR"
run chmod 700 "$DIR/data"    # accounts, sessions and API keys: the app's user only
note "Python packages"
as_app python3 -m venv "$DIR/.venv"
as_app "$DIR/.venv/bin/pip" install --quiet --upgrade pip
as_app "$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
note "the web UI (npm ci + build; a few minutes on a small server)"
as_app sh -c "cd '$DIR/frontend' && npm ci --no-audit --no-fund && npm run build"

# -- 5. bubblewrap -----------------------------------------------------------------------------
say "5/9 Sandbox check: can bubblewrap isolate users here?"
# The app's own check (services/sandbox.status), run as the app's user: the same test the Users page shows.
bwrap_check() {
  sudo -u "$APP_USER" -H sh -c "cd '$DIR' && .venv/bin/python -c 'import sys; from backend.services import sandbox; s = sandbox.status(refresh=True); print(\"    \" + s[\"detail\"]); sys.exit(0 if s[\"ready\"] else 1)'"
}
if [[ $DRY -eq 1 ]]; then
  note "[dry-run] sudo -u $APP_USER $DIR/.venv/bin/python -c 'sandbox.status()'"
elif bwrap_check; then
  :
else
  restrict=$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo "")
  if [[ "$restrict" == "1" ]]; then
    note "Ubuntu restricts unprivileged user namespaces (kernel.apparmor_restrict_unprivileged_userns=1),"
    note "which bubblewrap needs to sandbox regular users."
    if confirm "Allow them (writes /etc/sysctl.d/60-catcrew-bwrap.conf)?"; then
      write_file /etc/sysctl.d/60-catcrew-bwrap.conf 644 <<< "kernel.apparmor_restrict_unprivileged_userns = 0"
      run sysctl -p /etc/sysctl.d/60-catcrew-bwrap.conf
      bwrap_check || note "still not working: only admins can be invited until it does"
    else
      note "left as it is: only admins can be invited until bubblewrap works (the Users page says so)"
    fi
  else
    note "only admins can be invited until bubblewrap works (the Users page says why)"
  fi
fi

# -- 6. the service ----------------------------------------------------------------------------
say "6/9 systemd service 'catcrew' (127.0.0.1:${PORT}, logins required)"
allowed=""
[[ -n "$DOMAIN" ]] && allowed="Environment=CATCREW_ALLOWED_HOSTS=${DOMAIN}"
write_file /etc/systemd/system/catcrew.service 644 <<EOF
[Unit]
Description=CatCrewAI
After=network-online.target
Wants=network-online.target

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${DIR}
Environment=PYTHONUNBUFFERED=1
${allowed}
ExecStart=${DIR}/.venv/bin/python -m backend --ui --remote --host 127.0.0.1 --port ${PORT} --no-browser
Restart=on-failure
RestartSec=5
# Hardening that leaves bubblewrap (user namespaces) working.
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=${DIR} ${APP_HOME}

[Install]
WantedBy=multi-user.target
EOF
run systemctl daemon-reload
run systemctl enable catcrew

# -- 7. Caddy ----------------------------------------------------------------------------------
if [[ $CADDY -eq 1 ]]; then
  say "7/9 Caddy: HTTPS for ${DOMAIN} (the certificate comes from Let's Encrypt automatically)"
  if ! command -v caddy >/dev/null; then
    run sh -c "curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    run sh -c "curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list"
    run apt-get update -y
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
  fi
  email_line=""
  [[ -n "$EMAIL" ]] && email_line="{
	email ${EMAIL}
}
"
  write_file /etc/caddy/Caddyfile 644 <<EOF
${email_line}${DOMAIN} {
	encode zstd gzip
	reverse_proxy 127.0.0.1:${PORT}
}
EOF
  run systemctl enable caddy
  run systemctl reload-or-restart caddy
else
  say "7/9 Caddy: skipped (--no-caddy). Proxy https://your-domain to 127.0.0.1:${PORT} with WebSockets and X-Forwarded-Proto."
fi

# -- 8. firewall -------------------------------------------------------------------------------
if [[ $FIREWALL -eq 1 ]]; then
  say "8/9 Firewall"
  if ! command -v ufw >/dev/null; then run env DEBIAN_FRONTEND=noninteractive apt-get install -y ufw; fi
  run ufw allow OpenSSH            # first, so this session is never locked out
  run ufw allow 80/tcp
  run ufw allow 443/tcp
  run ufw --force enable
  note "the app's user (and so every sandbox) cannot reach the cloud metadata address"
  write_file /etc/systemd/system/catcrew-net.service 644 <<EOF
[Unit]
Description=CatCrewAI: keep the app's user away from the cloud metadata service
After=network-pre.target ufw.service
Before=catcrew.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'iptables -C OUTPUT -m owner --uid-owner ${APP_USER} -d 169.254.169.254 -j REJECT 2>/dev/null || iptables -I OUTPUT -m owner --uid-owner ${APP_USER} -d 169.254.169.254 -j REJECT'
ExecStop=/bin/sh -c 'iptables -D OUTPUT -m owner --uid-owner ${APP_USER} -d 169.254.169.254 -j REJECT 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF
  run systemctl daemon-reload
  run systemctl enable --now catcrew-net
else
  say "8/9 Firewall: skipped (--no-firewall)"
fi

# -- 9. start ----------------------------------------------------------------------------------
say "9/9 Starting CatCrewAI"
run systemctl restart catcrew
if [[ $DRY -eq 0 ]]; then
  for _ in $(seq 1 60); do
    curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1 || die "the app did not start: journalctl -u catcrew -n 50"
  code=$(journalctl -u catcrew --no-pager -n 200 2>/dev/null | grep -o 'first (admin) account: [A-F0-9]*' | tail -1 | awk '{print $NF}')
  say "Done."
  [[ -n "$DOMAIN" ]] && note "Open https://${DOMAIN}"
  if [[ -n "$code" ]]; then
    note "Setup code for the first (admin) account: ${code}"
  else
    note "Accounts already exist (or see the code with: journalctl -u catcrew | grep 'Setup code')"
  fi
  note "Logs: journalctl -u catcrew -f    Update later: sudo ${DIR}/scripts/vps/update.sh"
fi
