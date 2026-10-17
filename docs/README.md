# CatCrewAI

CatCrewAI is a desktop studio where a crew of specialised AI agents (the "cats") works a software project the way a small team would: one turns the brief into a spec, one designs the architecture, one builds, one tests, and so on. You keep the wheel. You talk to **Cat Crew**, the orchestrator: chat freely with the primary model, and when you ask for work it calls the right cats (the whole crew for an app, just Pulse for a bug fix), and you watch each cat appear in the sidebar, work, and report, live, without refreshing anything. You can steer, approve or stop at any point.

The vision is an autonomous but inspectable team. Every specialist runs with a strictly scoped context (its own prompt, its assigned task and the relevant file diffs), every result is written to a task ledger, and every step lands on an activity stream. Nothing happens in a hidden global conversation.

## Tech stack

| Layer | What is used |
| --- | --- |
| Desktop shell | **Tauri 2** (Rust): native window, starts and stops the backend |
| UI | **React 18 + TypeScript + Tailwind CSS + Font Awesome**, built with Vite; state in zustand; tests in vitest |
| API | **FastAPI + uvicorn**: REST under `/api`, one WebSocket at `/ws/events`, loopback only ([API.md](API.md)) |
| Language | Python 3.10+ (backend), TypeScript (UI), Rust (shell) |
| Persistence | SQLite via the standard library (`catcrew.db` operations ledger, `catcrew_state.db` run supervisor), plus a JSON identity catalog (`registry.json`) and JSON chat sessions |
| Model hosts | Cloud only: OpenRouter, OpenAI, Anthropic, Google Gemini, DeepSeek, Groq, Mistral, xAI, each with its own key; CatCrewAI downloads and runs no models |
| Cloud models | DeepSeek, Google Gemini, Groq, Anthropic Claude. Model lists and capabilities are read live from each host |
| Memory | mem0 with an embedded Qdrant store and sentence-transformers embeddings |
| Browser checks | Playwright + headless Chromium, used by Aegis (QA) and Pixel (design) through text-first tools; Chromium is installed on demand the first time it is needed |
| Media and search | Optional local ComfyUI for image generation; DuckDuckGo through `ddgs`, optional SearXNG or Brave |

## Quickstart

Requirements: Python 3.10+, Node 20+, and for the desktop window the Rust toolchain plus the Tauri prerequisites for your OS (on Windows: WebView2 and the MSVC build tools). Model hosts only need a key.

```powershell
git clone <your-remote> cat-crew-ai
cd cat-crew-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cd frontend
npm install
```

Configuration lives in a `.env` file at the repository root. Copy the template and edit only what you need; every entry is optional, and cloud keys are better entered in the app under **Models**.

```powershell
Copy-Item .env.example .env
```

The most useful variables are `CATCREW_HOME` (marketplace tools and app files; default `C:\CatCrewAI` on Windows, `~/CatCrewAI` elsewhere), `CATCREW_PORT` (API port, default 8765), `CATCREW_COMFYUI_URL`, `CATCREW_CONTEXT_TOKENS` (the size at which a chat is summarised, default 8000) and `CATCREW_ALLOWED_ORIGINS` (extra browser origins the API accepts). Command timeout, tool-round cap and tool approvals are runtime settings, not `.env` values.

Database setup needs no separate migration command. The first connection to `data/platform/catcrew.db` creates every table with `CREATE TABLE IF NOT EXISTS` and applies additive `ALTER TABLE ... ADD COLUMN` steps for older files; `data/platform/catcrew_state.db` does the same from `backend/db/state/schema.sql`. Existing rows are never dropped or rewritten. The role-specific tools (Pulse, Forge, Aegis, and the browser set for Aegis and Pixel) are registered and assigned automatically when the engine starts; a tool you unassign on the Tools page stays unassigned. To seed the eight-cat roster with its default skill assignments, run the one-time seeder (idempotent, deliberately not run at start); `seed_role_tools.py` forces every role tool back onto its cats:

```powershell
python scripts/init_catcrew_team.py
python scripts/seed_role_tools.py
```

Run it:

```powershell
# Desktop app: opens the window; the shell starts the backend itself if none is running
cd frontend
npm run tauri dev
# Closing the window (X) hides CatCrewAI to the system tray. Show it again from the tray icon
# (left click, or right click -> Show CatCrewAI). Quit for real from the tray (Quit CatCrewAI) or with
# the power button at the bottom of the sidebar; both stop the backend the app started.

# Or run the two halves yourself (the UI also works in a browser at http://127.0.0.1:1420)
python -m backend               # API on http://127.0.0.1:8765
cd frontend; npm run dev        # UI on http://127.0.0.1:1420
python -m backend --smoke       # start, probe /api/health, exit 0/1
```

Tests: `python -m pytest -q` (backend; see the stale-test note in the backlog), and `cd frontend && npm run typecheck && npm test` (UI). A headless command line front end that uses the same harness is available through `python scripts/catcrew_cli.py`. `python scripts/dev_fake_llm.py` runs an offline OpenAI-compatible fake model on port 1234, useful for trying the whole flow without a real model.

Only one backend runs per `data/` folder (a lock file guards this).

On launch the window shows a "Starting the engine…" screen with a spinner until the backend answers (a few seconds). If the engine cannot start, that screen says why (for example, Python was not found) and offers **Try again**.

## How you use it

The window opens on **Home**, a launcher: your recent projects, **New project** and **Open folder**. It shows no tasks. Create a project with a title, a description and an optional computer folder, or open an existing folder. Opening a project enters the project workspace: the **Cat Crew** chat (the orchestrator, not an agent, so it has no skills or tools to assign) plus one 1:1 thread per cat, and a sidebar with three tabs, **Fleet** (the cats on this project, their live status), **Tasks** and **Changes** (file diffs). Your first message is the work order. The orchestrator staffs cats one by one, each appears in the Fleet as it is called, and each reports back. Activity in the chat is a two-line card that opens into the full step log with **See more**, and a long report from a cat is a short card too (a few lines, then **See more**), so one cat cannot fill the whole screen. Type `@name` to talk to a single cat directly. The **Skills**, **Tools**, **Agents** and **Models** pages manage what the crew knows, can do, who it is, and which model each role uses.

**API keys** (OpenRouter, OpenAI, Anthropic, Google Gemini, DeepSeek, Groq, Mistral, xAI, TypeSafe for Jev's official API, and the optional Brave Search key) are entered on **Models → Hosts → Set key** (the search key under **Other services**). A pasted key goes into a password field, is stored on this computer only (`data/settings.json`), and is never shown again: the UI only says *Key saved*, *Env key* (taken from an environment variable) or *No key*. Once a cloud host has a key it becomes selectable for the roles above.

### Models, roles and tokens

The **Models** page gives each role its own host and model: **Chat** (conversation and dispatching the crew), **Coding**, **Memory**, **Vision**, **Planning** and **Images**. A role you leave on *Not set* follows Chat. The model lists are read live from each host (the refresh button asks the provider again), and every model shows colour-coded badges for what it can do: *Vision*, *Reasoning* and *Tools*. A role given a model that lacks what it works best with (Vision without vision, Planning without reasoning, Coding without tools) shows a warning; it is advice, not a block.

When CatCrewAI opens it checks that every model you chose still exists. If one was retired, renamed or sits on a host that is offline or has no key, a **Model Configuration Notice** lists the roles and models, and **Re-assign Models** opens the Models page with those roles highlighted until you pick another model.

A collapsible **Recommended Hybrid Strategy & Token Optimization** guide on the page suggests spreading the roles over cheap and dear models: a local or light model for Chat, DeepSeek or Claude for Coding, Gemini Flash for Memory, a reasoning model (DeepSeek R1, o1) for Planning only, and Gemini Flash or Qwen-VL for Vision. The engine backs this up: when a chat grows past 8,000 tokens, the Memory model first summarises the older turns, and the costly model receives that summary plus the recent messages. The architecture and distribution phases of a project run on the Planning model, and Aegis and Pixel can ask the Vision model to describe a page screenshot (`browser_look`).

## The crew

| Cat | Role | Responsibility | Typical tools |
| --- | --- | --- | --- |
| **Cipher** | Prompt Engineer | Turns the raw brief into a structured blueprint: goals, constraints, target stack, and what each downstream role needs to know. First phase of every project. | Built-in file tools, `read_skill` |
| **Vanguard** | Team Lead & Architect | System design, APIs, data models, file layout, and the role-by-role todo list. Keeps specialists unblocked. Also runs the distribution phase. | Built-in file tools, task ledger, delegation |
| **Pixel** | UI/UX Designer | Visual hierarchy, layout, Tailwind styling, glassmorphism; fetches design references instead of guessing. Checks its own work in a real browser: layout, contrast and phone/tablet/desktop sizes. | Built-in file tools, `image_search`, the browser tools (`browser_navigate`, `browser_snapshot`, `browser_inspect`, `browser_set_viewport`, `browser_look`, …) |
| **Pulse** | Full-Stack Developer | Implementation: patches, migrations, API routes, third-party integrations. Its build is verified by an automatic build-and-heal loop. | Built-in tools plus `write_file_patch`, `git_diff_inspector`, `read_code_ast` |
| **Aegis** | QA Specialist | Real checks, not chat-history assumptions: serves the app and runs `browser_check`, or smoke-tests the entry point; writes test suites; reports bugs with reproduction steps. A failing QA sends the work back to Pulse. | The browser tools (`browser_navigate`, `browser_snapshot`, `browser_inspect`, `browser_set_viewport`, `browser_click`, `browser_type`, `browser_select_option`, `browser_take_screenshot`, `browser_look`, `browser_close`), `browser_mcp_screenshot`, `http_endpoint_audit`, `run_test_suite` |
| **Lex** | Legal Counsel | Terms of service, privacy policy, licences and compliance research. Never modifies source code. | `web_search`, `web_fetch`, built-in file tools |
| **Blaze** | Marketing & PR | Campaigns, copy, SEO and positioning. Needs Vanguard's approval before publishing statements. | `web_search`, `web_fetch`, built-in file tools |
| **Forge** | DevOps Engineer | Environment bootstrap, CI/CD, Docker, VPS deployment, secrets hygiene. Runs before the build phase. | `execute_terminal_cmd`, `docker_container_status`, `vps_health_check` |

The definitions live in `backend/platform/catcrew.py` (stable ids, so re-running the seeder updates the same people and keeps their photos). Each cat can additionally be given more skills and tools on the Skills and Tools pages (assignment matrices) or the Agents page.

## Repository map

| Path | Contents |
| --- | --- |
| `backend/` | The Python package (`python -m backend`): `api/` (FastAPI, WebSocket), `core/` (chat service, run engine), `platform/` (projects, SOP, runner, bus), `db/` (SQLite), `harness/`, `providers/`, `services/`, `skills/`, `tool_functions/`, `tools.py` |
| `frontend/` | The desktop UI: `src/` (React), `src-tauri/` (Rust shell), `package.json` |
| `docs/` | This documentation and the API contract |
| `todo/` | Technical debt and optimisation backlog |
| `scripts/` | Seeders, the CLI wrapper, the fake model, the runtime wipe |
| `tests/` | Backend tests |
| `legacy/` | Retired packaging scripts (and, on the device, the Mixamo animation assets) kept for reference |
| `data/` | Runtime state (gitignored): DBs, sessions, traces, scratch, memory |
| `backend/identity.md` | The product identity document loaded into every chat turn |

More: [API.md](API.md) for the REST and WebSocket contract, [ARCHITECTURE.md](ARCHITECTURE.md) for how the engine works, and [../todo/TECHNICAL_DEBT_AND_OPTIMIZATIONS.md](../todo/TECHNICAL_DEBT_AND_OPTIMIZATIONS.md) for the audit backlog.
