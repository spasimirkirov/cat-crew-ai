# Jinx AI — product bible

This is the source of truth for **what Jinx is now** and **how to build it**. This document is loaded into every chat turn. If you are Jinx, or a Cursor agent in a new session, read this before changing the app.

UI, logs, comments, README, and this document are English. The person using the app may speak Bulgarian in chat.

---

## One-line product

Jinx is a **Windows desktop multi-bot studio** for **local AI agents** that code and run autonomous tasks, plus an **AI Assistant** toolset (web, images via `generate_image`, voice, overlay). You define **Team Members** (bots), group them into **Teams**, start **Projects**, then work inside a **Project Explorer** (1:1 chat with a bot, or a group chat with everyone on the project). The coding/search/git/image **tools already in this repo stay**. The old “Assistant vs Developer landing + session-first chat” is gone.

Not a companion app. Not a VS Code clone as the home screen.

## Workflow (this is the product)

```
Launch
  → HOME
       Projects  — open / new / edit
       Teams     — open / new / edit (add/remove members)
  → TEAM MEMBERS (catalog)
       define bots: name, avatar, role, skills, short bio, prompt
  → NEW PROJECT (title, description, optional Computer folder, optional teams, optional Kickoff)
  → PROJECT EXPLORER
       assign teams and/or standalone bots
       group chat (“Everyone”) + 1:1 chat per bot
       Computer takeover = Files / Terminal / Git / DB (not the landing screen)
```

1. **Wipe / fresh start.** Old chats and session workspaces are discarded. Catalog starts empty except **Chief of Staff**. Official built-in team: `python scripts/init_arcane_team.py` (Arcane). Marketplace seed remains optional (`python scripts/init_marketplace_bots.py`).
2. **Team Members** — the people. Each bot has: name, optional circular photo (crop in the create modal; otherwise a generic role glyph), role (Lead through Morale Specialist — fills default title, skills, and prompt), optional bio, system prompt, optional MCP/tool pack. You create them *before* or *while* staffing a team.
3. **Home** — first screen after launch. Two boards:
   - **Projects** — Open, New, Edit details.
   - **Teams** — Open a team (see members), add/remove bots from the catalog, edit team name/description.
4. **New project** — title, description, optional **Computer** (a folder on this PC; never the Jinx install tree), optional **Teams** checkboxes, optional **Kickoff** (your first message to Chief in Everyone). Starting it opens **Project Explorer**. Empty kickoff means you type in Everyone yourself. Grok Bot does not pick a Windows folder — its computer is a cloud VM; Jinx’s computer is this folder.
5. **Project Explorer** — the work surface:
   - Left: **Everyone** (group chat) + each assigned bot.
   - Add a whole **Team** or a **standalone bot** from the catalog.
   - Click a bot → 1:1 thread (that bot’s prompt + tools).
   - Click Everyone → group thread (**Chief of Staff** coordinates; may `delegate_to_bot`). Kickoff and later messages in Everyone are work orders — Chief should `update_todo` then delegate, not quiz. `ask_user` is only for a real blocking decision.
   - **Computer** (desktop glyph / Files) is their shared machine — status → takeover, then back to the project.

Settings and **AI Models** stay global (brains, keys, connectors, overlay look). They are not the home screen.

## What we keep (do not rewrite)

These already exist in Python and are the reason we did not start a new repo:

| Area | Where |
| --- | --- |
| Agent loop + tools | `jinx_ai/tools.py`, `jinx_ai/providers/` |
| Files, shell, git, DB, Terminal | explorer / `git_ops` / `db_*` / `terminal.py` |
| llama-server + GGUF Hub + cloud hosts | `lm_*`, `llama_server.py`, `api_access.py` |
| Connectors / MCP deferred | `jinx_ai/connectors/`, `harness/deferred.py` |
| Skills playbooks | `jinx_ai/skills/`, Settings → Skills |
| Approvals, ask_user, TODO, subagent, code mode | `jinx_ai/harness/` |
| Overlay body | `jinx_ai/body/`, `docs/body.md` |
| Chief + delegate_to_bot | `jinx_ai/platform/` |
| Catalog store | `data/platform/registry.json` |

## What we removed (do not bring back)

- Assistant vs Developer as the **landing** (no Chat-tab-first mode).
- Home = Explorer + Terminal.
- Binding a project as an afterthought on a hidden Teams catalog.
- Session-first “Jinx AI - Session - timestamp” as the main object. Threads belong to a **project** (`project_id` + `bot_id`; `bot_id=chief` means group chat).

## Data model

Stored in `data/platform/registry.json`:

| Object | Meaning |
| --- | --- |
| **Bot** (Team Member) | Identity: `id`, `name`, `role` (fills default title/skills/prompt), optional `description` (bio), circular `avatar` (or a generic role glyph), `skills[]`, `system_prompt`, tool/MCP allowlists |
| **Team** | Named group: `bot_ids`, optional `lead_bot_id` |
| **Project** | Unit of work: `name`, `description`, `team_ids`, `bot_ids` (standalone), **`folders[]` (shared computer)** |
| **Chief** | Singleton orchestrator, not a team member. Group chat talks to Chief. |

Chat transcripts: `data/sessions/<id>.json` with `project_id` + `bot_id`. Scratch: `data/scratch/<session-id>/`. **Project.folders** is the shared computer for Everyone + 1:1; threads copy it on open. Changing Files roots writes back to the project. Never the Jinx install tree. No folder → tools use session scratch.

**Shared vs private.** Bots share the disk folder, `Project.description`, `state` (including kickoff), recent Everyone/delegation memory, and `AGENTS.md` / `SYSTEM.md` / `README` in that folder. Teammate data is a lookup, not prompt stuffing: **`list_teams`** → **`list_members`** (`team_id`) → **`get_member`** (`bot_id`) for the dossier (name, title, role, bio, photo). **`list_roster`** dumps every card when you need the whole page. 1:1 transcripts stay private. Everyone talks to **Chief of Staff**. Arcane is the official built-in team.

## Brains (unchanged)

**AI Models → General** picks Chat / Coding / Memory / Images. Local llama-server `127.0.0.1:8787`. Optional DeepSeek / Gemini / Groq on Cloud API. Search: DuckDuckGo; images: hidden ComfyUI. Command timeout and optional tool approvals: Settings → General. Tool rounds default to **unlimited** (0); Stop ends the turn. Voice is a **call**: phone button starts listening, leave the call hangs up. No Hey Jinx wake word.

## Later (not this slice)

CLI workers (aider / pytest as subprocesses, file-based handoff): `docs/ORCHESTRATOR_WORKERS.md`. Grok Bot UX notes: `docs/GROK_BOT_REFERENCES.md`. Do not port Node stacks.

## UI chrome

Dark acrylic, cyan `#22d3ee` / pink `#f472b6`. Font Awesome only (`jinx_ai/icon.py`). Compact on/off = `IconToggle`. Traffic lights Apple circles. Skill: `.cursor/skills/jinx-ui/SKILL.md`.

Activity bar:

1. **Home**
2. **Members** (Team Members catalog)
3. **Project** (explorer — only meaningful once a project is open)
4. Files / Git / DB — Computer, when a project is open
5. AI Models, Settings
6. Power

Chat activity is a transcript line, not a status card: **Worked for 10m 32s** with a chevron. Click to expand the tool steps. Everyone is Chief of Staff.

## Agent coding rules (this repo)

Small patches. Tools first. `search_files` is not Python-only. `update_todo` for harness checklists. Prefer `run_subagent` / `run_code_mode` when cheaper. Never paste tool-call JSON into chat. After code: `python -m compileall -q jinx_ai` then `python -m pytest -q`.

## Layout of this tree (short)

`jinx_ai/chat_window.py` — shell. `home_page.py` — Home boards. `members_page.py` — bot catalog. `platform_page.py` — project roster. `platform/` — models + Chief + runner. `tools.py` — tool implementations. Overlay: `body/`.
