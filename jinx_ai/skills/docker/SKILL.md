---
name: docker
description: >-
  When Docker Engine is on this PC — optional command sandboxes and MCP/toolbox
  containers. Not required for search or everyday host Terminal coding.
---

# Docker

Call `docker_info` (or read the Docker line in the system prompt) before assuming
containers work. If Docker is missing or the engine is stopped, say so and use
the host Terminal instead.

## What Docker is for in Jinx

Two good uses — **not** a replacement for normal coding:

1. **MCP / toolbox containers** (preferred for connectors)  
   Run a one-shot or long-lived tool server image when we need an isolated
   toolbox (future Connectors / MCP HTTP). Example pattern:
   `docker run --rm -i <image>` or `docker compose up -d` for a stack the user
   asked for. Keep credentials in env/files the user controls — never bake
   secrets into images.

2. **Optional command sandbox**  
   For risky or version-pinned commands (unknown npm script, one-off Linux
   toolchain) use an ephemeral container with the workspace mounted:
   ```
   docker run --rm -v "<workspace>:/work" -w /work <image> <command>
   ```
   Prefer this over installing random global tools on Windows. Still pass
   `cwd` via `run_command` when the CLI itself runs on the host.

## What Docker is NOT for

- Web search (DuckDuckGo / SearXNG URL) — no Docker required.
- Everyday `npm` / `composer` / `pytest` on a project already set up on the host.
- Replacing the Developer Terminal for normal agent loops.
- Running the whole Jinx app or the LLM inside Docker by default.

## Rules

- Detect first: if `docker_info` says unavailable, do not invent container steps.
- Prefer `--rm` for one-shots; name containers only when the user wants them kept.
- Mount only the workspace (or a subfolder) the user attached — never the whole disk.
- Do not `docker system prune` or delete volumes unless they explicitly ask.
- Stop / remove containers you started for a turn when the user does not need them.
- On Windows, paths in `-v` must be absolute Windows paths Docker Desktop can mount.

## Host tools

- `docker_info` — Engine / Compose / context.
- `run_command` — `docker …` / `docker compose …` with a sensible `cwd`.
- After long `docker compose up`, `read_terminal` for port / build errors.
