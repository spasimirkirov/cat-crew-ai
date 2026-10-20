#!/usr/bin/env bash
# Start CatCrewAI in your browser (no desktop shell needed).
#
#   scripts/start.sh             # builds the web UI the first time, then opens http://127.0.0.1:8765
#   scripts/start.sh --rebuild   # rebuild the web UI first (after pulling new code)
#
# The desktop app still works too: cd frontend && npm run tauri dev
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "Creating the Python environment (.venv) ..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
if [[ "${1:-}" == "--rebuild" || ! -f frontend/dist/index.html ]]; then
  echo "Building the web UI ..."
  (cd frontend && { [[ -d node_modules ]] || npm install; } && npm run build)
fi
echo "Starting CatCrewAI (Ctrl+C or Power off in the sidebar stops it) ..."
exec .venv/bin/python -m backend --ui
