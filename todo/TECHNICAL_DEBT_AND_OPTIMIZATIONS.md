# Technical debt and optimisation backlog

Audit of the `cat-crew-ai` code base, first written in September 2026 against the PyQt6 desktop app and **updated after the Phase 3 pivot** (PyQt purged; `backend/` FastAPI + WebSocket, `frontend/` Tauri + React + Tailwind). Every open finding cites the file where it was observed. Severity: **P0** loses data or blocks the user, **P1** visibly hurts every session, **P2** cost or hygiene.

**Limits.** The findings come from reading the code and from running the backend and the UI in a Linux sandbox, not from profiling the Windows desktop app. Where a claim depends on runtime behaviour it says "verify". Line numbers come from the tree before the pivot and will have drifted.

## Priority summary (open items)

| ID | Area | Finding | Sev | Effort |
| --- | --- | --- | --- | --- |
| A-01 | DB | A corrupt or half-written `registry.json` silently loads as an empty catalog, and the next save overwrites the recoverable file | P0 | S |
| A-02 | Async | Auto-heal retries re-run the original task and never see the failure output | P0 | S |
| U-01 | UX | A pending approval or question is not re-listed after a page reload or reconnect; the agent waits | P1 | S |
| N-01 | Tests | 2 test modules cannot be collected and 17 tests fail (all of them already failing before the pivot) | P1 | M |
| N-02 | Security | `data/settings.json` holds API keys in plain text; two tokens were exposed and must be rotated | P1 | S |
| A-03 | DB | No WAL and a 10 s lock timeout on the ledger | P1 | S |
| A-05 | Async | `report_task_results` is synchronous and blocks on a model call | P1 | S |
| C-01 | Tokens | SOP hands every later specialist the full text of every earlier phase | P1 | S |
| C-02 | Tokens | Volatile clock and probes sit mid-prompt and defeat prompt caching | P1 | S |
| C-03 | Tokens | `SYSTEM_PROMPT = build_system_prompt()` runs hardware and Docker probes at import | P1 | S |
| A-06 | DB | Chat sessions and registry are written non-atomically, whole-file | P1 | M |
| N-03 | Packaging | No release build for the new stack (Tauri bundle + backend sidecar); the PyInstaller packager was retired to `legacy/` | P1 | M |
| N-04 | Runtime | One turn at a time across all projects (process-global workspace and tool allow-list) | P2 | L |
| N-05 | Runtime | Self-modify restart re-executes the backend mid-turn and does not cooperate with the desktop shell | P2 | M |
| N-06 | API | `bot.staffed` may be emitted repeatedly for one cat | P2 | S |
| N-07 | API | The runner's "Computer after this turn" note and role banners are stored inside message text and stripped only on the wire | P2 | S |
| N-08 | API | No token streaming (`chat.stream`); replies arrive whole | P2 | M |
| A-07 | DB | `runs` rows and schema DDL run on the event loop and on every connection | P2 | S |
| A-08 | DB | Orphaned `running` rows are never reaped; no retry on `database is locked` | P2 | S |
| C-04 | Tokens | Static tool list in the prompt duplicates the JSON schemas | P2 | M |
| C-05 | Tokens | Compaction now runs at a fixed 8,000 tokens; it does not yet read each model's real window or offload large tool results | P2 | M |
| C-06 | Tokens | Identity and AGENTS.md budgets | P2 | S |
| N-09 | Hygiene | Repository, dependency and dead-code clean-up (see below) | P2 | S-M |
| N-10 | Frontend | No end-to-end test of the desktop shell; the Rust shell is unit-tested for its HTTP helpers only | P2 | M |
| N-11 | Orchestrator | The Cat Crew intent router is keyword-based (English + Bulgarian); no confidence score and no way for the user to override a misroute | P2 | M |
| M-01 | Models | The Anthropic host, `browser_look` and the Gemini list endpoint are tested against fake responses only, never a live API | P2 | S |
| M-02 | Models | Pixel and Aegis prompts are seeded once, so existing installs keep the old wording (the browser tools carry their own guidance) | P2 | S |
| N-12 | Hygiene | Legacy `chief` compatibility shims (session `bot_id`, orphaned `agent_*` rows, `chief` table, registry JSON key, `RunKind.CHIEF`, `ROLE_PACKS` role name) can be dropped after a one-off data migration | P2 | S |

## 1. Resolved by the pivot

These were the PyQt-era findings. They no longer apply because the code they describe was deleted, not because they were patched.

| ID | Was | Now |
| --- | --- | --- |
| R-01 | Ghost rows from `takeAt()` + `deleteLater()` | React renders keyed lists; no widget teardown |
| R-02 | The room entry (now Cat Crew) blocked the GUI thread (transcript rebuild, page reloads) | Data is fetched over REST and rendered by React; the backend never runs on a UI thread |
| R-03 | Task panel rebuilt itself every 3 s with N+1 queries | Tasks come from one query (`list_tasks_with_summary`) and then live `task.changed` events |
| R-04 | Overlay window shown, moved, then blurred | The overlay (and its voice/particle body) was removed; assets are archived in `legacy/` |
| Window | Titlebar flicker and a ghost window | The native window is created hidden with the app background (`tauri.conf.json`), and the UI shows it after its first painted frame; a Rust failsafe shows it after 4 s |
| U-02 | Streaming re-laid out the whole message every 16 ms | No streaming (see N-08); the UI appends whole messages and step lines |
| U-03 | "5 minutes ago" wording, frozen at build time | `frontend/src/lib/time.ts` ("X mins ago" / "X hours ago" under 24 h, an exact date after) re-rendered by a shared clock hook; the backend returns ISO timestamps |
| U-04 | Real-time sidebar and card reactivity | `bot.staffed` pops a cat into the Fleet tab within one WebSocket frame; verified live against the real SOP |
| U-05 | `chat_window.py` (4 216 lines) | Deleted. `ChatService` (`core/chat_service.py`) plus the React app replace it. `tools.py` (about 3 300 lines) is still one module |
| T-01 | PyQt6 in code, PySide6 in the brief | Neither: the UI is Tauri + React. The brief's "PySide6" was in fact PyQt6, which is now removed |
| R-05 | "Could not update browser_click: Unknown agent 'chief'" on the Tools and Skills pages; the orchestrator showed up as a toggleable matrix column | Cat Crew (wire id `crew`) is the orchestrator, not an agent: it stays out of the frontend `agents` map (so no column), the assignment endpoints ignore its ids instead of looking them up, and `skills_for_agent` / `tools_for_agent` / `assign_*` never touch an agent row for it. Regression tests: `tests/test_api.py`, `tests/test_orchestrator.py`, `Tools/Skills.test.tsx`, `reducer.test.ts` |

## 2. Async and database bottlenecks

Threads today: the uvicorn event loop (async routes and the WebSocket hub), FastAPI's worker pool for synchronous routes, one turn thread for the running chat turn (`ChatService`, one turn at a time), one throwaway asyncio loop thread per delegated call, and `asyncio.to_thread` workers for each specialist turn. SQLite is opened per call and never shared across threads, which is correct. The problems are the write concurrency mode and the two JSON stores that sit next to it. File and line references below come from the tree before the pivot; paths have been updated, line numbers may drift.

### A-01 (P0) `registry.json`: silent reset and non-atomic writes

`load_registry()` (platform/registry.py:41-55) catches `OSError`, `JSONDecodeError` and `ValueError`, then falls back to `_defaults()`. `platform/defaults.json` contains no bots and no projects, so a corrupt file yields an **empty catalog** with no message. The recoverable, half-readable file is left on disk until the next `save_registry()` (registry.py:58-68) writes the empty cache over it. That write is itself the cause of corruption: `Path.write_text` truncates the file and then writes 150 KB of indented JSON, so a crash, power loss or a concurrent reader between the two steps leaves a partial file. Nothing takes a lock; `_cache` is a module-global object mutated by the SOP worker thread while an API thread saves it (16 call sites of `save_registry`).

The live data shows the problem is real: `data/platform/` already holds `catcrew.db.corrupt-*` for the ledger, and `registry.json` has grown to about 150 KB because each project's `state` embeds `phase_outputs`, `tool_events` and `created_artifacts` (hundreds of absolute scratch paths).

Fix: (1) write atomically: write to `registry.json.tmp` in the same directory, `flush` and `os.fsync`, then `os.replace`, keeping one `registry.json.bak` of the previous version; (2) on a decode error, rename the bad file to `registry.json.corrupt-<timestamp>`, try the `.bak`, and only then fall back to defaults, surfacing a visible warning; (3) guard load/save with one `threading.RLock` and save a deep copy so worker threads never serialise a structure another thread is mutating; (4) move project run state (`phase_outputs`, `tool_events`, `created_artifacts`) out of the registry into the ledger or a per-project file. The state package already contains a tested `RegistryRepo` (`state/repositories.py`) that was built as a drop-in replacement and never wired in; finishing that migration solves all four at once.

Verify: a test that truncates `registry.json` mid-write and asserts the app loads the `.bak` and preserves the corrupt file.

### A-02 (P0) The auto-heal loop retries blind

`verify_and_heal()` (core/verify.py) builds a `heal_task` that contains the build output and passes it as `AgentRunRequest.task` to a child run. But the executor used by `run_delegated` (platform/delegation.py:94-107) ignores its `ctx` argument and calls `run_bot(bot, task, ...)` with the **original `task` captured by the closure**. Every healing attempt therefore re-runs the original task text with none of the failure output. The bounded retry only works if the model happens to fix the build on its own the second time. The existing test (tests/test_delegation.py:48) passes because its provider ignores messages and the fake build script fails once by design.

Fix: `task=ctx.run.task` inside the executor (and pass the failure text as `extra_context`). Add a test whose provider records the messages it receives and asserts the verify output appears in the second call.

### A-03 (P1) The ledger has no WAL and a 10 s lock timeout

`db/ledger.py:_connect()` uses `sqlite3.connect(timeout=10)` with the default rollback journal. In that mode a reader blocks a writer and a writer blocks readers. With the API answering REST reads on worker threads while a turn writes tasks, results and file diffs, a reader can be stalled by a write (and the reverse). The old UI-thread version of this problem is gone; the lock mode is not.

Fix: enable `PRAGMA journal_mode=WAL`, `synchronous=NORMAL` and `busy_timeout=5000` once at first connect for `catcrew.db` (the state DB already uses WAL); keep the module-level write lock only for cross-thread ordering of multi-statement writes. Add a test that reads while a long write transaction is open.

### A-04 (done in the pivot, gaps remain) The event bus

`platform/bus.py` is now a thread-safe in-process bus. The activity board, the ledger, the SOP, the run supervisor and `ChatService` publish to it, and `api/ws.py` fans every event out on `/ws/events`, so the UI no longer polls. Remaining gaps: (1) the bus has no replay, so a client that reconnects must refetch over REST (documented, and done on every `hello`); (2) the delegation engine's `RunSupervisor` state changes are not published as their own event type (runs only surface through tasks and activity); (3) `approval.requested` is not re-listed after a reconnect (see U-01).

### A-05 (P1) `report_task_results` is synchronous

The tool (tools.py:1076) writes the ledger, rewrites `PROJECT_STRUCTURE.md`, then calls `maybe_propose_skill` (platform/auto_skill.py:93-122). The docstring calls that "fire-and-forget", but `_propose` calls `provider.chat(...)` inline (auto_skill.py:118): the specialist's tool round waits for a full extra model round trip, and with a single local GGUF the call occupies the same inference slot that the next phase is waiting for. `refresh_project_structure_doc` is also a synchronous file write that queries the ledger three times.

Fix: after `add_task_result` returns, emit a `task_reported` event and return immediately. A single background consumer (a daemon thread or the engine loop) handles the rest: regenerate `PROJECT_STRUCTURE.md` debounced per project (coalesce bursts into one write) and run the skill classifier only when the queue is idle and the local-inference semaphore is free. Log both steps to the Activity Timeline so they remain visible.

### A-06 (P1) Whole-file, non-atomic JSON writes

`save_session()` (sessions.py:225-236) serialises the entire session, every message, with `indent=2`, `write_text`s it, then updates the index, and it is called from `ChatService` at the end of every turn and on every message. Same truncate-then-write hazard as A-01, and cost grows with conversation length. Fix: atomic replace (as above), compact JSON (no indent) for sessions, append-only JSONL for messages with periodic snapshots, and run the write on a worker.

### A-07 (P2) Engine and supervisor overheads

`RunSupervisor` calls `RunRepo.create/update` synchronously from coroutines (core/supervisor.py:97, 127-139); each call opens a connection, sets WAL, and `db/state/db.py:ensure_schema` re-reads `schema.sql` from disk and runs the whole script every time (db/state/db.py:32-36, 55). Blocking SQLite inside the loop is harmless at one run at a time but breaks the moment runs are concurrent (see backlog item on concurrent specialists). Fix: memoise schema creation per path, and wrap repo calls in `asyncio.to_thread` or a single writer thread. Also `run_delegated` creates and tears down a whole `CatCrewEngine` per call (delegation.py:142-160); keep one long-lived engine bound to the store path and make the path a constructor argument for tests.

### A-08 (P2) Unhandled locks and orphaned runs

There is no retry on `sqlite3.OperationalError: database is locked`; most callers wrap DB access in a bare `except Exception` and return an empty list, which hides failures (the tasks endpoint would show an empty list instead of an error). No code reaps `runs` rows that were `running` when the process died: `catcrew_state.db` currently holds five such rows. Fix: a small `retry_locked()` wrapper with jittered backoff at the connection helpers, log-and-surface errors in the UI, and on startup mark any `running` or `pending` rows as `failed` with `error='app exited'` (additive `UPDATE`, no schema change).

Note: this run also found the `catcrew.db.corrupt-1789764549` file next to the ledger. Determine what wrote it (two processes? the CLI and the API server both open the same file) before it happens again; `services/instance_lock.py` covers only the API server, not the CLI.

---

---

## 3. Context window and token efficiency

### C-01 (P1) Phase hand-off accumulates everything

In `platform/sop.py` the variable `prior` grows by `turn.text` after every phase (platform/sop.py:467, 501, 515, 534) and is passed whole to each subsequent specialist through `extra_context`. The stored copy in `phase_outputs` is capped at 4000 characters (platform/sop.py:324) but the string handed to the model is not. By the QA phase the prompt carries the blueprint, architecture, distribution, devops, build and design outputs verbatim, including anything a model pasted into chat despite the rules. This is the opposite of the isolation the architecture calls for.

Fix: a hand-off contract per role. For each downstream role define which upstream artifacts it needs (for example QA needs the task list and the build result, not the blueprint) and pass a bounded summary plus file paths, not transcripts. Have each specialist write its artifact to disk (`BLUEPRINT.md`, `ARCHITECTURE.md`, `TASKS.md`) and reference the path; the next specialist reads it with a tool only if needed. Cap any inline hand-off at about 1500 characters.

### C-02 (P1) A live clock in the middle of the prompt

Done (2026-10-05): the clock, project shared state, recent memory, recalled memories, the clarified spec, the checklist and on-demand skills now ride in a turn note on the newest user message (`harness/turn_note.py`, `platform/chief.turn_context`); system prompts are stable between messages (`tests/test_prompt_cache.py`). Claude models via OpenRouter also get a moving `cache_control` breakpoint on the newest message, and every model request is traced as `model_call` with prompt / cached / completion tokens. Still open: a deferred tool loaded mid-turn changes the tool list and so the cached prefix.

`_internet_rules()` (tools.py:3087) inserts `here_and_now()` (minute-resolution "Now: ... HH:MM") and the hardware and Docker one-liners before the long static tool text, identity, AGENTS.md and skills. Local model-server prompt caching and provider-side caching both match on the longest identical prefix, so every minute invalidates everything after that line. Fix: order the prompt static-first (persona, rules, tool notes, identity, AGENTS.md, skills) and append volatile facts (clock, hardware, workspace, checklist) last, or inject them into the latest user message. Round the clock to the hour where minute precision is not needed.

### C-03 (P1) Prompt building at import time runs subprocesses

Partly done (2026-10-05): the unused `SYSTEM_PROMPT` constant is gone, so importing `backend.tools` no longer builds a prompt or runs the probes. The probes still run on the calling thread when their cache expires.

`SYSTEM_PROMPT = build_system_prompt()` (tools.py:3298) executes at module import, and nothing reads `SYSTEM_PROMPT`. Building the default prompt calls `_internet_rules()`, which calls `probe_hardware()` (GPU query) and `probe_docker()` (`docker version` with a 6 s timeout, then `docker compose version` with 4 s timeouts). That happens at import, before the API accepts its first request; a stalled Docker Desktop can add over ten seconds to startup. The probes also re-run on the calling thread each time their cache TTL expires.

Fix: delete the constant. Move the probes to one background refresh thread that populates a cache at startup and refreshes it periodically; `_internet_rules()` reads the cache and never blocks.

### C-04 (P2) Tool descriptions are paid for twice

`build_system_prompt` (tools.py:3110-3298) writes a prose list of every tool for the no-project developer-mode chat while the same tools are sent as JSON schemas through function calling. That same mode passes the full built-in `TOOLS_SCHEMA` (tools.py:3065-3085); project-room, 1:1 and delegated turns are role-filtered (`schema_for_direct_bot`, `builtin_names_for_role`), so the cost is concentrated in one mode. The comment in `scripts/seed_role_tools.py` claiming the legacy schema is "available to every bot unconditionally" is stale.

Fix: remove the prose tool list from the prompt (keep only behavioural rules), give the developer-mode chat a role pack like the others, and measure the schema size per role (`len(json.dumps(schema))`) to set a budget; target under about 4k tokens of tool schema for a specialist. Cache `function_to_schema` results per handler path and file mtime so schemas are not regenerated every turn (tool_registry.py:214, 268). Note that `tools_schema_for` calls `register_agent_tools()` (database reads and handler imports) at the start of each turn (tools.py:3048-3050); see A-03.

### C-05 (P2) Compaction has one fixed threshold

Done: `services/compaction.py` (`ContextBudget`) works in tokens and runs before every model call. Over 8,000 tokens (`CATCREW_CONTEXT_TOKENS`) the older turns are summarised by the Memory-role model (Chat when unset) and the recent turns stay verbatim, so a costly model receives the summary instead of the whole history. Still open: the threshold is one number for every model. Model discovery already returns `context_length` for hosts that report it (LM Studio does), so the budget could be a share of the real window of the model that will receive the request. The estimate is characters / 4 (fine for a trigger, not exact; `tiktoken` where available would be better), and large tool results are truncated but not offloaded or summarised before user turns are.

### C-06 (P2) Identity and house-rule budgets

Every chat turn carries the identity document (`_IDENTITY_BUDGET` 20 000 characters, now about 5 KB after the rewrite) and up to 12 000 characters of `AGENTS.md`. Keep both short and stable, which also helps C-02. Skills stay lazy through `read_skill`; add a test that fails if the skills index for any role exceeds a set size.

---

---

## 4. UI and UX

### U-01 (P1) Approvals and questions are not re-listed after a reload

Approvals and ask-user prompts are now first-class: `approval.requested` carries `approval_id`, `bot_id`, `tool`, `detail` and the project id, the UI answers with `POST /api/approvals/{id}`, and they belong to the project room rather than to whichever chat is open, so switching threads no longer hides them. What is left: the pending list exists only in memory on the server and in the store on the client. A reload, or a reconnect after `hello`, drops the card while the worker thread is still blocked waiting. Fix: add `GET /api/approvals` (and `/api/asks`), fetch it in `bootstrap` and on every `hello`, and block the worker on a `concurrent.futures.Future` with a timeout instead of polling an event.

Verify: start a turn that needs approval, reload the page; the card must reappear and the agent must resume when it is answered.

---

## 5. Repository, packaging and tests

### N-01 (P1) Stale and uncollectable tests

Already failing before the pivot (confirmed against the pre-pivot tree, not caused by it): two modules cannot be imported (`test_arcane_site` needs the missing `scripts/arcane_site.py`, and `test_sop` imports a name that no longer exists, `sop.Team`), and 17 tests fail (`test_platform` 11, mostly the removed "Teams" feature, `test_memory` 2, and one each in `test_computer`, `test_hardware`, `test_mcp_tools`, `test_modes`). The model-management work removed the other stale modules together with the features they covered (`test_downloads`, `test_gguf_browser`, `test_llama_server`, `test_local_models`, `test_model_recs`), rewrote `test_lm_runtime` and fixed the four `test_local_errors` failures. Decide per remaining module: fix, or delete with the feature. A green suite is the precondition for refactoring anything in section 2.

### N-02 (P1) Secrets

`data/settings.json` stores API keys in plain text (the Hugging Face key is gone: CatCrewAI no longer downloads models, and a leftover `huggingface_key` is deleted when the engine starts). Keys are now entered in the UI (Models → Hosts → Set key, `PUT /api/keys/{id}`), and the API never returns them (only `configured` and `source`); the file is written owner-only where the OS supports it. It is still one backup away from leaking. Two secrets (an LM Studio key and a Hugging Face token) were seen in that file during this work: **rotate both**. Then move secrets to the OS credential store (`keyring`) and keep only references in settings. Also keep `.env` out of every sync and commit (it is in `.gitignore`).

### N-03 (P1) No release build for the new stack

The desktop shell runs from source: `npm run tauri dev` (or `tauri build`) starts the window, and the Rust side launches `python -m backend` from the repository (`CATCREW_REPO`, `CATCREW_PYTHON`) or attaches to one that is already running. A shipped installer still needs: the backend frozen as a Tauri sidecar (PyInstaller `--onedir`, including `backend/skills/`, `backend/prompts/`, `backend/identity.md`, `backend/db/state/schema.sql`, `backend/platform/defaults.json`, `assets/` and the connector catalog), a frozen-aware `BASE_DIR` in `config.py` (it uses `__file__`), a per-user data directory instead of `<repo>/data`, and code signing. The old PyInstaller + Inno Setup scripts are in `legacy/packaging/` for reference; their hidden-import list names many modules that no longer exist.

### N-04 (P2) One turn at a time

`ChatService` runs a single turn; a message sent to another project meanwhile is queued. The cause is process-global state: the workspace folder set and the tool allow-list (`services/workspace`, `harness/deferred`). Making them per-turn (`ContextVar`, see `core/context.py:AgentContext`) is the prerequisite for two projects working in parallel and for running independent specialists concurrently (section 6).

### N-05 (P2) Self-modify restart

`restart_catcrew` and the end-of-turn auto restart (`services/pending_restart.py`, `self_modifier.restart_app`) re-execute `main.py`. On Linux and macOS `os.execl` keeps the process id, so the desktop shell's handle stays valid. On Windows it starts a new process and exits the old one: the shell's handle goes stale (the shell still stops the backend on exit through `POST /api/shutdown`), and an in-flight turn is cut off after 1.5 s. Fix: a supervised restart (exit code 75 and a respawn in the shell), and never auto-restart while a turn is running.

### N-06 (P2) Repeated `bot.staffed`

Staffing a cat emits `bot.staffed` more than once for the same cat within a turn. The client treats it as an idempotent upsert (and only animates the first), so nothing breaks, but the emitter should publish once (`staff_bot_on_project` / the SOP phase start).

### N-07 (P2) Presentation text stored inside message text

Delegated results are stored with a leading `[Name / role]` banner and a trailing "Computer after this turn: ..." note, because the model history has always seen them. `core/messages.py` strips both on the wire (messages and task summaries), but the stored text still carries them. Move them into structured fields (`speaker`, and a separate `runner_note`) and let the history builder render them for the model only.

### N-08 (P2) No token streaming

The API contract has no `chat.stream`. The pre-pivot app never streamed tokens either (it batched text into a bubble), so a reply arrives as one `chat.message` after the turn's steps. If streaming is wanted, add it in the providers first (`providers/openai_compat.py` already receives SSE chunks), then a `chat.stream` event and a growing bubble in `MessageView`.

### N-09 (P2) Hygiene

- **Repository.** The git repository had no commits and a stale zero-byte `.git/index.lock` (17 September) that blocks `git add`. With no git process running, delete the lock, then commit. The folder is still named `car-crew-ai` on disk; rename it to `cat-crew-ai`. Stale `build/`, `dist/` and `console/` artifacts from the old app can be deleted.
- **Dependencies.** `requirements.txt` is now the API's dependency set with no upper bounds and no lock file. `numpy` and `sentence-transformers` are not imported directly (mem0 may pull them in): verify and prune. `huggingface_hub` was dropped from the list (nothing imports it; `sentence-transformers` brings it in for the embedder cache). `playwright` is now declared in `requirements.txt`; only the Chromium download still happens on demand inside the first browser tool call (`tools._ensure_playwright()`, up to 180 s, retried at most every 10 minutes after a failure): pre-install it in the release build (N-03).
- **Dead code.** The voice, wake-word, overlay and particle-body modules were removed, but `services/settings_store.py` still carries their settings sections (`character_window`, `audio`, `look`, `speak`, `life`) so old `settings.json` files load unchanged; drop them together with a settings migration. `backend/db/legacy.py` (old module-path mapping for stored tool handlers), `platform/catcrew.py:migrate_legacy_arcane`, `db/state/repositories.RegistryRepo` (built, unwired, see A-01) and the "Team Arcane" fixtures (`Ekko`, `Caitlyn`) should each be kept or deleted deliberately. `legacy/` holds the retired packager and the Mixamo animation assets; delete it once nobody needs it.
- **Size and shape.** `tools.py` is about 3 300 lines and should be split into per-domain modules. `ContextVar` state is spread over six modules (`harness/runtime.py`, `platform/runtime.py`, `harness/deferred.py`, `harness/approvals.py`, `harness/ask_user.py`, `services/trace.py`); `core/context.py:AgentContext` was written to replace it and is not yet used.
- **Paths in the catalog.** The catalog stores absolute paths (avatars, scratch tool results) that break when the repository moves; store them relative to `BASE_DIR` or `CATCREW_HOME`. The API already returns repo-relative skill paths and avatar URLs.

### N-10 (P2) Desktop shell test coverage

The React app has unit tests for the reducer, the reconnecting socket, the execution card and the pages (about 100 tests) and was exercised against the real backend with a scripted LLM. The Rust shell has unit tests (7) for its loopback HTTP helper, the tray toggle decision and the once-only quit / notice flags, and `cargo check` / `cargo test` pass, but the window lifecycle (hidden until first paint, failsafe show, backend spawn and stop) and the tray (menu, left-click toggle, close-to-tray, the notification, `quit_app` stopping the backend) have only been reasoned about, not run: this environment has no display and no tray host. The start-up handshake (`BootGate`, `runBoot`) is covered by unit tests and a scripted browser run that starts the backend three seconds late, but the shell side (`backend_status` with a real spawn, a missing Python, a crashing engine) is only unit-tested, not run. Run `npm run tauri dev` on Windows once, and add a WebDriver smoke test (`tauri-driver`) that opens the window, waits for `hello`, creates a project and sees a cat pop in.

---

### M-01 (P2) Hosts and the vision tool have not met a live API

The Anthropic host (`/v1/models` with `x-api-key` and `anthropic-version`, chat through its OpenAI-compatible endpoint), the Gemini `v1beta/models` list and the `image_url` message that `browser_look` sends were exercised against fake responses and a fake LM Studio server, and the Models UI against the real backend. Nobody has run them with a real key. Fix: one manual pass per host with a real key (list, pick, chat, and for a Vision model one `browser_look`), then keep a recorded response per host as a test fixture. Also check how each host answers when a model is deprecated (a `404` at chat time versus a missing list entry), because the start-up notice only catches the second.

### M-02 (P2) Pixel and Aegis keep their old prompts on existing installs

`platform/catcrew.py` seeds a cat's prompt once. The wording that mentions the browser tools and `browser_look` therefore reaches new installs only; an existing install keeps the old text. The tools do not depend on it: each tool's docstring (its schema description) says when to use it, and the tool set itself arrives through `ensure_role_tools`. Fix: an explicit "reset prompt to default" action on the Agents page, or a versioned prompt refresh that leaves user-edited prompts alone.

## Notes on scope

- **"Hermes auto-building"** in the pivot brief was interpreted as the **auto-skill builder** (`platform/auto_skill.py`): after a task it may propose a reusable playbook, which the API lists at `GET /api/skills/proposals` and announces with the `skill.proposed` event, and a person approves or dismisses it on the Skills page. If Hermes meant something else (a model, an external agent), say so.
- **Removed with the Qt UI:** the overlay and particle body, voice (speech to text, text to speech, wake word), the terminal / Git / DB / file-explorer panels, the local-model download flyouts and the Qt settings pages. Their backend services (`services/terminal`, `git_ops`, ...) are still in place and can be exposed through the API when the UI needs them. The local-model download and GGUF services (`llama_server`, `downloads`, `local_models`, `gguf_browser`, `model_recs`) were deleted in the model-management work: LM Studio owns local models now.

---

## 6. Roadmap carried over from the retired planning documents

These items were open in the documents removed during this clean-up. They are product decisions, not defects, and are listed so nothing is lost.

| Item | Origin | Status |
| --- | --- | --- |
| Run independent specialists concurrently (for example architect and designer) with `asyncio.gather`; supported by `core/`, but changes what later specialists see | Delegation engine notes | Open, needs a product decision (interacts with C-01) |
| Wire `db/state` `RegistryRepo` in place of `registry.json` behind a staged migration (`migrate_from_registry_json.py`, run against a copy first) | Delegation engine notes | Built and tested, not wired (A-01) |
| Route `harness/subagent.run_subagent` through the engine and give subagents an `AgentRun` identity | Delegation engine notes | Open |
| Event bus for Activity Log entries, "Open Chat" and two-way global-state sync into open 1:1 views | Delegation engine notes | Done for the wire (`bus.py` + `/ws/events`); see A-04 |
| Compaction polish | Harness backlog 4.3 | Open (C-05) |
| Git-backed skill install | Harness backlog 4.5 | Open |
| Scheduled and cron agents (routines that start work without a prompt) | Harness backlog 6.1 | Open, low priority |
| Generative UI cards (charts and tables in chat) | Harness backlog 6.2 | Open |
| Full OAuth loopback for connectors | Harness backlog 6.3 | Open |
| Agent library and multi-agent presets | Harness backlog 6.4 | Open, product decision |
| Orchestrator and worker model: CLI agents such as aider as subprocess executors, communicating through files and git; QA as a script that feeds stderr back | Orchestrator notes | "Think later" |
| Bot roster UX from the Grok Bot reference: presence on avatars, the computer as status then preview then takeover, routines | Grok Bot references | Partly done (roster, presence pill) |

Already shipped from that backlog and no longer tracked: connector catalog with GitHub, Linear, Notion, Slack and MCP over HTTP; activity timeline and post-hoc viewer; approvals and ask-user; deferred tools and large-result offload; subagent and code mode; Docker detection and skill.

---

---

### N-11 (P2) The Cat Crew intent router is a keyword heuristic

`platform/orchestrator.classify_intent` decides chat / work / mention without a model call: request verbs and deliverable nouns raise a work score, question openers and greetings raise a chat score, and keyword groups pick the specialists (`roles_for`). It is deterministic and tested (`tests/test_orchestrator.py`), but it will misjudge phrasing it has no words for: a work request in a language other than English or Bulgarian reads as chat, and a chatty message that happens to contain "build" and "app" reads as work. The safety valves are the chat path itself (the model can call `hand_off_to_crew` when a conversation turns into work) and `@name` (always direct).

Fix: keep the heuristic as the fast path, add a small classification call only when the score is close (`abs(work - chat) <= 1`), and show the routing decision as a chip on the turn ("Routed to Pulse") with a one-click "Talk instead" that re-runs the message as chat.

### N-12 (P2) Legacy `chief` shims

Cat Crew's id changed from `chief` to `crew`. Old data keeps working through read-time normalisation (`platform/orchestrator.is_room_id` / `canonical_room_id`): sessions written with `bot_id: "chief"` are found as Cat Crew's thread, `chief` rows in `agent_skills` / `agent_tools` are hidden from every view (and left in place: additive migrations only), and the singleton config row in the `chief` table is rewritten as `crew` on the next registry save. Once every install has been opened once, a one-off migration can rewrite the session files, delete the orphaned assignment rows and rename the table, and the shims (`_thread_owner` in `services/sessions.py`, `_is_orchestrator` in `db/ledger.py`, `LEGACY_ROOM_IDS`, `isRoomId` in `frontend/src/lib/types.ts`) can go.

### N-13 (P2) Tray: no single-instance guard, notice once per run

Close-to-tray makes a second launch likely (the user clicks the Start menu entry while CatCrewAI sits in the tray). Today a second process opens a second window and a second tray icon; it attaches to the running backend (so nothing corrupts), but it is not what anyone wants. Add `tauri-plugin-single-instance` so a second launch just calls `show_main` on the first. Also: the "running in the background" notification is once per run (a flag in `Lifecycle`), not once per install; persist a marker under the app data directory if "first time ever" is what is wanted. On Linux the tray needs `libayatana-appindicator3` at runtime and a desktop that hosts status icons (GNOME needs an extension); without one the app still works but has no way back once hidden, so consider not hiding when no tray host exists. Windows' tray-icon overflow may hide the icon by default: the taskbar's "show hidden icons" arrow holds it until the user pins it.

---

## 7. Suggested order of work

1. A-01, A-02, U-01, N-02 (data loss, a broken retry loop, a stuck agent, leaked secrets). Each is small.
2. N-01 (a green test suite), then A-03 and A-06 to A-08.
3. C-03, C-02, C-01 (startup time and prompt cost), then A-05.
4. N-06, N-07 (clean up the wire), then N-04 (parallel projects) with the section 6 concurrency item.
5. N-03 and N-10 before the next release build; M-01 before anyone relies on the Anthropic host or the Vision role.
6. N-11 when users report a misrouted message; N-12 with the next data migration; N-13 (single instance) before anyone else uses the desktop app.
