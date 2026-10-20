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

Or in any browser, without the desktop shell (browser mode):

```powershell
cd frontend; npm install; npm run build; cd ..
python -m backend --ui           # serves the app on 127.0.0.1 (8765, or a free port) and opens it
python -m backend --ui --port 0  # any free port; --no-browser to only print the address
```

The page and the API share one loopback origin, and **Power off** in the sidebar stops the engine.
On a server, bind a network address: `python -m backend --ui --host 0.0.0.0` turns on accounts.
The console prints a setup code for the first account, which becomes the admin; further accounts
come from the admin's one-time invite links (Users page). Put a TLS proxy (Caddy, nginx) in front:
never serve it over plain HTTP on the internet.

Documentation lives in [`docs/`](docs/):

| Document | What it covers |
| --- | --- |
| [docs/README.md](docs/README.md) | Overview, tech stack, quickstart, the eight-cat catalog, repository map |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Processes and threads, the live event stream, context isolation, the SOP, thread-safe SQLite, the skill and tool engine, the frontend |
| [docs/API.md](docs/API.md) | The REST and WebSocket contract the UI is built on |
| [todo/TECHNICAL_DEBT_AND_OPTIMIZATIONS.md](todo/TECHNICAL_DEBT_AND_OPTIMIZATIONS.md) | Audit findings and the prioritised backlog |

Agent and contributor rules are in [AGENTS.md](AGENTS.md).
