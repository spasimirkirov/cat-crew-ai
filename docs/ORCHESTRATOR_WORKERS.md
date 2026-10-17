# Later: Orchestrator–Worker (CLI agents as executors)

**Status: think later — do not implement yet.** Product home stays Grok-style Bots (roster + chat). This is a *runtime* idea for how specialists do the black work, not a UI rewrite.

## The idea

Python is the smart brain. Existing CLI tools are the workers. No heavy bridge.

| Role | Process | Job |
| --- | --- | --- |
| **Orchestrator** | Chief / PO in Jinx Python | Holds project context, writes shared state, assigns tasks |
| **Workers** | Subprocesses Jinx already knows how to run (`run_command` / Terminal) | Black work: edit repo, run tests, emit markdown |

Bots do **not** chat with each other in an endless loop. They communicate through **files + git**:

1. Chief accepts “Build a marketplace” → writes `project_state.json` (or we keep `data/platform` project state + scratch TODO).
2. Architect: one API call to a strong model → `docs/architecture.md` + commit.
3. PO (Python): reads the architecture → `tasks.json` (checklist).
4. Developer worker: `aider --message "Implement Task #1 following docs/architecture.md" --no-auto-commits`.
5. QA worker: pytest / Playwright; on fail, feed stderr back to aider: “Task #1 failed with this stack. Fix it.”

Why it saves months: aider already owns repo map (AST), surgical edits, git, and retry. QA does not need an LLM loop — it is a script that captures stdout/stderr. Architect / marketing mostly emit `.md`, so a direct chat completion is enough.

## How this sits on today’s Jinx

Already close:

- Chief + `delegate_to_bot` (in-process nested `run_agent`, same llama-server).
- Shared computer: Files / Terminal / Git.
- File bus: `update_todo` → `data/scratch/<session>/TODO.md`, project `state` / events in the registry.
- `run_command` streams into Terminal; Stop kills the tree.

What would change later (if we do it):

- **Developer bot** may *launch* a coding CLI (aider, or our own tools) instead of re-implementing repo-map + patch loops in the chat LLM.
- **QA bot** becomes “run the test command, return the log” — not another 12-round tool loop.
- **Architect** stays a single completion that writes markdown (cloud host we already have: DeepSeek / Gemini / Groq — no new litellm dependency unless it earns its keep).
- User-facing Grok shape stays: you message Chief (or a specialist). The worker is behind presence: working / waiting / done.

## Constraints (do not ignore later)

- One local GGUF in VRAM. Aider wants **its own** model/API. Two brains at once will fight VRAM unless Coding role is offloaded to cloud or aider uses a cloud key.
- Do not wrap aider as a second product UI. Headless CLI only; Jinx owns roster, approvals, Terminal.
- `--no-auto-commits` (or Jinx git panel) so the user still owns history.
- Prefer files we already have (`TODO.md`, project state, `AGENTS.md`) over a new parallel `tasks.json` unless the old checklist is too weak.
- No Node/pi.dev runtime. Aider is Python CLI — that is allowed. Do not port aider internals.
- Nested `delegate_to_bot` stays for cheap local specialists; CLI workers are an *optional* pack on the Developer role when the workspace is a real repo.

## First slice (when we pick this up)

1. Skill or bot tool: `run_aider` / generic `run_coding_cli` with a message + cwd, streamed to Terminal.
2. QA: `run_command` pytest already exists — Chief/QA prompt should *prefer* that over rereading the whole repo.
3. Architect: one `write_file` of `docs/architecture.md` from a cloud completion (Coding/Chat role), then `git_add` if the user wants.

Grok UX + this runtime can coexist: roster is how you talk to them; files/git/CLI is how they hand work to each other.
