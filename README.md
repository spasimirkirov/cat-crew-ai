# cat-crew-ai

**CatCrewAI** is a multi-agent desktop studio: a crew of eight specialised AI agents (Cipher, Vanguard, Pixel, Pulse, Aegis, Lex, Blaze, Forge) that plans, builds, tests and ships a project as a team, while you watch every step live and stay in control. A Python FastAPI backend does the work; a Tauri + React window shows it.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env      # optional overrides
python scripts/init_catcrew_team.py
cd frontend
npm install
npm run tauri dev                # opens the desktop window and starts the backend
```

Or in any browser, without the desktop shell (browser mode): one command builds the web UI the
first time and opens `http://127.0.0.1:8765` (or a free port):

```powershell
.\scripts\start.ps1             # Windows: tray icon, no console (-Rebuild, -Shortcut, -Console)
scripts/start.sh                # Linux / macOS  (--rebuild)
python -m backend --ui          # the same by hand in a console, once frontend/dist is built
```

On Windows it runs in the **tray**: closing the browser tab leaves it running, the cat icon near the
clock opens it again (double-click or "Open CatCrewAI") and "Stop CatCrewAI" shuts it down; starting
it again just opens the browser. `-Shortcut` adds a desktop shortcut; logs go to
`data\logs\backend.log`. **Power off** in the sidebar also stops the engine. No login locally.

### On a server (VPS)

```bash
git clone <this repo> && cd cat-crew-ai
sudo scripts/vps/setup.sh --domain cats.example.com --dry-run   # read what it will do
sudo scripts/vps/setup.sh --domain cats.example.com             # do it
sudo /opt/cat-crew-ai/scripts/vps/update.sh                     # later: update
```

It installs the toolchains and bubblewrap, runs the app as its own `catcrew` user behind Caddy with
HTTPS (`python -m backend --ui --remote` on 127.0.0.1), opens only SSH/HTTP/HTTPS, and prints the
setup code for the first (admin) account. Further accounts come from the admin's invite links;
regular users' agents run sandboxed with bubblewrap and see only their own workspace.

Documentation lives in [`docs/`](docs/):

| Document | What it covers |
| --- | --- |
| [docs/README.md](docs/README.md) | Overview, tech stack, quickstart, the eight-cat catalog, repository map |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Processes and threads, the live event stream, context isolation, the SOP, thread-safe SQLite, the skill and tool engine, the frontend |
| [docs/API.md](docs/API.md) | The REST and WebSocket contract the UI is built on |
| [todo/TECHNICAL_DEBT_AND_OPTIMIZATIONS.md](todo/TECHNICAL_DEBT_AND_OPTIMIZATIONS.md) | Audit findings and the prioritised backlog |

Agent and contributor rules are in [AGENTS.md](AGENTS.md).
