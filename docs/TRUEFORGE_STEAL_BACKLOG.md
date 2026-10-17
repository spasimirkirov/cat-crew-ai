# TrueForge → Jinx steal backlog

Steal **harness ideas** from [truefoundry/trueforge](https://github.com/truefoundry/trueforge) into **Jinx AI** (Python / PyQt6). Do **not** port Node/TypeScript, React UI SDK, Helm, Postgres/Redis hosted mode, or run TrueForge as a dependency.

**Product framing:** Jinx AI = local AI agents (coding + autonomous tasks) + Assistant toolset. Home → Members / Teams / Projects. Not a “companion”.

### Status (read this first)

**Worth-it items are implemented.** Full product picture: [JINX.md](JINX.md) → *Big picture* + *Agent harness*.

| Area | State |
| --- | --- |
| Connectors catalog + GitHub / Linear / Notion / Slack + MCP HTTP | Done |
| Activity timeline, tool previews, post-hoc viewer | Done |
| Approvals + ask-user | Done |
| Deferred tools + large-result offload | Done |
| Skills catalog UX | Done |
| Subagent + code mode | Done |
| Docker detect + skill | Done |
| Leftovers | Optional polish only (OAuth loopback, git skill install, compaction polish, schedules, generative UI cards, agent library) |

**How to use this file:** optional leftovers below stay unchecked until someone wants them. Prefer updating [JINX.md](JINX.md) when shipping product surface.

Base: `https://github.com/truefoundry/trueforge/blob/main/` (files) · `…/tree/main/` (folders)

---

## What TrueForge is (detailed skim)

| Layer | What they ship | Steal? |
| --- | --- | --- |
| Agent harness core | Loop, MCP tools, skills, sandbox-as-tool, compaction, subagents, Code Mode, large-result offload, deferred tools | Ideas yes; Python rewrite |
| Catalogs | YAML presets for models / MCP / skills / sandbox | Yes — Jinx JSON/YAML catalogs |
| Connectors UI | Settings → Connectors, logos, OAuth in-chat | Yes — PyQt “Connectors” |
| Chat / events | Turn event stream, tool cards, approvals, ask-user, timeline | Yes — enrich our activity + transcript |
| Skills | Git-backed `SKILL.md` + progressive disclosure | Partial — we have local skills; add catalog/UI |
| Sandbox | Daytona / TFY remote sandbox | Later / optional; we have Terminal + workspace |
| Hosted / Helm / OIDC | Team deploy | No |
| React `@trueforge/trueforge-ui` | Web chat SDK | No — keep PyQt |

### Shipped catalogs (discover + copy shapes)

- [packages/trueforge/catalog/mcp-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/mcp-catalog.yaml)
- [packages/trueforge/catalog/model-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/model-catalog.yaml)
- [packages/trueforge/catalog/skill-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/skill-catalog.yaml)
- [packages/trueforge/catalog/sandbox-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/sandbox-catalog.yaml)

### Docs worth reading once

- [docs/mcp-servers.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/mcp-servers.mdx) · https://trueforge.dev/mcp-servers
- [docs/skills.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/skills.mdx) · https://trueforge.dev/skills
- [docs/key-features/overview](https://trueforge.dev/key-features/overview)
- [docs/key-features/deferred-tool-loading.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/key-features/deferred-tool-loading.mdx)
- [docs/key-features/large-tool-responses.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/key-features/large-tool-responses.mdx)
- [docs/key-features/subagents.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/key-features/subagents.mdx)
- [docs/key-features/code-mode.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/key-features/code-mode.mdx)
- [docs/ui-sdk/reference/events.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/ui-sdk/reference/events.mdx) · https://trueforge.dev/ui-sdk/reference/events
- [docs/chat-ui.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/chat-ui.mdx)

---

## Phase 0 — Architecture contract (do first)

### [x] 0.1 Write Jinx harness event model (Python dataclasses)

Define a small event vocabulary aligned with TrueForge turns (names can differ; shapes matter): `turn_start`, `activity`, `tool_call`, `tool_result`, `approval_required`, `auth_required`, `ask_user`, `turn_end`, `error`.

**TrueForge refs**

- [packages/trueforge-core/src/agent-session/schemas/events.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/agent-session/schemas/events.ts)
- [packages/trueforge/src/schemas/events.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/src/schemas/events.ts)
- [packages/trueforge-sdk/src/events.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/events.ts)
- [docs/ui-sdk/reference/events.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/ui-sdk/reference/events.mdx)
- Types: [ToolCall.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/ToolCall.ts), [ToolApprovalRequiredEvent.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/ToolApprovalRequiredEvent.ts), [McpAuthRequiredEvent.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/McpAuthRequiredEvent.ts)

**Jinx targets**

- New: `jinx_ai/harness/events.py` (or extend `jinx_ai/trace.py`)
- Wire: `jinx_ai/providers/base.py` (agent loop), `jinx_ai/activity.py`, `jinx_ai/chat_transcript.py`

### [x] 0.2 Map “catalog + configured resource” pattern

Catalog = discovery presets. Configured = credentials + enable in `data/settings.json` (or `data/connectors.json`). Agents/sessions only reference **names**, never secrets.

**TrueForge refs**

- [docs/harness initial-setup](https://trueforge.dev/harness/initial-setup)
- Folder: [packages/trueforge/catalog/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge/catalog)
- UI adapter: [packages/trueforge-ui/.../catalogs/connectorCatalog.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/plugins/trueforge-agent-server-adapter/catalogs/connectorCatalog.ts)
- Docs catalog port: [docs/ui-sdk/reference/catalog.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/ui-sdk/reference/catalog.mdx)

**Jinx targets**

- New: `jinx_ai/connectors/catalog.py`, `jinx_ai/connectors/store.py`
- Existing: `jinx_ai/settings_store.py`, `jinx_ai/api_access.py`

### [x] 0.3 Document “built-in vs connector” tool split in JINX.md

Built-in (always local): files, shell, git, search, Comfy, desktop. Connectors (optional): GitHub, Linear, Notion, Slack, …

**Jinx targets:** `docs/JINX.md`, `AGENTS.md`

---

## Phase 1 — Connectors foundation

### [x] 1.1 Connector data model + persistence

Fields: `id`, `name`, `kind` (`builtin_http` | `mcp_http` | `python`), `enabled`, `auth` (none / header / oauth_token), `base_url`, `headers` (secrets redacted on read), `preload_tools` bool, `tool_allowlist` / deny.

**TrueForge refs**

- MCP core folder: [packages/trueforge-core/src/core/mcp/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge-core/src/core/mcp)
- [RemoteMCP.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/RemoteMCP.ts)
- [IMCPServer.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/IMCPServer.ts)
- [ToolSet.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/ToolSet.ts)
- [convertMCPServers.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/convertMCPServers.ts)
- Catalog YAML: [mcp-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/mcp-catalog.yaml)

**Jinx targets**

- `jinx_ai/connectors/__init__.py`, `model.py`, `store.py`
- Persist under `data/connectors.json` (gitignored via `data/`)

### [x] 1.2 Shipped connector catalog (YAML/JSON in repo)

Seed presets inspired by TrueForge catalog (GitHub, Linear, Notion, Slack, Atlassian, …). Each entry: display name, description, icon kind, default URL or “python driver”, auth type, docs URL.

**TrueForge refs**

- [packages/trueforge/catalog/mcp-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/mcp-catalog.yaml)
- Screenshots: [docs/images/mcp-connectors.png](https://github.com/truefoundry/trueforge/blob/main/docs/images/mcp-connectors.png), [docs/assets/getting-started-connectors.png](https://github.com/truefoundry/trueforge/blob/main/docs/assets/getting-started-connectors.png)
- UI stubs/logos: [packages/trueforge-ui/src/atoms/draft/mcpConnectorStubs.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/atoms/draft/mcpConnectorStubs.ts)

**Jinx targets**

- `jinx_ai/connectors/catalog.json` (or `.yaml`)
- Icons: map FA kinds in `jinx_ai/icon.py`

### [x] 1.3 Settings → Connectors page (PyQt)

List catalog + configured connectors. Enable `IconToggle`. Add custom URL. Edit details (auth, test connection). Match Jinx chrome (not React).

**TrueForge refs**

- [ConnectorSettings.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/SettingsBuilder/ConnectorSettings.tsx)
- [ConnectorDetails.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/SettingsBuilder/ConnectorDetails.tsx)
- Folder: [packages/trueforge-ui/src/containers/SettingsBuilder/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge-ui/src/containers/SettingsBuilder)

**Jinx targets**

- New: `jinx_ai/connectors_page.py`
- Wire nav in `jinx_ai/chat_window.py` Settings (or AI Models sibling)
- Widgets: `jinx_ai/widgets.py`, `jinx_ai/fa.py`, `jinx_ai/ui_style.py`
- Skill: `.cursor/skills/jinx-ui/SKILL.md`

### [x] 1.4 MCP HTTP client (Python) — list tools + call tool

Prefer official MCP Python SDK or thin JSON-RPC over streamable HTTP. Support header auth first; OAuth later.

**TrueForge refs**

- [executeToolCalls.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/executeToolCalls.ts)
- [RemoteMCP.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/RemoteMCP.ts)
- [LocalToolMCP.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/LocalToolMCP.ts)
- [ClientSideTool.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/ClientSideTool.ts)
- [ToolSelectorPolicy.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/ToolSelectorPolicy.ts)
- Docs: [docs/mcp-servers.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/mcp-servers.mdx)

**Jinx targets**

- `jinx_ai/connectors/mcp_client.py`
- Tests: `tests/test_connectors_mcp.py`

### [x] 1.5 Register connector tools into the agent tool schema

When a connector is enabled, expose tools as `connector__{name}__{tool}` or namespaced OpenAI functions. Pass `toolInfo` equivalent so UI can show “GitHub · create_issue”.

**TrueForge refs**

- [ToolCall.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/ToolCall.ts) (`toolInfo.type: "mcp"`)
- [ToolSet.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/mcp/ToolSet.ts)
- Orchestration tests: [packages/trueforge-core/tests/orchestration/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge-core/tests/orchestration)

**Jinx targets**

- `jinx_ai/tools.py` (`tools_schema_for`, `execute_tool`)
- `jinx_ai/providers/base.py`

### [x] 1.6 First concrete connector: GitHub

Issues/PRs/search via MCP URL **or** thin `requests` + token (whichever is reliable offline). Document both.

**TrueForge refs**

- Catalog entry in [mcp-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/mcp-catalog.yaml)
- OAuth helpers (later): [packages/trueforge/src/mcp/auth/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge/src/mcp/auth)

**Jinx targets**

- `jinx_ai/connectors/drivers/github.py` (if not pure MCP)
- Settings → API Access or Connectors token field
- Skill playbook optional: `jinx_ai/skills/` (e.g. `github/SKILL.md`)

### [x] 1.7 Second wave connectors (one PR each)

Order suggestion: **Linear** → **Notion** → **Slack** → custom URL MCP.

**TrueForge refs**

- Same catalog + [docs/mcp-servers.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/mcp-servers.mdx)
- Managed MCP docs (shape only): TrueFoundry Linear/Notion MCP pages linked from catalog descriptions

**Jinx targets**

- `jinx_ai/connectors/drivers/` or MCP-only configs in catalog
- Tests per connector where feasible

### [x] 1.8 In-chat / in-turn auth pause (token missing)

If connector needs auth, pause turn and show Connect / paste-token UI (desktop dialog). Mirror `mcp.auth_required`.

**TrueForge refs**

- [McpAuthRequiredEvent.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/McpAuthRequiredEvent.ts)
- [docs/ui-sdk/setup-custom-ui/mcp-oauth.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/ui-sdk/setup-custom-ui/mcp-oauth.mdx)
- [packages/trueforge/src/apis/mcpOAuth.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/src/apis/mcpOAuth.ts)
- [packages/trueforge/src/routes/mcpOAuthRoutes.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/src/routes/mcpOAuthRoutes.ts)
- UI: [PostMcpOauthScreen.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/McpOauthContainer/PostMcpOauthScreen.tsx)
- Image: [docs/images/mcp-in-chat-auth.png](https://github.com/truefoundry/trueforge/blob/main/docs/images/mcp-in-chat-auth.png)

**Jinx targets**

- `jinx_ai/chat_window.py`, `jinx_ai/chat_transcript.py`, connector store
- Optional local OAuth loopback later (Phase 6)

---

## Phase 2 — Agent activity / chat UX

### [x] 2.1 Tool timeline inside the transcript (not only one activity card)

While a turn runs, show stacked steps: Thinking → Writing file → GitHub create_issue → … Final assistant bubble stays a short summary.

**TrueForge refs**

- Event stream docs: [events.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/ui-sdk/reference/events.mdx)
- Session timeline UI: [AgentSessionEventTimeline.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/atoms/agent-details/AgentSessionEventTimeline.tsx)
- [AgentSessionTimelineContainer.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/AgentSessionTimelineContainer.tsx)
- Utils: [sessionEventTimeline.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/utils/sessionEventTimeline.ts), [buildSessionTimelineSegments.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/utils/buildSessionTimelineSegments.ts), [sessionTimelineEvents.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/utils/sessionTimelineEvents.ts)
- Image: [docs/images/chat-ui.png](https://github.com/truefoundry/trueforge/blob/main/docs/images/chat-ui.png)

**Jinx targets**

- `jinx_ai/chat_transcript.py`, `jinx_ai/activity.py`
- `jinx_ai/trace.py` (already logs tools — feed UI live)

### [x] 2.2 Tool chips with source label (builtin vs connector)

Show `builtin · write_file` or `GitHub · list_issues`.

**TrueForge refs**

- `toolInfo` on [ToolCall.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/ToolCall.ts)
- Composer connector logos changeset concept: [.changeset/pre/20260904170300-composer-connector-logos.md](https://github.com/truefoundry/trueforge/blob/main/.changeset/pre/20260904170300-composer-connector-logos.md)

**Jinx targets**

- `jinx_ai/activity.py`, `jinx_ai/chat_transcript.py`, `jinx_ai/icon.py`

### [x] 2.3 Expandable tool args / result preview in the timeline

Click a step → args JSON + truncated result (full in scratch/trace).

**TrueForge refs**

- [tool.response event](https://trueforge.dev/ui-sdk/reference/events) in events docs
- [LargeToolResponse.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/capabilities/builtins/LargeToolResponse.ts)

**Jinx targets**

- `jinx_ai/chat_transcript.py`, `jinx_ai/trace.py`, `data/scratch/<id>/`

### [x] 2.4 Session activity viewer (post-hoc timeline)

Enhance download / Tree / a panel to replay events like TrueForge session timeline.

**TrueForge refs**

- Timeline folder pieces under [packages/trueforge-ui/src/atoms/agent-details/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge-ui/src/atoms/agent-details)
- Chart helpers: [sessionEventTimelineChart.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/utils/sessionEventTimelineChart.ts)

**Jinx targets**

- `jinx_ai/trace.py`, optional panel in `jinx_ai/chat_window.py`
- Export already: `data/traces/`, `data/scratch/<id>/activity.md`

### [x] 2.5 Composer affordances: show attached connectors / tool count

Subtle status under composer: “3 connectors · 42 tools” (after deferred tools).

**TrueForge refs**

- [ComposerShell.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/atoms/ComposerShell.tsx)
- [ComposerSections.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/atoms/ComposerSections.tsx)
- [ComposerContainer.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/ComposerContainer.tsx)
- [.changeset/composer-tools-count.md](https://github.com/truefoundry/trueforge/blob/main/.changeset/composer-tools-count.md)

**Jinx targets**

- `jinx_ai/composer.py`, `jinx_ai/chat_window.py`

---

## Phase 3 — Human checkpoints (approvals + ask-user)

### [x] 3.1 Tool approval policy

Config: which tools need approval (`run_command`, `git_push`, connector destructive tools, `write_file` outside workspace?). Default allowlist of safe tools.

**TrueForge refs**

- [orchestrationApproval.test.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/tests/orchestration/orchestrationApproval.test.ts)
- [ApprovalAllow.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/ApprovalAllow.ts) / [ApprovalDeny.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/ApprovalDeny.ts) / [ApprovalDecision.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/ApprovalDecision.ts)
- [McpServerApprovalToolSelector.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/McpServerApprovalToolSelector.ts)
- [ToolApprovalRequiredEvent.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/ToolApprovalRequiredEvent.ts)
- [UserToolApprovalEvent.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/UserToolApprovalEvent.ts)

**Jinx targets**

- New: `jinx_ai/approvals.py`
- Settings → General → Agent (or Connectors per-server)
- `jinx_ai/settings_store.py`

### [x] 3.2 Approval UI bar in chat

Pause turn; Allow once / Always allow this tool / Deny. Resume agent loop.

**TrueForge refs**

- [ToolApprovalBar.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/atoms/ToolApprovalBar.tsx)
- [ToolApprovalContainer.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/ToolApprovalContainer.tsx)
- [ApprovalNavBanner.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/atoms/ApprovalNavBanner.tsx)
- [ApprovalNavContainer.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/ApprovalNavContainer.tsx)
- [approvalFocus.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/approvalFocus.tsx)
- [useApprovalNav.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/hooks/useApprovalNav.ts)

**Jinx targets**

- `jinx_ai/chat_transcript.py`, `jinx_ai/chat_window.py`, agent worker thread handshake

### [x] 3.3 Ask-user structured questions mid-run

Capability like clarify but interactive form (choices / short text) mid-tools — complements existing `clarify.py`.

**TrueForge refs**

- [AskUserQuestion.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/capabilities/builtins/AskUserQuestion.ts)
- [AskUserContainer.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/containers/AskUserContainer.tsx)
- [AskUserPromptAdapter.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/atoms/adapters/AskUserPromptAdapter.tsx)
- Image: [docs/images/ask-user-questions.png](https://github.com/truefoundry/trueforge/blob/main/docs/images/ask-user-questions.png)
- Config: [AskUserQuestionsConfig.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/AskUserQuestionsConfig.ts)

**Jinx targets**

- Extend `jinx_ai/clarify.py` or new `jinx_ai/ask_user.py`
- UI cards in `jinx_ai/chat_transcript.py`

---

## Phase 4 — Context engineering (critical for local GGUF)

### [x] 4.1 Deferred tool loading

Default: only expose connector **name + description** (+ a `list_connector_tools` / `load_tools` meta-tool). Full schemas load on demand. Built-in core tools stay preloaded.

**TrueForge refs**

- [docs/key-features/deferred-tool-loading.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/key-features/deferred-tool-loading.mdx)
- [DeferredTool.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/runtime/DeferredTool.ts)
- [deferredLoading.test.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/tests/core/runtime/deferredLoading.test.ts)
- [.changeset/pre/mcp-pill-preload-toggle.md](https://github.com/truefoundry/trueforge/blob/main/.changeset/pre/mcp-pill-preload-toggle.md)

**Jinx targets**

- `jinx_ai/tools.py`, `jinx_ai/connectors/`, `jinx_ai/providers/base.py`
- Per-connector `preload` flag in store

### [x] 4.2 Large tool response offload

If result > N chars/tokens → write `data/scratch/<session>/tool_results/<id>.txt`, inject preview + path; agent uses `read_file` / `read_terminal` style tools.

**TrueForge refs**

- [LargeToolResponse.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/capabilities/builtins/LargeToolResponse.ts)
- [docs/key-features/large-tool-responses.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/key-features/large-tool-responses.mdx)
- Config: [LargeToolResponseConfig.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/LargeToolResponseConfig.ts)

**Jinx targets**

- `jinx_ai/tools.py` (`TOOL_RESULT_LIMIT` today), `jinx_ai/session_fs.py`, `jinx_ai/todo.py` patterns
- Settings: agent section

### [ ] 4.3 Compaction polish (we already have it)

Align summary schema with TrueForge structured fields (intent, decisions, artifacts, errors, next steps). Optional input-token trigger vs char budget.

**TrueForge refs**

- [ContextCompaction.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/capabilities/builtins/ContextCompaction.ts)
- [ContextCompaction.test.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/tests/core/capabilities/builtins/ContextCompaction.test.ts)
- [CompactionConfig.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/CompactionConfig.ts)
- [.changeset/pre/modern-compaction-triggers.md](https://github.com/truefoundry/trueforge/blob/main/.changeset/pre/modern-compaction-triggers.md)
- Overview: https://trueforge.dev/key-features/overview

**Jinx targets**

- `jinx_ai/compaction.py` (already exists)

### [x] 4.4 Progressive skills (tighten what we have)

Ensure index = name+description only; full body only via `read_skill`. Optional Settings → Skills browser.

**TrueForge refs**

- [docs/skills.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/skills.mdx)
- [skill-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/skill-catalog.yaml)
- Sandbox skill mount (ideas): [packages/trueforge-core/src/core/sandbox/skills/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge-core/src/core/sandbox/skills)
- [skill_downloader.py](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/sandbox/scripts/skill_downloader.py)

**Jinx targets**

- `jinx_ai/agent_context.py` (already progressive-ish)
- Optional UI page for skills under Settings
- Roots: `jinx_ai/skills/`, `C:\Jinx-AI\skills\`, `<ws>/.jinx-ai/skills/`

### [ ] 4.5 Git-backed skill install (optional)

“Add skill from GitHub URL” → clone/copy into `C:\Jinx-AI\skills\<name>`.

**TrueForge refs**

- Skills docs + [SkillMounter.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/sandbox/skills/SkillMounter.ts)
- Images: [docs/images/add-skill.png](https://github.com/truefoundry/trueforge/blob/main/docs/images/add-skill.png)

**Jinx targets**

- `jinx_ai/skills_install.py`, Settings UI, `git` via existing `jinx_ai/git_ops.py` / `run_command`

---

## Phase 5 — Subagents & Code Mode (later, after connectors + context)

### [x] 5.1 Subagent capability (single nested worker first)

Spawn a child loop with clean context + subset of tools; return summary only to parent. UI: nested card under parent tool step.

**TrueForge refs**

- [docs/key-features/subagents.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/key-features/subagents.mdx)
- [DynamicSubAgents.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/capabilities/builtins/DynamicSubAgents.ts)
- [CreateDynamicSubAgentThread.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/runtime/CreateDynamicSubAgentThread.ts)
- [SubAgentCard.tsx](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-ui/src/atoms/SubAgentCard.tsx)
- Thread events: `thread.created` / `thread.done` in [events.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/ui-sdk/reference/events.mdx)
- Image: [docs/images/subagents.png](https://github.com/truefoundry/trueforge/blob/main/docs/images/subagents.png)

**Jinx targets**

- New: `jinx_ai/subagent.py`
- `jinx_ai/providers/base.py`, transcript nesting in `chat_transcript.py`
- Gate behind strong Coding model + Settings flag

### [x] 5.2 Code Mode (PTC) — optional

Agent writes a short Python script that calls tools / processes JSON; only stdout summary enters context. Run in workspace or scratch, not remote Daytona initially.

**TrueForge refs**

- [docs/key-features/code-mode.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/key-features/code-mode.mdx)
- Folder: [packages/trueforge-core/src/core/sandbox/codeMode/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge-core/src/core/sandbox/codeMode)
- [CodeModeDispatcher.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/sandbox/codeMode/CodeModeDispatcher.ts)
- [.changeset/pre/code-mode-destructive-defaults.md](https://github.com/truefoundry/trueforge/blob/main/.changeset/pre/code-mode-destructive-defaults.md)

**Jinx targets**

- New tool `run_code_mode` / harness capability
- Must require approval (Phase 3)
- Execute via `jinx_ai/terminal.py` / `run_command`

### [x] 5.3 Sandbox-as-tool (optional remote **or local Docker**)

Only if we want isolated compute beyond local Terminal. TrueForge uses Daytona — we already detect **Docker Desktop** on the host (`jinx_ai/docker_runtime.py`, skill `docker`). Prefer:

1. **MCP toolbox images** via Docker when Connectors land (`docker run` / compose).
2. **Ephemeral `docker run --rm -v workspace:/work`** for risky one-off commands.
3. Remote Daytona only if Docker-on-host is not enough.

**TrueForge refs**

- [docs/sandbox.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/sandbox.mdx)
- [packages/trueforge-core/src/core/sandbox/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge-core/src/core/sandbox)
- [DaytonaProvider.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-core/src/core/sandbox/provider/DaytonaProvider.ts)
- [sandbox-catalog.yaml](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/catalog/sandbox-catalog.yaml)

**Jinx targets**

- Done (detect + skill): `jinx_ai/docker_runtime.py`, `jinx_ai/skills/docker/SKILL.md`, tool `docker_info`
- Later: optional `run_in_docker` helper wrapping `run_command`, connector toolbox compose files under `jinx_ai/connectors/`
- Local Terminal remains default “sandbox” for everyday coding

---

## Phase 6 — Polish / parity extras

### [ ] 6.1 Schedules / cron agents (low priority)

TrueForge has schedules API — useful for heartbeat-like autonomous tasks. We already have Life/heartbeat; don’t duplicate unless product wants “run this agent every Monday”.

**TrueForge refs**

- API docs in [llms.txt schedules section](https://trueforge.dev/llms.txt) (List/Create schedule)

**Jinx targets**

- Possibly extend `jinx_ai/life.py` / `jinx_ai/heartbeat.py` — only if distinct from Life

### [ ] 6.2 Generative UI cards (charts/tables in chat)

Nice-to-have; we already show images/photo strips. Structured cards for tables later.

**TrueForge refs**

- [GenerativeUiConfig.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge-sdk/src/api/types/GenerativeUiConfig.ts)
- [docs/images/generative-ui.png](https://github.com/truefoundry/trueforge/blob/main/docs/images/generative-ui.png)

**Jinx targets**

- `jinx_ai/chat_transcript.py`

### [ ] 6.3 OAuth for connectors (full loopback)

Browser OAuth for GitHub/Linear/Notion when token paste is not enough.

**TrueForge refs**

- [packages/trueforge/src/mcp/auth/](https://github.com/truefoundry/trueforge/tree/main/packages/trueforge/src/mcp/auth)
- [mcpOAuth.ts](https://github.com/truefoundry/trueforge/blob/main/packages/trueforge/src/apis/mcpOAuth.ts)
- Token stores under `packages/trueforge/src/db/*/token-store/`

**Jinx targets**

- `jinx_ai/connectors/oauth.py`, local HTTP callback, Windows browser open via `desktop.py`

### [ ] 6.4 Agent library / multi-agent presets (product decision)

TrueForge “create an agent” with fixed connectors+skills+model. Jinx today = modes (Assistant/Developer) + session brains. Optional later: saved agent profiles.

**TrueForge refs**

- [docs/create-agent](https://trueforge.dev/create-agent/overview)
- [docs/agent-library](https://trueforge.dev/agent-library)
- UI agent modes: [docs/ui-sdk/guides/agent-modes](https://trueforge.dev/ui-sdk/guides/agent-modes)

**Jinx targets**

- Would touch `jinx_ai/sessions.py`, settings, activity bar — **defer** until connectors exist

---

## Explicitly out of scope (do not implement)

| Item | Why | Ref (for awareness) |
| --- | --- | --- |
| Node TrueForge server / `npx @truefoundry/trueforge` | Wrong stack | repo root README |
| React `@trueforge/trueforge-ui` embed | We are PyQt | [docs/chat-ui.mdx](https://github.com/truefoundry/trueforge/blob/main/docs/chat-ui.mdx) |
| Postgres + Redis hosted mode | Desktop single-user | TrueForge hosted docs |
| Helm / Railway / OIDC team login | Not desktop | [docs/authentication](https://trueforge.dev/authentication/overview) |
| Running whole agent inside remote sandbox by default | Secrets + latency; we want sandbox-as-tool later | [sandbox docs](https://trueforge.dev/sandbox) |
| Copying their TypeScript under `jinx_ai/` | License OK (MIT) but maintenance hell | LICENSE |

---

## Suggested implementation order (checklist)

1. **0.1 → 0.3** event + catalog contract + docs  
2. **1.1 → 1.5** connectors core + MCP client + tool wiring  
3. **1.6** GitHub connector (first real win)  
4. **2.1 → 2.3** activity timeline in chat  
5. **3.1 → 3.2** approvals  
6. **4.1 → 4.2** deferred tools + large offload (helps local models a lot)  
7. **1.7** more connectors  
8. **3.3** ask-user  
9. **4.4 → 4.5** skills UI / git install  
10. **5.x** subagents / code mode  
11. **6.x** OAuth, generative cards, schedules, agent library  

---

## Jinx files to keep re-reading while stealing

| Area | Paths |
| --- | --- |
| Agent loop | `jinx_ai/providers/base.py`, `jinx_ai/chat_window.py` |
| Tools | `jinx_ai/tools.py` |
| Activity / chat | `jinx_ai/activity.py`, `jinx_ai/chat_transcript.py`, `jinx_ai/composer.py` |
| Trace | `jinx_ai/trace.py` |
| Skills | `jinx_ai/agent_context.py`, `jinx_ai/skills/` |
| Compaction | `jinx_ai/compaction.py` |
| Clarify | `jinx_ai/clarify.py` |
| Settings / keys | `jinx_ai/settings_store.py`, `jinx_ai/api_access.py`, `jinx_ai/api_access_page.py` |
| Session scratch | `jinx_ai/session_fs.py`, `jinx_ai/todo.py` |
| Terminal | `jinx_ai/terminal.py` |
| Self-knowledge | `docs/JINX.md`, `AGENTS.md` |

---

*Last updated from a recursive skim of `truefoundry/trueforge` `main` tree + public docs. Re-open catalog YAML on GitHub when seeding connectors — network fetch of the YAML may time out; use the blob links above.*
