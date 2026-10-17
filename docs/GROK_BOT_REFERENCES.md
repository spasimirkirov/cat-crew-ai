# Grok Bot references and how it actually works

Inspiration for Jinx’s multi-bot product. **Primary source: [x.ai/bot](https://x.ai/bot)** and xAI’s own design write-up. Open-source cousins are secondary — steal product shape into Python/PyQt, never their Node stacks.

## Canonical (read these first)

| Link | Why it matters |
| --- | --- |
| **[https://x.ai/bot](https://x.ai/bot)** | **Product page — start here.** AI teammates: many Bots at once, you message them like coworkers, they come back when they need approval. Persistent named agents, not disposable chats. |
| [docs.x.ai/grok-bot/overview](https://docs.x.ai/grok-bot/overview) | **A Bot = one persistent named teammate**, not a chat session. Shared computer, parallel work, MCP + computer-use. |
| [x.ai/news/designing-grok-bot](https://x.ai/news/designing-grok-bot) | **How the UI actually works.** Home is a **Bot roster**, not a chat history. Presence on the avatar. Computer is *theirs*: status → preview → takeover. Chief of Staff coordinates specialists. Group chats for overlapping work. Routines start work without a prompt. |
| [x.ai/news/introducing-grok-bot](https://x.ai/news/introducing-grok-bot) | Positioning: always-on teammates, own computer, 24/7, people often run a chief-of-staff plus specialists. |
| [docs.x.ai/grok-bot/faq](https://docs.x.ai/grok-bot/faq) | Shared computer per *user* (not per Bot). Background work continues if the app is closed. Skills vs routines. |

## Open-source cousins (UX ideas only)

| Link | What it is |
| --- | --- |
| [github.com/CopilotKit/OpenBot](https://github.com/CopilotKit/OpenBot) | Open-source AI coworkers; each gets a computer (browser, files, tools); every action decided before / recorded after. |
| [github.com/hamedgitty/bloks](https://github.com/hamedgitty/bloks) | Local-first desktop workspace for personal AI agents. |
| [github.com/najmuzzaman-mohammad/gawkbot](https://github.com/najmuzzaman-mohammad/gawkbot) | Open-source “Grok Bot” take: bots automate work and spin up micro-apps to review outcomes. |

## How Grok Bot works (from xAI, especially *Designing Grok Bot*)

xAI kept five primitives and hid the rest:

1. **Bots** — persistent agents with identity, memory, runtime, tools.
2. **Chats** — the conversational interface for working with a Bot.
3. **Prompts** — context once, as Skills, or as Routines on a schedule.
4. **Tools** — software, APIs, connectors, shell, computer use.
5. **Artifacts** — documents, designs, code, data the Bots produce.

Then the product rules:

1. **Bots are the main object, not conversations.** Name, avatar, title, own memory, durable thread. Tomorrow you come back to the *same* Bot. Chats are disposable; teammates are not.
2. **You message a Bot like a teammate.** Create one, give it a task, add another when the work grows. No workflow builder required.
3. **Home is a roster** (sidebar of teammates + last line + presence), not Explorer and not a settings catalog.
4. **Presence answers three questions:** who is this, what are they doing, how much do I need to know. Avatar state: idle / thinking / working / waiting / blocked / done. Hover for the current action — not a wall of logs by default.
5. **One shared computer** for all of your Bots (files, shell, logins). Each Bot has a *screen* on that computer. Grok’s computer is a **persistent cloud VM** (`/workspace`) — you do not pick a Windows folder. In Jinx that computer is a **user-chosen disk folder** on the Project (`folders[]`), shown as Files / Terminal / Git.
6. **Computer visibility is three levels, not an always-on IDE:**
   - **Status** — chrome hint while the workspace is active.
   - **Preview** — glance without leaving the chat.
   - **Takeover** — full Files/Terminal when you need to sit down, then hand it back.
7. **Chief of Staff** sits above specialists and routes. You are not the dispatcher. Group/project threads share context; each Bot keeps its own memory. Tools/skills are account-wide; memory stays per Bot.
8. **They only ping you for approval.** Otherwise they finish the job.
9. **Routines** (later): work that starts on a schedule without a prompt.

The design essay’s punchline: as agents take more responsibility, the interface should ask *less* of the person. Dashboards and assignment boards turned the user back into a dispatcher — they cut those.

## What Jinx maps to

| Grok Bot | Jinx |
| --- | --- |
| Bot roster home | **Home** (Projects + Teams); **Members** is the catalog; Project Explorer is Everyone + 1:1 |
| Message a Bot | Composer talks to Chief (Everyone) or a specialist 1:1 |
| Chief of Staff | `Chief` orchestrator (`delegate_to_bot` / `delegate_to_team`) |
| Shared computer | Files + Terminal + Git — **desktop** toolbar = takeover. Grok: one cloud VM per user. Jinx: `Project.folders` on this PC (never the install tree). |
| Start work | Grok: message a teammate (no workflow builder). Jinx: same — optional **Kickoff** on New project is that first message to Chief. Empty kickoff → type in Everyone. |
| MCP / connectors | Settings → Connectors; `global_mcp_tools` vs `bot.mcp_tools` |
| Approvals | Settings → General → Tool approvals + ask_user |
| Catalog | `data/platform/registry.json` |

Local constraint: one llama-server in VRAM, so specialist turns are sequential unless we add a second host. Product *shape* still matches Grok; parallelism is the leftover.

**Computer:** Grok never asks you to pick a Windows folder — every Bot shares one cloud VM. Jinx has no VM, so New project **Computer** is a disk folder stored on the Project. Kickoff is not a Grok wizard; it is “message Chief now.” Routines (work without a prompt) stay later.

**Later (not now):** Orchestrator–Worker — Chief stays Python; Developer/QA may be headless CLI subprocesses (aider, pytest) talking through files + git. See [ORCHESTRATOR_WORKERS.md](ORCHESTRATOR_WORKERS.md).
