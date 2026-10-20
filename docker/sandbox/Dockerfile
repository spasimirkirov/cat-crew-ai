# The container a regular user's agents run their commands in (remote mode, services/sandbox.py).
# It sees only that user's workspace, mounted at /workspace. Build it once on the server:
#
#   docker build -t catcrew-sandbox:latest docker/sandbox
#
# Toolchains the cats use: PHP + Composer (Laravel), Node.js + npm, Python, Go, git, SQLite.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    COMPOSER_ALLOW_SUPERUSER=0 \
    npm_config_update_notifier=false \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git unzip zip sqlite3 procps \
        php-cli php-mbstring php-xml php-curl php-zip php-sqlite3 php-mysql php-bcmath php-intl php-gd \
        python3 python3-pip python3-venv \
        golang-go \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer \
    && pip3 install --no-cache-dir --break-system-packages ruff \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Commands run as the host user that owns the workspace (docker run --user), so files stay theirs.
ENV HOME=/tmp/home
RUN mkdir -p /tmp/home && chmod 1777 /tmp/home
WORKDIR /workspace
CMD ["sleep", "infinity"]
