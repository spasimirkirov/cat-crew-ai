# CatCrewAI — product identity

This is the source of truth for **what CatCrewAI is** and **how to change it**. This document is loaded into every chat turn. If you are CatCrewAI, or an agent in a new session, read it before changing the app.

UI, logs, comments and this document are English. The person using the app may speak Bulgarian (or any other language) in chat.

---

## One-line product

CatCrewAI is a **desktop multi-bot studio** for local and cloud **AI agents** that plan, code, review and ship projects as a team, plus an **AI Assistant** toolset (web search, images via `generate_image`). It is a **Python FastAPI backend** (`backend/`, `python -m backend`) with a **Tauri + React + Tailwind** front end (`frontend/`) that talks to it over REST and one WebSocket (`/ws/events`). You define **Team Members** (bots), start **Projects**, then work inside a project: talk to **Cat Crew**, the orchestrator and system chat (a free conversation with the primary model that calls the specialist cats when you ask for work), or open a 1:1 thread with a single cat.

It is not a companion app and not a VS Code clone.

## The crew

Eight system specialists are defined in `platform/catcrew.py` and seeded with `python scripts/init_catcrew_team.py`. Each has its own prompt, tool allowlist and skills.

| Cat | Role id | Job |
| --- | --- | --- |
| Cipher | `prompt_engineer` | Turns the brief into a precise spec and prompts |
| Vanguard | `vanguard` | Lead and architect: structure, distribution, delegation |
| Pixel | `designer` | UI/UX design and visual polish |
| Pulse | `developer` | Implementation; verifies and self-heals its own code |
| Aegis | `qa` | Tests, reviews, headless browser checks |
| Lex | `legal` | Licences, privacy and compliance |
| Blaze | `marketing` | Copy, positioning and launch material |
| Forge | `devops` | Environment, build, CI and deployment |

## Workflow

```
Launch -> Home (a launcher: recent projects, New project, Open folder; no tasks)
       -> New Project (title, description, optional computer folder, optional kickoff)
       -> project workspace (the Cat Crew chat + one thread per cat; the Fleet, Tasks and Changes tabs)
```

**Cat Crew** is not an agent: it has no skills or tools assigned and uses built-in routing. A question or remark to it is a normal chat; a request for work is classified automatically and handed to the right cats (the whole pipeline for an app, only the needed roles otherwise); it can also hand work over itself with `hand_off_to_crew`. Cats pop into the fleet the moment the orchestrator staffs them. The SOP pipeline (`platform/sop.py`) runs the phases in order: blueprint (Cipher), architecture and distribution (Vanguard), devops (Forge), build (Pulse), design (Pixel), QA (Aegis). Lex and the accountant join by keyword. If QA fails, Pulse fixes and Aegis re-checks. `@name` in a message routes straight to that cat.

Every delegated run gets a **fresh, isolated context**: the cat's system prompt, its assigned task and the relevant file diffs. Global conversation history is never pasted into a specialist prompt. Each cat ends its turn by calling `report_task_results`, which records the result and appears as a task result in the project.

## Skills and tools

- **Skills** are Markdown playbooks (`SKILL.md` with frontmatter) under `backend/skills/`. Only a short index is in the prompt; load one with `read_skill <name>` when the task matches. The auto-skill builder (`platform/auto_skill.py`) proposes new playbooks after a task; a person approves them on the Skills page.
- **Tools** are Python functions turned into JSON schemas for native function calling (`backend/tool_functions/`). Their metadata lives in `catcrew.db` (`tools`, `agent_tools`); handlers are resolved from `module:function` paths. Marketplace tools import into `CATCREW_HOME/catcrew_marketplace_tools`.
- Connectors / MCP tools are deferred: they are registered per turn only when an agent is allowed to use them.

## Where things live

| Area | Location |
| --- | --- |
| API (REST + WebSocket) | `backend/api/` (contract: `docs/API.md`) |
| Chat turns and live events | `backend/core/chat_service.py`, `backend/platform/bus.py` |
| Agent loop and built-in tools | `backend/tools.py`, `backend/providers/` |
| Harness (approvals, ask_user, TODO, subagents, events) | `backend/harness/` |
| Teams, projects, SOP, delegation | `backend/platform/` |
| Operations ledger | `data/platform/catcrew.db` |
| Run supervisor state | `data/platform/catcrew_state.db` |
| Bot / team / project catalog | `data/platform/registry.json` |
| Chat transcripts | `data/sessions/<id>.json` |
| Settings | `data/settings.json` |

Environment variable `CATCREW_HOME` (default `C:\CatCrewAI` on Windows, `~/CatCrewAI` elsewhere) points at models and marketplace assets. Never write generated project files into the CatCrewAI install tree; use the project's computer folder, or the session scratch folder when none is set.

## Brains

**Models** picks a model per role: Chat, Coding, Memory, Vision, Planning and Images (an unset optional role follows Chat). Every model runs on a cloud host with its own key: OpenRouter (every vendor through one key), OpenAI, Anthropic, Google Gemini, DeepSeek, Groq, Mistral or xAI. Images can be Off. Model lists and capabilities come live from each host, and a long chat is summarised by the Memory model before it reaches a costly one. Search is DuckDuckGo; images use ComfyUI.

## UI

Tauri shell + React 18 + Tailwind. Background `#0B0D12`, cards `#13161F` with a 1px `#222634` border, accents cyan `#38BDF8`, emerald `#34D399`, violet `#A78BFA`, muted `#8B92A8`, Inter. Font Awesome icons only. Pages: Home, Skills, Tools, Agents, AI Models, and the project workspace (Cat Crew chat + Fleet / Tasks / Changes). Every activity card in a chat is clamped to two lines with a "See more" toggle for the full log, trace or diff. The UI holds no business logic: it reads REST and applies WebSocket events.

## Agent coding rules (this repo)

Prefer small patches. Tools first. `search_files` is not Python-only. Use `update_todo` for checklists. Prefer `run_subagent` / `run_code_mode` when cheaper. Never paste tool-call JSON into chat. Database changes are additive only (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN`). After code changes run `python -m compileall -q backend` then `python -m pytest -q`.

## Layout (short)

`backend/api/` is the HTTP/WebSocket surface; `backend/core/` the chat service and message shapes; `backend/platform/` the models, Chief fallback, SOP and runner; `backend/db/` the SQLite ledger; `tools.py` the tool implementations; `frontend/src/` the React app and `frontend/src-tauri/` the native shell.
