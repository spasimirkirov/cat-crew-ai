// Wire types for the CatCrewAI backend. Source of truth: docs/API.md (contract v1).

export type Status = "idle" | "thinking" | "coding" | "testing" | "chatting";
export type BusyState = "working" | "idle";

/**
 * Cat Crew: the orchestrator / system chat. It is the first thread of every project and the
 * first sidebar card, but it is NOT an agent: it has no skills or tools assigned, so it never
 * appears in the Skills / Tools matrices (the engine routes work to the specialist cats itself).
 */
export const ROOM_ID = "crew";
export const ROOM_NAME = "Cat Crew";
/** Bundled with the app (`public/avatars/default.png`), so it shows even before the backend is up. */
export const ROOM_AVATAR = "/avatars/default.png";
/** Current and legacy (`chief`, sessions written before the rename) ids of the orchestrator. */
export const isRoomId = (id?: string | null): boolean => !id || id === ROOM_ID || id === "chief";

export interface Agent {
  id: string;
  name: string;
  role: string;
  title: string;
  description: string;
  avatar: string; // "" or "/api/agents/{id}/avatar"
  color: string;
  skills: string[];
  playbooks: string[];
  tools: string[];
  letta_tools: string[];
  mcp_tools: string[];
  /** Built-ins and registry tools this cat can never be without (read-only). */
  essential_tools?: string[];
  max_rounds: number;
  is_system: boolean;
  system_prompt?: string;
}

export interface Activity {
  bot_id: string;
  status: Status;
  state: BusyState;
  text: string;
  updated_at: string | null;
}

export interface RosterEntry {
  bot_id: string;
  agent: Agent;
  is_room: boolean;
  activity: Activity;
  joined_at: string | null;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  folders: string[];
  bot_ids: string[];
  created_at?: string | null;
  phase: string;
}

export type TaskStatus = "pending" | "in_progress" | "blocked" | "failed" | "unverified" | "done";

export interface Task {
  id: string;
  project_id: string;
  assigned_to: string;
  created_by: string;
  title: string;
  description: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  latest_summary: string;
  /** Work-order fields (build | bug | plan | fix | review); older rows read as "build". */
  kind?: "build" | "bug" | "plan" | "fix" | "review";
  parent_id?: string;
  acceptance?: string;
  depends_on?: string[];
  attempt?: number;
  reported_by?: string;
}

export interface TaskResult {
  id: string;
  task_id: string;
  final_state: string;
  summary: string;
  created_at: string;
}

export interface FileChange {
  id: string;
  task_id: string;
  project_id: string;
  agent_id: string;
  file_path: string;
  diff_uuid: string;
  created_at: string;
}

export interface DiffBody {
  diff_uuid: string;
  file_path: string;
  agent_id: string;
  diff_content: string;
  created_at: string;
}

export interface ActivityStep {
  line: string;
  preview?: string;
}

/** A live step also carries `n` (grows by one per step within a turn and thread). */
export interface LiveStep extends ActivityStep {
  n: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "error";
  speaker?: string;
  bot_id?: string;
  text: string;
  created_at: string;
  phase?: string;
  tool_events?: string[];
  activity?: { elapsed_s: number; steps: ActivityStep[] };
}

export interface Thread {
  bot_id: string;
  title: string;
  message_count: number;
  last_message_at: string | null;
}

export interface Turn {
  id: string;
  project_id: string;
  bot_id: string;
  started_at: string;
  phase: string;
  kind: "room" | "direct";
  /** How Cat Crew is handling the message: a free chat, work for the cats, an @mention, or a 1:1. */
  mode?: "chat" | "work" | "mention" | "direct";
}

export type SkillType = "bundled" | "custom" | "marketplace" | "auto_generated";

export interface Skill {
  id: string;
  name: string;
  description: string;
  type: SkillType;
  version: string;
  target_roles: string[];
  playbook_path: string;
  source_url: string;
  is_global: boolean;
  agent_ids: string[];
  /** Cats for which this playbook is essential (backend `platform/essentials.py`): always on. */
  locked_agent_ids?: string[];
  body?: string;
}

export interface Tool {
  id: string;
  name: string;
  description: string;
  handler: string;
  is_global: boolean;
  agent_ids: string[];
  /** Cats for which this tool is essential: always on, cannot be unassigned, the tool cannot be deleted. */
  locked_agent_ids?: string[];
  schema?: unknown;
}

export interface AvailableTool {
  handler: string;
  name: string;
  description: string;
  registered: boolean;
}

export type KeySource = "saved" | "environment" | "none";

export interface ModelHost {
  id: string;
  label: string;
  enabled: boolean;
  cloud: boolean;
  configured?: boolean;
  key_source?: KeySource;
}

export type McpStatus = "connected" | "connecting" | "reconnecting" | "disconnected" | "disabled";

/** One MCP server (Blender, Unity, ...) as GET /api/mcp/servers reports it. Never carries a secret. */
export interface McpServer {
  id: string;
  label: string;
  icon: string;
  description: string;
  enabled: boolean;
  transport: string;
  target: string;
  status: McpStatus;
  /** Connected AND the program behind the server (Blender, Unity) answers. */
  ready: boolean;
  bridge: "ok" | "down" | "unknown";
  app_label: string;
  error: string;
  error_kind: string;
  note: string;
  setup_hint: string;
  bound_to: string[];
  bound_cats: { id: string; name: string }[];
  tools_total: number;
  tools_exposed: number;
  tools_on_demand: number;
  tool_names: string[];
  connected_at: string | null;
  checked_at: string | null;
  next_retry_in: number | null;
}

export interface McpPayload {
  servers: McpServer[];
  problems: string[];
  sdk_installed: boolean;
  running: boolean;
  /** False when CATCREW_MCP=0 switches the whole subsystem off. */
  enabled: boolean;
}

/** What the UI may know about an API key: whether one is set and where it comes from, never the key. */
export interface KeyInfo {
  id: string;
  title: string;
  subtitle: string;
  group: "host" | "service";
  configured: boolean;
  source: KeySource;
  signup: string;
}

export interface RoleBrain {
  host: string;
  model: string;
}

export interface ModelsState {
  roles: { chat: RoleBrain; coding: RoleBrain; memory: RoleBrain; vision: RoleBrain; planning: RoleBrain; review: RoleBrain; fallback?: RoleBrain; images: RoleBrain };
  hosts: ModelHost[];
}

/** What a role actually runs on: the project's own pick, else the global one (an unset role follows Chat). */
export interface EffectiveModel {
  host: string;
  model: string;
  /** "chat" / "coding": the role is unset and follows that role. */
  source: "project" | "global" | "chat" | "coding";
  explicit: boolean;
}

export type RoleName = keyof ModelsState["roles"];

/** `GET /api/projects/{id}/models`: this project's model overrides next to the global picks. */
export interface ProjectModelsState {
  project_id: string;
  /** Only the roles this project overrides. */
  overrides: Partial<Record<RoleName, RoleBrain>>;
  effective: Record<RoleName, EffectiveModel>;
  /** The Models page picks as saved (an unset role has an empty host). */
  global: Record<RoleName, RoleBrain>;
  /** What each role runs on with no override (unset roles follow Chat): the "Use Global Setting" label. */
  global_effective: Record<RoleName, EffectiveModel>;
  hosts: ModelHost[];
}

/** What a host says a model can do. `chat` is implied for every listed model and is not badged. */
export type Capability = "vision" | "reasoning" | "tools" | "chat";

export interface HostModel {
  name: string;
  display_name: string;
  loaded: boolean;
  capabilities: string[];
  /** Tokens, when the host reports it (else 0). */
  context_length: number;
  /** OpenRouter only: USD per 1M tokens from its live `/v1/models` pricing. Absent/`null`
   * elsewhere (any host but OpenRouter) or when the host's response omitted pricing. Optional so existing
   * fixtures/tests that predate pricing still type-check. */
  price_prompt_per_1m?: number | null;
  price_completion_per_1m?: number | null;
  /** The model id ends with `:free` (or OpenRouter reports zero pricing both ways). */
  is_free?: boolean;
  /** OpenRouter reports a prompt-cache read price for this model. */
  supports_prompt_cache?: boolean;
}

export interface HostModels {
  host: string;
  online: boolean;
  /** A short fixed phrase, never a URL or a key. */
  error: string;
  error_kind: "" | "no_key" | "auth" | "unreachable" | "error";
  checked_at: number;
  models: HostModel[];
}

/** One role whose saved model cannot be used. `message` is a ready-to-show sentence. */
export interface ModelIssue {
  role: string;
  role_label: string;
  host: string;
  host_label: string;
  model: string;
  reason: "missing" | "offline" | "no_key" | "disabled";
  message: string;
}

export interface ModelHealth {
  checked_at: number;
  ok: boolean;
  hosts: Record<string, { online: boolean; error_kind: string; model_count: number }>;
  issues: ModelIssue[];
}

export interface Bootstrap {
  agents: Agent[];
  projects: Project[];
  activity: Activity[];
  current_turn: Turn | null;
  /** Every running turn, one per project lane (older servers send only `current_turn`). */
  turns?: Turn[];
}

export interface BrowseResult {
  path: string;
  parent: string | null;
  entries: { name: string; path: string; is_dir: boolean }[];
}

export interface Approval {
  approval_id: string;
  bot_id: string;
  tool: string;
  detail: string;
  project_id: string;
}

export interface Ask {
  ask_id: string;
  bot_id: string;
  prompt: string;
  choices: string[];
  project_id: string;
}

// -- WebSocket ---------------------------------------------------------------------

export interface WireEvent<T extends string = string, D = unknown> {
  v: number;
  seq: number;
  ts: string;
  type: T;
  project_id: string | null;
  data: D;
}

/** The cat that currently holds the engine (one persona at a time in a work run). */
export interface ActiveCat {
  cat_id: string;
  name: string;
  phase: string;
  label: string;
  status: string;
}

// -- live model telemetry (GET /api/telemetry/live, `context.usage` on /ws/events) ------------

export type MeterLevel = "ok" | "warn" | "danger";

/** How full the running conversation is, against the model's real context window (64k, 128k, 1M...). */
export interface ContextUsage {
  host: string;
  model: string;
  /** The conversation's estimated tokens, system prompt included (all of it fills the window). */
  tokens: number;
  /** The part of `tokens` that is the system prompt. */
  system_tokens: number;
  /** The model's context window: the gauge's 100 %. */
  limit: number;
  /** Where the older turns are summarized: about 85 % of `limit` (absent in older readings). */
  threshold?: number;
  percent: number;
  level: MeterLevel;
  /** This reading was taken right after the older turns were summarized. */
  compressed: boolean;
  updated_at?: string | null;
}

export interface BalanceInfo {
  host: string;
  configured: boolean;
  status: "ok" | "empty" | "error" | "no_key" | "unsupported";
  balance?: number | null;
  currency?: string;
  /** "$12.45 remaining" */
  display?: string;
  message?: string;
}

/** OpenRouter prompt-cache token savings (`services/usage_meter.cache_savings`). `estimated_pct_spend_saved`
 * is a rough estimate (a documented assumption, never a vendor-quoted dollar figure). */
export interface CacheSavings {
  host: string;
  calls: number;
  prompt_tokens: number;
  cached_tokens: number;
  cache_hit_pct: number;
  estimated_pct_spend_saved: number;
  /** Same estimate priced in USD from the live OpenRouter catalog, or `null` when a cached call's
   * model isn't in the last-known catalog snapshot — an honest unknown, never a guessed number. */
  estimated_usd_saved: number | null;
  /** Set only on the {configured:false} fallback shape used when the section could not be read. */
  configured?: boolean;
  status?: string;
}

/** The Jev ("System One") pre-execution gatekeeper: its saved settings plus its running counters
 * (`services/jev_metrics`). Off by default (`enabled: false`) since it depends on a third-party model. */
export interface JevTelemetry {
  enabled: boolean;
  model: string;
  confidence_threshold: number;
  precheck_calls: number;
  bypasses: number;
  guard_calls: number;
  blocked_tool_calls: number;
  /** `bypasses + blocked_tool_calls`. */
  interceptions: number;
  /** A rough estimate, not a measured saving: see `services/jev_metrics`. */
  tokens_saved_estimate: number;
  avg_decision_latency_ms: number;
  /** `tokens_saved_estimate` priced at the Coding role's current OpenRouter model, or `null` when
   * that role isn't on OpenRouter or the model isn't in the last-known catalog snapshot. */
  estimated_usd_saved: number | null;
  /** Set only on the {configured:false} fallback shape used when the section could not be read. */
  configured?: boolean;
  status?: string;
}

export interface GpuStats {
  available: boolean;
  source?: string;
  count?: number;
  name?: string;
  vram_used_gb?: number;
  vram_total_gb?: number;
  utilization?: number | null;
  temperature_c?: number | null;
  /** The local model's last generation speed in tokens per second. */
  gen_speed_tps?: number | null;
  gen_speed_at?: string | null;
  status?: string;
}

export interface ActiveModel {
  role: string;
  host: string;
  host_label: string;
  model: string;
  source: string;
  local: boolean;
  /** ready | offline | missing | no_key | disabled | unknown */
  status: string;
  message: string;
}

/** `key_source` for Jev specifically: on top of the usual `saved`/`environment`/`none`, Jev can also
 * ride on the already-configured OpenRouter key (the normal case — see `JevGatekeeperPanel.tsx`). */
export type JevKeySource = KeySource | "openrouter";

/** `GET /api/jev/settings` response. Jev (TypeSafe AI's "System One") runs on OpenRouter's Decisions API
 * (the OpenRouter key) or on TypeSafe's official API (the TypeSafe key). It keeps no key of its own:
 * `api_key_configured`/`key_source` say whether the chosen backend's key is set under Hosts. */
export interface JevSettings {
  enabled: boolean;
  backend: "openrouter" | "typesafe";
  /** Opt-in: block a tool call Jev is sure is unsafe. */
  guard_tool_calls: boolean;
  /** Opt-in: drop tool schemas from a turn Jev is sure needs none (costs prompt-cache hits). */
  skip_tools_precheck: boolean;
  /** Read-only: the name Jev answers to on that backend. */
  model: string;
  confidence_threshold: number;
  api_key_configured: boolean;
  key_source: JevKeySource;
}

/** `GET/PUT /api/openrouter/settings`: OpenRouter cost/performance knobs. `""` for `provider_sort`
 * means OpenRouter's own default (price-weighted load balancing across providers). */
export interface OpenRouterSettings {
  provider_sort: "" | "price" | "throughput" | "latency";
  allow_fallbacks: boolean;
  /** The "context-compression" plugin: opt-in, can drop/truncate messages from a long conversation. */
  compression_enabled: boolean;
  /** Route lightweight background calls (context-summary compaction today) to a free model. */
  free_tier_enabled: boolean;
  free_tier_model: string;
}

/** `PUT /api/jev/settings` body. No key here: keys go through `PUT /api/keys/{id}` (Models → Hosts). */
export interface JevSettingsUpdate {
  enabled?: boolean;
  backend?: "openrouter" | "typesafe";
  guard_tool_calls?: boolean;
  skip_tools_precheck?: boolean;
  confidence_threshold?: number;
}

/** A model the host turned away (rate limit, no credit), sent with `turn.failed`: the run stopped at
 * once, and the person can switch every role on it to another model and retry the message. */
export interface BlockedModel {
  host: string;
  model: string;
  kind: "rate_limited" | "no_credit";
  status: number;
  message: string;
  /** A `:free` model (OpenRouter's free tier: tight per-minute and per-day limits). */
  free: boolean;
  at: number;
  /** The roles that run on it in this project; `source` "project" | "global" | "chat". */
  roles: { role: string; label: string; source: string }[];
}

export interface Telemetry {
  project_id: string | null;
  updated_at: string | null;
  context: ContextUsage | null;
  /** The active coding model's context window (what the gauge is measured against before a reading exists). */
  context_limit: number;
  /** Where that model's older turns get summarized (about 85 % of `context_limit`). */
  context_threshold?: number;
  /** "provider" (known window), "default" (a guess) or "env" (forced). */
  context_limit_source?: string;
  active: ActiveModel;
  /** OpenRouter is the only cloud host: one key, one balance. */
  openrouter: BalanceInfo;
  openrouter_cache: CacheSavings;
  jev: JevTelemetry;
  gpu: GpuStats;
}

export type ServerEvent =
  | WireEvent<"hello", { server_seq: number }>
  | WireEvent<"bot.staffed", { bot_id: string; agent: Agent; entry: RosterEntry }>
  | WireEvent<"bot.released", { bot_id: string }>
  | WireEvent<"bot.activity", Activity>
  | WireEvent<"phase.started", { phase: string; bot_id: string; label: string }>
  | WireEvent<"cat.swapped", ActiveCat>
  | WireEvent<"task.changed", { task: Task }>
  | WireEvent<"file.changed", { change: FileChange }>
  | WireEvent<"chat.message", { thread: string; message: Message }>
  | WireEvent<"chat.activity", { thread: string; turn_id: string; bot_id: string; step: LiveStep }>
  | WireEvent<"turn.started", Turn>
  | WireEvent<"turn.finished", { turn_id: string; status: "ok" | "aborted" }>
  | WireEvent<"turn.failed", { turn_id: string; error: string; model_issue?: BlockedModel }>
  | WireEvent<"approval.requested", { approval_id: string; bot_id: string; tool: string; detail: string }>
  | WireEvent<"approval.resolved", { approval_id: string; decision: string }>
  | WireEvent<"ask.requested", { ask_id: string; bot_id: string; prompt: string; choices: string[] }>
  | WireEvent<"ask.resolved", { ask_id: string }>
  | WireEvent<"skill.proposed", { skill: Skill; agent_id: string; task_id: string }>
  | WireEvent<"project.changed", { project: Project }>
  | WireEvent<"context.usage", ContextUsage>;
