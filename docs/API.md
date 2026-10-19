# CatCrewAI Backend API (v1)

The Python backend (`backend/`) is a FastAPI server. The Tauri + React frontend
(`frontend/`) is a pure client of this API: it holds no orchestration logic.

- Base URL: `http://127.0.0.1:8765` (override with `CATCREW_PORT`, `CATCREW_HOST`).
- REST prefix: `/api`. WebSocket: `/ws/events`.
- JSON everywhere. Timestamps are ISO-8601 strings with an offset
  (`2026-09-19T15:51:09+00:00`; older ledger rows use a trailing `Z`). Always parse
  them with `new Date(...)`. The UI turns them into "N mins ago" (under 24 h) or an
  exact date.
- Local-only. The server binds to loopback and rejects requests whose `Origin` (or
  `Host`) is not allow-listed (Tauri origins `tauri://localhost`,
  `http://tauri.localhost`, and the dev origins `http://localhost:1420`,
  `http://127.0.0.1:1420`; extend with `CATCREW_ALLOWED_ORIGINS=a,b`). Requests with
  no `Origin` (curl, Rust) are allowed.
- Errors: `{"detail": "human readable message"}` with the matching HTTP status.
  `409` means "not while a turn is running" (project reset/delete) or a duplicate id; `404` unknown id; `403` forbidden (core cats, bundled playbooks); `422` bad input.
- Secrets (API keys, tokens) are never returned by any endpoint.

## 1. Shapes

```ts
type Status = "idle" | "thinking" | "coding" | "testing" | "chatting";
type BusyState = "working" | "idle";

interface Agent {                // a cat specialist (bot)
  id: string;                    // "pulse"
  name: string;                  // "Pulse"
  role: string;                  // "developer"
  title: string;
  description: string;
  avatar: string;                // "" or a URL path served by the API: "/api/agents/{id}/avatar"
  color: string;                 // hex accent, e.g. "#22d3ee"
  skills: string[];              // free-form keyword tags from the agent definition
  playbooks: string[];           // names of assigned Skills (Playbooks), incl. global ones
  tools: string[];               // builtin tool names
  letta_tools: string[];         // names of assigned Letta-style function tools, incl. global ones
  mcp_tools: string[];
  essential_tools: string[]; // read-only: built-ins of the role + essential registry tools; always attached, never removable
  max_rounds: number;
  is_system: boolean;            // the 8 core cats
  system_prompt?: string;        // only on GET /agents/{id}
}

interface Activity {             // live per-agent state
  bot_id: string;
  status: Status;                // display word for the status pill
  state: BusyState;
  text: string;                  // one-line "what is it doing" ("Editing src/App.jsx")
  updated_at: string | null;
}

interface RosterEntry {          // one card in the sidebar fleet
  bot_id: string;                // "crew" is the Cat Crew entry (always first)
  agent: Agent;                  // for "crew": name "Cat Crew", title "Orchestrator", role "room",
                                 //   avatar "/api/agents/crew/avatar" (the bundled default.png),
                                 //   empty playbooks / letta_tools — it is NOT an agent
  is_room: boolean;
  activity: Activity;
  joined_at: string | null;      // when staffed (null for the room / seeded members)
}

interface Project {
  id: string; name: string; description: string;
  folders: string[];             // first entry is the active workspace
  bot_ids: string[];             // persisted roster
  created_at?: string;
  phase: string;                 // last SOP phase: "" | "blueprint" | ... | "done"
}

interface Task {                 // ALWAYS project scoped
  id: string; project_id: string;
  assigned_to: string;           // agent id
  created_by: string;            // "lead" | "user"
  title: string; description: string;
  status: "pending" | "in_progress" | "blocked" | "failed" | "unverified" | "done";
  created_at: string; updated_at: string;
  latest_summary: string;        // the specialist's report_task_results text ("" until reported)
}
interface TaskResult { id: string; task_id: string; final_state: string; summary: string; created_at: string; }
interface FileChange { id: string; task_id: string; project_id: string; agent_id: string;
                       file_path: string; diff_uuid: string; created_at: string; }

interface ActivityStep { line: string; preview?: string; }
interface Message {              // one chat transcript entry
  id: string;
  role: "user" | "assistant" | "system" | "error";
  speaker?: string;              // bot display name for assistant turns in the room
  bot_id?: string;               // who produced it (assistant turns)
  text: string;
  created_at: string;
  phase?: string;                // SOP phase this turn belongs to
  tool_events?: string[];        // one short line per tool call
  activity?: { elapsed_s: number; steps: ActivityStep[] };  // renders as an ExecutionCard
}

interface Skill {                // Playbook (SKILL.md)
  id: string; name: string; description: string;
  type: "bundled" | "custom" | "marketplace" | "auto_generated";
  version: string; target_roles: string[];
  playbook_path: string;         // repo-relative, forward slashes (never an absolute path)
  source_url: string;
  is_global: boolean;            // attached to every agent
  agent_ids: string[];           // agents this skill is assigned to
  locked_agent_ids: string[];    // cats whose built-in bundle holds it (platform/essentials.py)
  body?: string;                 // markdown, only on GET /skills/{id}
}
interface Tool {                 // Executable (Letta-style Python function tool)
  id: string; name: string; description: string;
  handler: string;               // "backend.tool_functions.pulse_tools:write_file"
  is_global: boolean; agent_ids: string[];
  locked_agent_ids: string[];    // cats whose built-in bundle holds it
  schema?: object;               // JSON schema, only on GET /tools/{id}
}
```

## 2. REST endpoints

### Meta
| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/health` | – | `{status:"ok", version, busy:boolean}` |
| GET | `/api/bootstrap` | – | `{agents:Agent[], projects:Project[], activity:Activity[], current_turn:Turn\|null, turns:Turn[]}` (one call at app start; `turns` lists every running turn, one per project lane, and `current_turn` is the most recently started of them; `agents` are the specialist cats only; `activity` also includes the `crew` entry) |
| POST | `/api/shutdown` | – | `202` graceful exit (stops the ComfyUI child, releases the instance lock). Called by the desktop shell when the app really quits (tray "Quit CatCrewAI" or the sidebar Power Off button, i.e. the `quit_app` Tauri command) — closing the window with X only hides it to the system tray. |

### Projects (Home = launcher: list / create / open / choose a workspace. No tasks here.)
| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/projects` | – | `Project[]` |
| POST | `/api/projects` | `{name, description?, folders?: string[], bot_ids?: string[], kickoff?: string}` | `201 Project`. A non-empty `kickoff` is sent to Cat Crew like any first message. |
| GET | `/api/projects/{pid}` | – | `Project` |
| PATCH | `/api/projects/{pid}` | `{name?, description?}` | `Project` |
| DELETE | `/api/projects/{pid}` | – | `204` |
| PUT | `/api/projects/{pid}/workspace` | `{path: string}` | `Project` (sets/creates the computer folder; `422` if it is not a directory) |
| POST | `/api/projects/{pid}/reset` | – | `Project` (clears room chat + shared state; bots back to idle) |
| GET | `/api/workspaces/browse?path=` | – | `{path, parent, entries:[{name, path, is_dir}]}` (folder picker for the launcher) |
| POST | `/api/workspaces/folders` | `{parent, name}` | `201` and the new folder's listing (same shape as browse). `name` is one folder name: `422` for an empty, over-long or invalid name (`\ / : * ? " < > |`, `.`/`..`, trailing dot or space, Windows reserved names) or an empty `parent` (the drive list); `409` if it exists; `403` without permission. |

### Fleet (sidebar)
| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/projects/{pid}/roster` | – | `RosterEntry[]` — Cat Crew first, then the persisted roster, then bots the orchestrator staffed mid-run |
| POST | `/api/projects/{pid}/roster` | `{bot_id}` | `201 RosterEntry` (kept for scripts; the UI no longer offers "Add a cat": cats join and leave through orchestration events) |
| DELETE | `/api/projects/{pid}/roster/{bot_id}` | – | `204` |

### Agents
`GET /api/agents` → `Agent[]` · `GET /api/agents/{id}` → `Agent` (with `system_prompt`) ·
`GET /api/agents/{id}/activity` → `Activity` · `GET /api/agents/{id}/avatar` → image (`404` if none). The crew is read-only: the core cats are defined in `platform/catcrew.py` and re-synced on every start, so there is no `POST` / `PUT` / `DELETE` (they answer `405`). `crew` / `chief` are accepted by `GET /api/agents/{id}` (the Cat Crew card), `…/activity` and `…/avatar` (default.png).

`GET /api/cat-profiles` → `CatProfile[]` without prompts: `{id, name, role, allowed_tools[], avatar_icon}`; `allowed_tools` is what that cat's turn is bound to (built-in pack, essentials first, plus assigned registry tools).

### Tasks (only under a project)
| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/projects/{pid}/tasks` | – | `Task[]`, oldest first. Work-order fields: `kind` (build/bug/plan/fix/review), `parent_id`, `acceptance`, `depends_on` (string[]), `attempt`, `reported_by` |
| POST | `/api/projects/{pid}/tasks` | `{assigned_to, title, description?}` | `201 Task` (also staffs the agent) |
| GET | `/api/projects/{pid}/tasks/{tid}` | – | `Task & {results: TaskResult[]}` |
| PATCH | `/api/projects/{pid}/tasks/{tid}` | `{status}` | `Task` |
| GET | `/api/projects/{pid}/changes?limit=8` | – | `FileChange[]`, newest first |
| GET | `/api/diffs/{diff_uuid}` | – | `{diff_uuid, file_path, agent_id, diff_content, created_at}` |

### Chat
A project has one **thread per cat** plus **Cat Crew** (`bot_id = "crew"`; the legacy id
`"chief"` is accepted everywhere and read as `"crew"`, old sessions included).

Cat Crew is the unconstrained **Orchestrator / System Chat**: the human talks to the primary
model directly to steer progress, analyse requirements and trigger the specialist cats. It is not
an agent: it has no tool/skill assignments and never appears in the Skills / Tools matrices; it
uses the engine's built-in routing. Every message to it is classified server-side
(`platform/orchestrator.py`, no LLM call) and starts a turn with a `mode`:

| `mode` | When | What runs |
| --- | --- | --- |
| `chat` | a question, remark, greeting or planning talk | one free-form model turn, read-only lookup tools, and `hand_off_to_crew` |
| `work` | a request to build / fix / design / test / deploy / write… | the SOP with the matching specialists: a whole app or nothing specific → the full pipeline (blueprint → architecture → distribution → devops → build → design → qa); otherwise only the roles the request calls for (e.g. "fix the login bug" → Pulse) |
| `mention` | the message contains `@name` of a cat | that cat directly |

In a `chat` turn the model may call `hand_off_to_crew(brief, roles?)`. The call only records the
request; when the chat turn ends the server starts a normal `work` turn (`turn.started` with
`mode:"work"`), so every specialist streams over the WebSocket exactly like an auto-routed one.
The brief is not shown as a user message. Writing in a cat's thread is a direct 1:1 turn.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/projects/{pid}/threads` | – | `[{bot_id, title, message_count, last_message_at}]` |
| GET | `/api/projects/{pid}/threads/{bot_id}/messages?limit=200` | – | `Message[]` (oldest first) |
| POST | `/api/projects/{pid}/threads/{bot_id}/messages` | `{text}` | `202 {turn_id, queued:false}`; if a turn is running the text is queued: `202 {turn_id:null, queued:true, position}` |
| POST | `/api/projects/{pid}/threads/{bot_id}/report` | `{text}` | Report a problem (`bot_id` = the cat whose work is wrong, or `crew`). Same `202` shape as messages. Starts a bug turn in the Cat Crew room: the lead writes a fix plan (`create_fix_task`), the cats fix it, QA verifies. The turn's `mode` is `bug`; phases are `fixplan`, `fix`, `qa`, `fixreport` |
| GET | `/api/turns` | – | `Turn[]`: every running turn, one per project lane, oldest first. Up to `CATCREW_PARALLEL_PROJECTS` projects (default 1) run at once; a project always runs one turn at a time and queues the rest. |
| GET | `/api/turns/current` | – | `Turn \| null` (the most recently started running turn) where `Turn = {id, project_id, bot_id, started_at, phase, kind:"room"\|"direct", mode:"chat"\|"work"\|"mention"\|"direct"}` |
| POST | `/api/turns/{turn_id}/abort` | – | `202` |
| POST | `/api/projects/{pid}/retry` | – | `202` `{turn_id, queued}`: runs again the last message whose model was turned away (after `POST /api/models/replace`); `404` when there is nothing to retry |
| POST | `/api/turns/{turn_id}/steer` | `{text}` | `202` (folded into the next phase / model round) |
| POST | `/api/approvals/{approval_id}` | `{decision: "allow"\|"allow_always"\|"deny"}` | `204` |
| POST | `/api/asks/{ask_id}` | `{answer}` | `204` |

### Skills (playbooks) and Tools (executables)
Each cat's tools and playbooks are a fixed bundle (`platform/essentials.py`); the front end only shows them. A skill or tool in no bundle is placed by the backend when it is created, imported or synced: on the cats its `target_roles` names, otherwise on every cat (`is_global`). The `assignments` and `global` routes remain for scripts; a bundle item cannot be removed through them.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/skills` | – | `Skill[]` |
| GET | `/api/skills/{id}` | – | `Skill` with `body` |
| POST | `/api/skills` | `{name, description, body, target_roles?}` | `201 Skill` (custom overlay). `target_roles`: cat ids or roles, comma-separated (`"pulse, qa"`); empty makes it global |
| POST | `/api/skills/import` | `{url}` | `201 {message, skills:Skill[]}` (fetch a SKILL.md from a URL) |
| PUT | `/api/skills/{id}/global` | `{on: boolean}` | `Skill` (attach to every agent) |
| DELETE | `/api/skills/{id}` | – | `204` (`403` for bundled) |
| PUT | `/api/skills/{id}/assignments` | `{agent_ids: string[]}` | `Skill`. Specialist cats only: an unknown cat is `404`, but Cat Crew's ids (`crew`, legacy `chief`) are silently ignored — the orchestrator is never looked up, stored or returned in `agent_ids`. |
| GET | `/api/skills/proposals` | – | `Skill[]` — auto-generated skills awaiting review (the Hermes builder) |
| POST | `/api/skills/{id}/approve` | `{agent_id}` | `Skill` (assigns it to the discovering agent) |
| POST | `/api/skills/{id}/dismiss` | – | `204` |
| GET | `/api/tools` | – | `Tool[]` |
| GET | `/api/tools/available` | – | `[{handler, name, description, registered}]` — public functions in `backend/tool_functions` that can be registered |
| POST | `/api/tools/import` | `{url}` | `201 {message, tools:Tool[]}` |
| GET | `/api/tools/{id}` | – | `Tool` with `schema` |
| POST | `/api/tools` | `{handler: "module:function"}` | `201 Tool`, global unless a bundle names it (`422` if it cannot be imported or has no schema) |
| PUT | `/api/tools/{id}/global` | `{on: boolean}` | `Tool` |
| PUT | `/api/tools/{id}/assignments` | `{agent_ids: string[]}` | `Tool` (same rule as skills). A cat whose bundle holds the tool (`Tool.locked_agent_ids`) always keeps it, whatever the request says. |
| DELETE | `/api/tools/{id}` | – | `204`; `409` when the tool is in a cat's bundle |

### Models (no secrets)
`GET /api/models` → `{roles: {chat|coding|memory|vision|planning|images: {host, model}}, hosts:[{id, label, enabled, cloud, configured?, key_source?}]}`.
Hosts are cloud hosts, each with its own key: `openrouter` (every vendor's models through one key, addressed by vendor-prefixed slug such as `deepseek/deepseek-v4-pro`) and the vendors' own OpenAI-compatible APIs `openai`, `anthropic`, `gemini`, `deepseek`, `groq`, `mistral`, `xai` (the vendor's own model ids). There is no local model host: a `lmstudio` / `ollama` host saved by an older version reads as unset and the start-up check asks for a new pick. `images` runs on `comfyui`, `openrouter` (image models) or `off`. `vision` may also be `off` (no model): for a chat model that cannot see, `browser_look` then answers that Vision is turned off instead of following Chat.
`configured` is a boolean: "a key is set". `key_source` is `"saved"`, `"environment"` or `"none"`. The key itself is never sent. A cloud host is `enabled` once it has a key.

`PUT /api/models/roles` (`{chat?:{host,model}, coding?:…, vision?:…, planning?:…}`) → same shape as `GET /api/models` (`422` for an unknown host). An optional role (`coding`, `memory`, `vision`, `planning`) with an empty host and model is **unset** and follows Chat; `review` unset follows Coding; `fallback` unset means no failover (a turned-away model stops the run). A set `fallback` is the model a run switches to when its model answers 402 / 429, for that run and for 15 minutes of the same project's calls. Saving clears the cached health report.

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/models/hosts/{host}` | The host's live model list (cached for 30 s; see below) |
| POST | `/api/hosts/{host_id}/refresh-models` | The same shape, asked from the provider right now (skips the cache) and clears the health report. `404` for an unknown host. |
| GET | `/api/models/health?refresh=false` | Are the models chosen for the roles still available? (see below) |

**Host listing** `{host, online, error, error_kind, checked_at, models:[{name, display_name, loaded, capabilities, context_length, price_prompt_per_1m, price_completion_per_1m, is_free, supports_prompt_cache}]}`. `error` is a short fixed phrase (it never carries a URL, a key or a raw exception); `error_kind` is `""`, `"no_key"`, `"auth"`, `"unreachable"` or `"error"`. `context_length` is in tokens (0 when the host does not say). The list holds chat models only: embeddings, speech, moderation and image-only models are dropped, and so is anything the host marks retired (Groq `active: false`).

`price_prompt_per_1m` / `price_completion_per_1m` are USD per 1M tokens, read straight from OpenRouter's own `GET /v1/models` `pricing` object (`null` on every host but OpenRouter). `is_free` is true for a `:free`-suffixed slug or a model priced at zero both ways. `supports_prompt_cache` reflects OpenRouter's `pricing.input_cache_read` being present (Anthropic/Qwen need an explicit `cache_control` breakpoint to actually use it — see the OpenRouter savings section below; OpenAI/Grok/Moonshot/Groq/DeepSeek/Gemini 2.5 cache automatically and this flag is true for them too). The Model Picker shows the price per 1M tokens next to each model and a Free / Cache-capable badge when set; there is no separate pricing service — this reuses the same 30 s cached snapshot as the rest of `/api/models/hosts/{host}`.

Where the list comes from: every host's own OpenAI-shaped `GET /models` (Gemini's `models/` prefix is dropped; Anthropic also gets `x-api-key` and `anthropic-version`). Keys travel in headers, never in a URL.

**Capabilities** (`capabilities`, in display order): `vision` (accepts images), `reasoning` (thinks step by step), `tools` (native function calling), `chat` (every listed model). What the host reports wins; otherwise the model name decides (`vision`, `-vl`, `gpt-4o`, `gemini`, `claude-3` and later count as vision; `reasoner`, `r1`, `o1`, `o3`, `thinking` as reasoning; tool calling is assumed unless the family is known not to have it). The UI shows vision, reasoning and tools as badges and warns (never blocks) when a role gets a model without what it works best with: Vision needs `vision`, Coding `tools`, Planning `reasoning`.

**Health** `{checked_at, ok, hosts:{id:{online, error_kind, model_count}}, issues:[{role, role_label, host, host_label, model, reason, message}]}`. The backend checks every host a role uses in the background at start-up and caches the report for 60 s. `refresh=true` forces a new check. Roles left unset are not checked. `reason` is `"missing"` (the host answered and the model is not in its list: retired, deprecated, renamed), `"offline"`, `"no_key"` or `"disabled"`; `message` is a ready-to-show sentence such as *The model 'gemini-1.0-pro' assigned to 'Memory' is no longer available on Google Gemini.* The UI reads this once per launch and opens the **Model Configuration Notice** when `issues` is not empty; its **Re-assign Models** button opens `#/models?fix=memory,coding`, and the Models page highlights each role that still holds a model in `issues`.

**Token routing.** When a chat's history goes over 8,000 tokens (about 4 characters each; `CATCREW_CONTEXT_TOKENS`), the engine summarises the older turns with the **Memory** model (the Chat model when Memory is unset) before the request goes to the next model, keeping the most recent turns word for word. The turn's activity card shows a "Compressed context" step with the before and after size. Planning: the architecture and distribution phases of the SOP run on the **Planning** model when one is set. Vision: the `browser_look` tool of Aegis and Pixel sends a page screenshot to the **Vision** model and returns its description as text; with no Vision model it says so and points to `browser_snapshot` / `browser_inspect`.

### Project model overrides (no secrets)
Each project can run a role on its own host and model. The global picks above stay the default.

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/projects/{id}/models` | `{project_id, overrides, effective, global, global_effective, hosts}` (`404` unknown project) |
| PUT | `/api/projects/{id}/models` | Body `{roles: {coding: {host, model}, vision: {host: "inherit"}, chat: null}}`; the same state back. Only the named roles change. |

`overrides` holds only the roles this project overrides. `effective[role]` is what the role runs on now: `{host, model, source: "project" | "global" | "chat" | "coding", explicit}`. `global` is the Models-page pick as saved (an unset role is empty); `global_effective` is what each role runs on with no override (an unset role follows Chat), which is what the UI shows in "Use Global Setting (OpenRouter: deepseek/deepseek-v4-pro)". No response carries a key. Role names are `chat, coding, memory, vision, planning, review, images`. `review` is the model the QA phase runs on; unset, it follows Coding (`source: "coding"`).

`host: "inherit"` (or an empty host, or `null`) clears the override for that role. Any other host must be a cloud host (`422` otherwise) and needs a non-empty model (`422`); `images` accepts `comfyui`, `openrouter` or `off` (no model); `vision` also accepts `off`. An override counts only while its host is usable (a cloud host with a key; ComfyUI for images); with no key the role quietly falls back to the global pick. Deleting a project removes its overrides.

Resolution order, one helper for the whole engine (`services/model_resolver.get_effective_model(project_id, role)` -> `{host, model, api_key}`; the key never leaves the process): project override -> the global pick -> (roles left unset) the Chat role resolved the same way, so a project's Chat override is inherited by its unset roles. The ambient project id (`platform/runtime.current_project_id()`) is set by the turn runners, so the provider factory, context compression (Memory), the SOP planning phases (Planning), `browser_look` (Vision) and the image checkpoint picker all resolve per project without new arguments. Cat Crew's free chat runs on the Chat role; work turns and 1:1 cat chats on Coding. Long-term memory (mem0's fact-extraction model, `services/memory.resolve_memory_llm`, Memory role) and self-modification (`services/self_modifier`, Coding role, now also through OpenRouter when that is the pick) follow the project too. Not per project (still global): the start-up model health check.

### Telemetry (no secrets)
`GET /api/telemetry/live?project_id=` -> the Telemetry tab's snapshot (`404` for an unknown `project_id`; without one the reading is the projectless one). Every part is gathered on its own, so one that fails is `{configured:false, status:"error"}` and the rest is intact. **No response carries a key.**

```json
{ "project_id": "p1", "updated_at": "2026-09-21T09:12:03Z", "context_limit": 64000, "context_threshold": 54000, "context_limit_source": "provider",
  "context": { "host": "openrouter", "model": "deepseek/deepseek-v4-pro", "tokens": 10270, "system_tokens": 900, "limit": 64000, "threshold": 54000,
               "percent": 16, "level": "ok", "compressed": false, "updated_at": "..." },
  "active":   { "host": "openrouter", "host_label": "OpenRouter", "model": "...", "status": "ready", "message": "",
                "source": "project", "local": false },
  "openrouter":       { "host": "openrouter", "configured": true, "status": "ok", "display": "$12.45 remaining", "balance": 12.45, "currency": "USD" },
  "openrouter_cache": { "host": "openrouter", "calls": 42, "prompt_tokens": 180000, "cached_tokens": 96000, "cache_hit_pct": 53, "estimated_pct_spend_saved": 48, "estimated_usd_saved": 0.65 },
  "jev": { "enabled": false, "model": "typesafe/jev-latest", "confidence_threshold": 0.92, "precheck_calls": 0, "bypasses": 0, "guard_calls": 0,
           "blocked_tool_calls": 0, "interceptions": 0, "tokens_saved_estimate": 0, "avg_decision_latency_ms": 0.0, "estimated_usd_saved": 0.0 },
  "gpu": { "available": true, "source": "nvml", "name": "...", "vram_used_gb": 8.9, "vram_total_gb": 16, "utilization": 41,
           "temperature_c": 62, "gen_speed_tps": 72 } }
```

- `context.level` is `ok` below 60 %, `warn` from 60 to 85 % and `danger` above 85 % **of the model's own window**; the older turns are summarised at `threshold` (about 85 %, see Token routing). `tokens` is the estimate (characters / 4) of everything the next request carries, system prompt included (`system_tokens` is the part of it that is the prompt); `limit` is the window of the model that will receive it. `context` is `null` until a turn has measured something. Top level: `context_limit` / `context_threshold` are the window of the project's active **Coding** model, so the gauge has its scale before a reading exists; `context_limit_source` is `provider` (a known window), `default` (a guess: 16,384 / 14,000) or `env`.
- Windows (`services/provider_limits.get_model_context_limit(host, model)` -> `{max_tokens, compression_threshold}`): for `openrouter`, the vendor prefix of the model slug picks the window — `deepseek/...` 64,000 / 54,000, `google/...` 1,000,000 / 850,000, `anthropic/...` 200,000 / 170,000, `openai/...` 128,000 / 110,000, anything else 64,000 / 54,000. The vendors' own hosts use their table row (Mistral and xAI 128,000 / 108,000); an unknown host gets 16,384 / 14,000. `CATCREW_CONTEXT_TOKENS` (default `0` = automatic) forces one threshold for every model.
- `active` is the project's **Coding** role (`services/model_resolver`); `status` is `ready`, `offline`, `missing`, `no_key`, `disabled` or `unknown` (from the cached health report).
- `openrouter` is the balance read from `GET /api/v1/credits` (total credits minus usage), cached for 60 s and marked stale right after a call. `status` is `ok`, `empty`, `error` or `no_key`; `message` is a fixed sentence (never a raw error).
- `openrouter_cache` is prompt-cache token savings (`services/usage_meter.cache_savings`): OpenRouter's Anthropic-style `cache_control` breakpoint on the system message, read back from `usage.prompt_tokens_details.cached_tokens` (or the Anthropic-native `cache_read_input_tokens` / `cached_tokens` fields OpenRouter passes through). `cached_tokens` and `prompt_tokens` are real, host-reported counts; `estimated_pct_spend_saved` stays a **documented assumption** (cached reads are typically discounted about 90 %). `estimated_usd_saved` is a real dollar figure now, computed only when possible: cached tokens × the calling model's own `price_prompt_per_1m` (read from the same OpenRouter catalog snapshot the Model Picker's pricing comes from) × the 90 % discount assumption; `null` when the model's price is not in the current catalog snapshot or no cached calls were made.
- `jev` is the Jev ("System One") pre-execution gatekeeper's saved settings (`GET/PUT /api/jev/settings`, minus the key) plus its running counters (`GET /api/jev/metrics`, `services/jev_metrics`): `precheck_calls`/`bypasses` count the once-per-turn "can this be answered directly, with no tools?" check; `guard_calls`/`blocked_tool_calls` count the per-tool-call safety screen; `interceptions` is `bypasses + blocked_tool_calls`; `tokens_saved_estimate` is a rough estimate of the conversation size that was never sent to the coding model (a bypass) or that would have gone into another repair round (a block), not a measured saving; `estimated_usd_saved` prices that estimate the same way `openrouter_cache` does (`null` when it can't); `avg_decision_latency_ms` averages the last 500 decisions. Off by default (`enabled: false`) — Jev (TypeSafe AI's `typesafe/jev-latest`) is reached through **OpenRouter's Decisions endpoint** and rides on the OpenRouter key already configured for chat models; see the section below.
- `gpu` comes from NVML (`nvidia-ml-py`, optional) or else `nvidia-smi`; `available:false` without an NVIDIA GPU. `gen_speed_tps` is always `null` (there is no local model host).

### Jev gatekeeper settings and metrics (no secrets)
Jev ("System One", TypeSafe AI — `docs.typesafe.ai`) is a small, fast **classifier**, not a chat
model: it never drafts a reply, it only answers narrow yes/no ("noul"), multiple-choice or scored
questions about a `state` object it is given. Two checks sit around the expensive parts of a turn
(`backend/middleware/jev_gatekeeper.py`):

1. **Precheck** (`precheck_intent`, once per turn, before the Coding model is called): asks Jev "can
   this be answered with no tools at all?" A confident **yes** (`confidence > 0.95`, a fixed
   threshold, not the slider below) makes the caller run that one model call *without* the tool
   schemas attached — the token saving is not calling the model, it is calling it without
   ~30 tool definitions in the request. A low-confidence or failed answer just falls through to the
   normal tool-enabled loop.
2. **Guard** (`guard_tool_call`, on every sensitive/destructive tool call — file writes, shell, git,
   MCP calls; read-only calls such as `read_file` are always let through unguarded): asks Jev "is
   this call safe and on-goal?" and blocks it *before* it runs when confidence is below the
   configurable `confidence_threshold`, short-circuiting a bad-edit → error → repair round-trip.

Both are no-ops — proceed exactly as if Jev were never asked — when the gatekeeper is off, no key
resolves at all, or the Jev call itself fails for any reason (network, bad key, timeout, an unparseable
response). Jev is reached through **OpenRouter's Decisions endpoint**
(`https://openrouter.ai/api/alpha/decisions`, model slug `typesafe/jev-latest`) rather than a
TypeSafe-hosted endpoint — the wire format (`{model, state, questions}` → `{answers}`) is the same
either way, only the host, key and model-slug prefix differ. In the normal case **no key of its own is
needed**: `_resolved_key()` (`backend/middleware/jev_gatekeeper.py`) falls back to the OpenRouter key
already configured for chat models (`services/api_access.resolved_key("openrouter")`). Settings →
Models → Jev gatekeeper only offers an *optional override* — a different key or, via
`CATCREW_JEV_URL`/`base_url`, a different endpoint entirely (TypeSafe's own direct API, for instance)
— stored the same write-only way as every other key here: `GET` never echoes it back.
Resolution order: explicit saved override → `CATCREW_JEV_KEY` env override → the OpenRouter key.

> The exact Decisions endpoint path (`/api/alpha/decisions`) reflects OpenRouter's own "beta" framing
> and two independent third-party reports; it has not been confirmed against a live authenticated
> call from this codebase. If Jev never records a successful decision once enabled, this is the first
> thing to check — see the module docstring in `backend/services/jev_client.py`.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/jev/settings` | – | `{enabled, model, confidence_threshold, api_key_configured, key_source}` |
| PUT | `/api/jev/settings` | `{enabled?, model?, confidence_threshold?, api_key?}` | Same shape (`422` for an empty `model`, a threshold outside 0–1, or an invalid key). `api_key` is write-only: omit it to leave the saved override untouched, send `""` to clear it. |
| GET | `/api/jev/metrics` | – | `{precheck_calls, bypasses, guard_calls, blocked_tool_calls, interceptions, tokens_saved_estimate, avg_decision_latency_ms}` |

Defaults: `enabled: false`, `model: "typesafe/jev-latest"`, `confidence_threshold: 0.92`. `key_source`
is `"saved"` or `"environment"` for an explicit override, `"openrouter"` when Jev is riding on the
already-configured OpenRouter key (the normal case), or `"none"` when nothing resolves at all.
`tokens_saved_estimate` is a rough estimate (a bypass's skipped tool-schema JSON size, or a block's
avoided repair-round message size, divided by ~4 chars/token) — not a measured saving; the Telemetry
endpoint turns it into a dollar figure when the project's Coding model is priced (see
`estimated_usd_saved` above). Jev's
documented jaggedness (no arithmetic, no date reasoning, does not treat its input as adversarial, and
`P(x) != 1 - P(not x)` across separately-worded questions) is why both checks ask a single question in
the one polarity they need and never derive an answer by negation. Jev exists to save tokens and
reduce repair loops; it must never become a new reason a turn cannot proceed, so nothing in
`services/jev_client.py` or `middleware/jev_gatekeeper.py` ever raises.

### OpenRouter savings settings (no secrets)
Optional extras on top of OpenRouter as the sole cloud host (`backend/services/settings_store.py`
`openrouter_settings`, applied in `services/api_access._openrouter_routing_body()`, which every
OpenRouter request carries via `extra_body`). Prompt caching itself needs no setting here — it is
automatic per model on OpenRouter's side (see `openrouter_cache` in Telemetry above); these are the
knobs beyond that.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/openrouter/settings` | – | `{provider_sort, allow_fallbacks, compression_enabled, free_tier_enabled, free_tier_model}` |
| PUT | `/api/openrouter/settings` | same fields, all optional | Same shape (`422` for an unknown `provider_sort` or an empty `free_tier_model` while `free_tier_enabled` is true) |

Defaults: `provider_sort: ""` (OpenRouter's own default: price-weighted, automatic — this is the real
mechanism behind "fastest/cheapest, automatically"; **there is no `:auto` model-slug suffix** in
OpenRouter's API, so one is never appended), `allow_fallbacks: true`, `compression_enabled: false`,
`free_tier_enabled: false`, `free_tier_model: "meta-llama/llama-3.3-70b-instruct:free"`.

- `provider_sort` (`"" | "price" | "throughput" | "latency"`) becomes the request's
  `provider: {sort, allow_fallbacks}` object. Appending `:nitro` (fastest) or `:floor` (cheapest)
  directly to a model slug are real, documented OpenRouter suffixes that need no extra code here —
  typing one into a model field just works.
- `compression_enabled` turns on OpenRouter's `context-compression` plugin
  (`plugins: [{"id": "context-compression"}]`) for requests over that endpoint's context window. It
  can drop or truncate older messages OpenRouter-side (on top of this app's own Memory-model
  compaction), so it defaults off.
- `free_tier_enabled` / `free_tier_model` route CatCrewAI's own lightweight background calls — today,
  context-summary compaction's Memory-role call (`services/compaction.py::summarizer_provider`) — to
  a `:free`-suffixed model instead of whatever the Memory role is set to, when the effective host for
  that call is OpenRouter. It never touches the Coding, Planning or Chat roles' own calls.

### Switching a blocked model

`POST /api/models/replace` `{from_host, from_model, to_host, to_model}` puts `to_*` wherever `from_*` is picked: the global roles, every project's own picks, and OpenRouter's free-tier model for background calls (turned off if the new host is not OpenRouter). `to_host` must be a host with a key and `to_model` non-empty (`422` otherwise). Returns the `GET /api/models` state plus `changed` (one line per role switched).

### API keys (write-only)
Every key CatCrewAI keeps goes in here (Models → Hosts for the model hosts; the **MCP Tools & Integrations**
page for the "service" group: `brave`, `typesafe`) and are stored in `data/settings.json`. **No
response ever contains a key**, no error message repeats one, and the body is parsed by hand so a
rejected payload is not echoed in a validation error. Ids: the model hosts `openrouter`, `openai`, `anthropic`, `gemini`, `deepseek`, `groq`, `mistral`, `xai` (group `host`), `typesafe` (Jev on TypeSafe's official API) and `brave` (web search) (group `service`). Jev keeps no key of its own: on OpenRouter it uses the `openrouter` key, on TypeSafe the `typesafe` key; `PUT /api/jev/settings` takes no key. A key Jev kept in its settings before is moved here once and erased there. There is no Hugging Face key any more (CatCrewAI no longer downloads models): `/api/keys/huggingface` answers `404`, and a `huggingface_key` left in an old `settings.json` is deleted when the engine starts. `/api/keys/lmstudio` is retired (`404`): there is no local model host.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/keys` | – | `KeyInfo[]`: `{id, title, subtitle, group: "host"\|"service", configured, source: "saved"\|"environment"\|"none", signup}` |
| PUT | `/api/keys/{id}` | `{key: string}` | `KeyInfo` (`404` unknown id; `422` for an empty key, one longer than 512 characters, or one containing spaces or line breaks, such as a pasted `Bearer …`) |
| DELETE | `/api/keys/{id}` | – | `KeyInfo` (forgets the saved key; idempotent; an environment variable, if any, keeps working) |

A key saved here wins over an environment variable (`OPENROUTER_API_KEY` / `CATCREW_OPENROUTER_KEY`, `BRAVE_SEARCH_API_KEY`, …). Saving or removing one clears the cached host lists and the health report, so the next call sees the change.

### MCP integrations (Blender, Unity)
Programs that have an MCP server are plugged in through `backend/config/mcp_servers.json` (see `docs/ARCHITECTURE.md` section 11). These routes show their live status and let the UI look again **without restarting the backend**. **No response carries an environment variable, header or credential**; a URL is shown without user info or query.

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/api/mcp/servers` | - | `McpPayload` (below) |
| POST | `/api/mcp/refresh` | optional `{hard: bool}` | `McpPayload`. Re-reads `mcp_servers.json`, re-discovers the tools (`tools/list`) of every connected server and reconnects the ones that are down now instead of waiting for their back-off. `hard: true` reconnects every server. Waits up to 15 s for the outcome. `409` when `CATCREW_MCP=0`. |
| POST | `/api/mcp/servers/{id}/reconnect` | - | `McpPayload` plus `server` (that server's view). `404` for an unknown id; `409` when `CATCREW_MCP=0`. |
| GET | `/api/mcp/servers/{id}/tools` | - | `[{name, function, description, exposed, read_only}]`: every tool the server offers; `exposed` says it is in the cat's own tool list (the rest is reachable through `mcp_find_tools` / `mcp_call_tool`). `404` for an unknown id. |

```json
{ "servers": [ { "id": "blender", "label": "Blender MCP", "icon": "🧊", "enabled": true, "transport": "stdio", "target": "mcp-blender --host 127.0.0.1 --port 9876",
                 "status": "disconnected", "ready": false, "bridge": "unknown", "app_label": "Blender", "error": "Blender is not running.", "error_kind": "refused",
                 "note": "", "setup_hint": "pip install mcp-blender, then ...", "bound_to": ["pixel"], "bound_cats": [{"id": "pixel", "name": "Pixel"}],
                 "tools_total": 0, "tools_exposed": 0, "tools_on_demand": 0, "tool_names": [], "connected_at": null, "checked_at": null, "next_retry_in": 8 } ],
  "problems": [], "sdk_installed": true, "running": true, "enabled": true }
```

- `status` is `connected`, `connecting`, `reconnecting`, `disconnected` or `disabled`. `ready` is true only when the server is connected **and** the program behind it (Blender, Unity) answers a cheap probe; a `connected` server with `ready:false` is "server up, program not answering" (`note` says which).
- `bound_to` / `bound_cats`: the only cats that get this server's tools in their running turn. Everyone else does not even see them in their schema.
- `problems` lists config lines that were skipped (a server with an unknown transport, no command, ...). `sdk_installed:false` means the `mcp` package (version 2 or newer) is missing: `pip install -r requirements.txt`. `enabled:false` means `CATCREW_MCP=0`.

## 3. WebSocket `/ws/events`

Connect: `ws://127.0.0.1:8765/ws/events?project_id=<pid>` (`project_id` optional:
omit for every project). Each frame is one JSON object:

```json
{ "v": 1, "seq": 1042, "ts": "2026-09-19T15:51:09Z", "type": "bot.activity",
  "project_id": "p1", "data": { } }
```

`seq` increases by one per event for the life of the server process (it is the same
counter for every client, so a filtered client sees gaps; that is normal). On (re)connect
the server sends `hello` (`data: {server_seq}`), and the client refetches state over
REST (`/api/bootstrap`, roster, tasks, messages) and then applies live events —
there is no replay. Client → server frames: `{"type":"ping"}` (answered with
`{"type":"pong"}`) and `{"type":"subscribe","project_id":"p1"|null}`.
A handshake with a bad `Host`/`Origin` is closed with code `1008` before any frame.
A client that stops reading is closed with `1013` ("try again later"): reconnect and refetch.

| `type` | `data` | Meaning / UI reaction |
| --- | --- | --- |
| `hello` | `{server_seq}` | Connected. Refetch. |
| `bot.staffed` | `{bot_id, agent:Agent, entry:RosterEntry}` | **The orchestrator invoked a specialist into the project.** Pop the card into the sidebar strip instantly (slide-in + glow), no refresh. |
| `bot.released` | `{bot_id}` | Card removed. |
| `bot.activity` | `Activity` + `project_id` (data) | (The envelope's `project_id` is `null`: a cat's status is global, every client gets it. `data.project_id` names the project whose turn wrote it, `""` when unknown.) Update the card's status pill (`idle`→`thinking`→`coding`/`testing`) and its one-line caption; the caption shows only while `state == "working"`. |
| `phase.started` | `{phase, bot_id, label}` | The orchestrator started a phase for that cat. |
| `cat.swapped` | `{cat_id, name, phase, label, status}` | The cat holding the engine changed (sent right after `phase.started`). `status` is the word for the phase (Thinking, Coding, Testing, Chatting). |
| `task.changed` | `{task:Task}` | Insert/update the task in the project's Tasks tab (in place, by id). The payload is the row as it is when the event is delivered, so two rapid changes may arrive with the same (latest) status. |
| `file.changed` | `{change:FileChange}` | Prepend to Recent Changes (no diff body; fetch `/api/diffs/{diff_uuid}` on demand). |
| `chat.message` | `{thread:bot_id, message:Message}` | A finished transcript entry (a cat's turn, a user message, an error). **Upsert by `message.id`**: the last message of a room turn is re-sent with its `activity` card attached. Errors have `role:"error"` and a synthetic id. |
| `chat.activity` | `{thread, turn_id, bot_id, step:ActivityStep & {n:number}}` | A live step for the running turn's ExecutionCard in `thread`. `n` grows by one per step within a turn and thread; upsert by `n`. During a room turn the specialist's own thread and the room card both receive steps. |
| `turn.started` | `Turn` | Disable/mark the composer as running. |
| `turn.finished` | `{turn_id, status:"ok"\|"aborted"}` | Turn ended. A queued follow-up starts right after (a new `turn.started`). |
| `turn.failed` | `{turn_id, error, model_issue?}` | Turn failed; show `error`. `model_issue` (`{host, model, kind: rate_limited\|no_credit, status, free, message, roles: [{role, label, source}]}`) means the host turned the model away: the run stopped at the first refusal (no further phases, no retries on a `:free` model). Offer `POST /api/models/replace` + `POST /api/projects/{pid}/retry`. |
| `approval.requested` | `{approval_id, bot_id, tool, detail}` | Show Allow / Allow always / Deny → `POST /api/approvals/{id}`. |
| `approval.resolved` | `{approval_id, decision}` | Dismiss. |
| `ask.requested` | `{ask_id, bot_id, prompt, choices:string[]}` | Ask the user → `POST /api/asks/{id}`. |
| `ask.resolved` | `{ask_id}` | Dismiss. |
| `skill.proposed` | `{skill:Skill, agent_id, task_id}` | The Hermes auto-builder proposed a skill (it noticed a repeatable procedure); badge on the Skills tab. |
| `project.changed` | `{project:Project}` | Name / workspace / roster / phase changed. |
| `context.usage` | `{host, model, tokens, system_tokens, limit, percent, level, compressed}` | How full the running conversation is, sent before every model call of a turn (after compression, when it ran). Stored per project as `rooms[pid].context`; the Telemetry gauge shows it live between polls. Sent to the project's own sockets only (an event with an empty `project_id` goes to every client). |

Ordering. Events are delivered to a client in `seq` order. One specialist's appearance in a
room turn (verified against the running server) is:

```
turn.started(mode:work), chat.activity(crew), bot.activity(crew: thinking)  <- the work turn begins
bot.staffed(cipher)                                                       <- pops into the sidebar
task.changed(cipher)  x2                                                  <- created, then in_progress
phase.started(blueprint, cipher), bot.activity(cipher: thinking)
chat.activity(cipher n=1..)  chat.activity(crew n=..)                      <- live card steps
bot.activity(cipher: coding), file.changed, ...                           <- tools running
bot.activity(cipher: idle), task.changed(cipher: done)
chat.message(crew, cipher's reply), chat.message(cipher thread, reply + card)
project.changed                                                           <- then the next phase
... turn.finished(ok)
```

`bot.staffed` may be sent more than once for the same cat (once when the ledger adds the member,
once when the persisted roster does): treat it as an idempotent upsert. A queued follow-up message is
started by the server as soon as the running turn ends; `POST .../messages` while a turn runs answers
`202 {queued:true}` (there is one running turn at a time, process-wide).

## 4. WebSocket `/ws/projects/{project_id}/terminal`

The integrated terminal under a project's chat (`TerminalPanel.tsx`, toggled with Ctrl/Cmd+`). The socket goes through the same Host/Origin guard as `/ws/events`. An unknown project closes with code `4404` and starts nothing. The shell opens in the project's first folder (a private scratch folder when it has none) and dies with the socket.

Client to server (JSON text frames): `{"type":"run","command":"npm test"}`, `{"type":"input","data":"y\n"}` (stdin of the running command), `{"type":"interrupt"}` (kills the command's whole process tree), `{"type":"eof"}`, `{"type":"ping"}`.

Server to client: `{"type":"ready","cwd","root","shell","platform"}`, `{"type":"started"}`, `{"type":"output","stream":"stdout"|"stderr","data"}`, `{"type":"exit","code","cwd","interrupted"}`, `{"type":"error","message"}`, `{"type":"pong"}`.

Shell: PowerShell (else cmd) on Windows, `$SHELL` when it is bash or zsh (else bash, else sh) elsewhere; `CATCREW_TERMINAL_SHELL` overrides (`powershell`, `pwsh`, `cmd`, `bash`, `zsh`, or a path). Each `run` is one command in the shell (no PTY): the working directory persists between commands (a bare `cd` is handled by the engine; `cd x && ...` runs inside the shell and does not persist), variables and aliases do not, and full-screen programs (`vim`, `top`) are not supported. One command runs at a time (a second `run` gets an `error`); output past 4 MB per command is dropped with a notice. The shell inherits the app's environment minus the model-provider keys CatCrewAI holds. At most 8 sessions; opening a project again replaces its session and resumes in the folder the last one ended in.
