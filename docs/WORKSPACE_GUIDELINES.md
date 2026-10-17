# CatCrewAI workspace and system guidelines (post-pivot)

These replace the PySide6 rules in the earlier project instructions. Paste this text into the Project's instructions.

## Role and system context

You are assisting in the development and maintenance of **CatCrewAI**: an autonomous multi-agent desktop studio. The backend is Python (FastAPI + WebSocket + SQLite). The desktop UI is **Tauri 2 + React + TypeScript + Tailwind + Font Awesome**. There is no Qt/PySide6/PyQt code any more.

## 1. Repository identity and naming

- Project `CatCrewAI`, root folder `cat-crew-ai`, Python package `backend`, env vars `CATCREW_*` (`CATCREW_HOME`, `CATCREW_PORT`, `CATCREW_ALLOWED_ORIGINS`), marketplace tools dir `catcrew_marketplace_tools`.
- Never generate code, variables or paths that use the retired pre-rename name.

## 2. Architecture

- Layout: `backend/` (`api/`, `core/`, `db/`, `platform/`, `skills/`, `tool_functions/`, plus `harness/`, `providers/`, `services/`, `tools.py`), `frontend/` (`src/`, `src-tauri/`), `docs/`, `todo/`, `scripts/`, `tests/`, `legacy/` (retired material).
- The frontend is a pure client of `docs/API.md`: REST under `/api`, one WebSocket at `/ws/events`, loopback only, Host/Origin allow-listed. No endpoint returns a secret.
- 8 core cats: Cipher (prompt engineering), Vanguard (lead/architect), Pixel (UI/UX), Pulse (developer), Aegis (QA), Lex (legal), Blaze (marketing), Forge (DevOps).
- Context isolation: a specialist receives only its system prompt, its assigned task and the relevant file diffs. Never leak global conversation history into a specialist prompt.
- Dual skill/tool engine: Skills are Anthropic-style `SKILL.md` playbooks, lazy-loaded through `read_skill`. Tools are Letta-style Python functions converted to JSON schemas for native function calling. "Hermes auto-building" is the auto-skill builder (`platform/auto_skill.py`): it proposes playbooks that a person approves.
- Database: SQLite (`catcrew.db`). Migrations are additive only (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN`). Never drop or rewrite tables.
- Live state: every writer publishes to `platform/bus.py`; the API fans events out over `/ws/events`; the UI applies them in `frontend/src/state/reducer.ts`. Publishing must never happen while holding a lock that a subscriber could need.

## 3. UI rules (React / Tailwind)

- Palette: background `#0B0D12`, cards `#13161F` with a 1px `#222634` border, accents cyan `#38BDF8`, emerald `#34D399`, violet `#A78BFA`, muted text `#8B92A8`, font Inter. Icons are Font Awesome.
- **Home is a launcher only** (recent projects, New project, Open folder). It never shows tasks. Tasks live in the project workspace (Fleet / Tasks / Changes sidebar tabs next to the Cat Crew chat) and in in-chat execution cards.
- Real-time pop-in: a cat appears in the Fleet sidebar the moment `bot.staffed` arrives; no refresh.
- Activity, trace and diff cards are clamped to two lines (`line-clamp-2`) with a "See more" toggle for the full log. Long message text goes through `CollapsibleText` (work reports collapse after 5 lines, conversation after 12): never render a cat's full output unclamped in the chat.
- Timestamps: "X mins ago" / "X hours ago" under 24 h, an exact date after.
- Window lifecycle: the Tauri window is created hidden with the app background colour and shown by the UI after its first painted frame (`frontend/src/lib/window.ts`); the Rust shell shows it after 4 s as a failsafe. Never show or create windows from constructors before render.
- Start-up: never show "cannot reach the backend" while the engine is starting. The window shows `StartupScreen` (spinner) behind `BootGate` until the first bootstrap has loaded; `runBoot` retries quietly and only reports a failure when the shell (`backend_status`) or the backend says so, or after 30 s. Do not add page-level "backend down" errors for the launch phase.
- Tray and close-to-tray: the window's X hides to the system tray (`WindowEvent::CloseRequested` is prevented in `frontend/src-tauri/src/lib.rs`); the tray menu (Show / Quit), a left click on the tray icon and the sidebar Power Off button (`quit_app`, `frontend/src/components/PowerOff.tsx`) are the only ways to show or really quit the app. Quitting always goes through the one idempotent `quit()` in `lib.rs`, and never blocks the UI thread.

## 4. Code deliverables

- Modular, production-ready Python, SQL, TypeScript and Rust.
- Thread safety: never run blocking LLM or database work on the API event loop; use worker threads and the event bus.
- Checks: `python -m compileall -q backend`, `python -m pytest -q`, and `cd frontend && npm run typecheck && npm test && npm run build`.
- Never print, log or commit secrets (`.env`, `data/settings.json`). Keep `data/` and `.env` out of any sync.
