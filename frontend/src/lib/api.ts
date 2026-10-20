// Thin, typed REST client for docs/API.md. Every call rejects with ApiError on a non-2xx.

import { apiUrl } from "./config";
import type {
  Agent,
  AuthStatus,
  AuthUser,
  Activity,
  AvailableTool,
  Bootstrap,
  BrowseResult,
  Checkpoint,
  DiffBody,
  FileChange,
  HostModels,
  JevSettings,
  JevSettingsUpdate,
  McpPayload,
  McpServer,
  ModelHealth,
  KeyInfo,
  Message,
  ModelsState,
  OpenRouterSettings,
  ProjectModelsState,
  RestoreResult,
  RoleBrain,
  RoleName,
  Project,
  RosterEntry,
  Skill,
  Task,
  TaskResult,
  TaskStatus,
  Thread,
  Tool,
  Turn,
  Telemetry,
  UsersPayload,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailOf(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const d = (payload as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      // FastAPI validation errors: [{loc, msg, ...}]
      return d.map((e) => (e && typeof e === "object" && "msg" in e ? String((e as { msg: unknown }).msg) : String(e))).join("; ");
    }
  }
  return fallback;
}

export async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the CatCrewAI backend. Is it running?");
  }
  if (res.status === 204) return undefined as T;
  let payload: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!res.ok) {
    // Remote mode: the session ended (signed out elsewhere, expired). The auth gate shows the login.
    if (res.status === 401 && !path.startsWith("/api/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new ApiError(res.status, detailOf(payload, `Request failed (${res.status})`));
  }
  return payload as T;
}

/** Fired on a 401 outside the auth routes (see ``state/auth.ts``). */
export const UNAUTHORIZED_EVENT = "catcrew:unauthorized";

const get = <T>(p: string, signal?: AbortSignal) => request<T>("GET", p, undefined, signal);
const post = <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {});
const put = <T>(p: string, b: unknown) => request<T>("PUT", p, b);
const patch = <T>(p: string, b: unknown) => request<T>("PATCH", p, b);
const del = <T = void>(p: string) => request<T>("DELETE", p);
const enc = encodeURIComponent;

export interface NewProject {
  name: string;
  description?: string;
  folders?: string[];
  bot_ids?: string[];
  kickoff?: string;
}

export const api = {
  health: () => get<{ status: string; version: string; busy: boolean }>("/api/health"),
  bootstrap: () => get<Bootstrap>("/api/bootstrap"),

  auth: {
    status: () => get<AuthStatus>("/api/auth/status"),
    register: (username: string, password: string, setup_code: string) =>
      post<{ user: AuthUser }>("/api/auth/register", { username, password, setup_code }),
    login: (username: string, password: string) => post<{ user: AuthUser }>("/api/auth/login", { username, password }),
    logout: () => post<void>("/api/auth/logout", {}),
    invite: (token: string) => get<{ role: string; expires_at: number }>(`/api/auth/invite/${enc(token)}`),
    acceptInvite: (token: string, username: string, password: string) =>
      post<{ user: AuthUser }>(`/api/auth/invite/${enc(token)}`, { username, password }),
    changePassword: (old: string, next: string) => post<void>("/api/auth/password", { old, new: next }),
  },

  users: {
    list: () => get<UsersPayload>("/api/users"),
    invite: (role: "user" | "admin") => post<{ token: string; url: string; role: string }>("/api/users/invites", { role }),
    setRole: (id: string, role: "user" | "admin") => patch<AuthUser>(`/api/users/${enc(id)}`, { role }),
    remove: (id: string) => del(`/api/users/${enc(id)}`),
  },
  /** Stop the engine (browser mode's Power off; the desktop shell has its own quit command). */
  shutdown: () => post<{ status: string }>("/api/shutdown", {}),

  telemetry: {
    /** One snapshot for the inspector's Telemetry tab (context, balances, quotas, GPU). */
    live: (projectId: string, signal?: AbortSignal) => get<Telemetry>(`/api/telemetry/live?project_id=${enc(projectId)}`, signal),
  },

  projects: {
    list: () => get<Project[]>("/api/projects"),
    get: (id: string) => get<Project>(`/api/projects/${enc(id)}`),
    create: (b: NewProject) => post<Project>("/api/projects", b),
    patch: (id: string, b: { name?: string; description?: string }) => patch<Project>(`/api/projects/${enc(id)}`, b),
    remove: (id: string) => del(`/api/projects/${enc(id)}`),
    setWorkspace: (id: string, path: string) => put<Project>(`/api/projects/${enc(id)}/workspace`, { path }),
    reset: (id: string) => post<Project>(`/api/projects/${enc(id)}/reset`),
    browse: (path = "") => get<BrowseResult>(`/api/workspaces/browse?path=${enc(path)}`),
    /** Make a folder inside `parent`; answers with the new folder's listing so the picker can step into it. */
    createFolder: (parent: string, name: string) => post<BrowseResult>("/api/workspaces/folders", { parent, name }),
  },

  roster: {
    list: (pid: string) => get<RosterEntry[]>(`/api/projects/${enc(pid)}/roster`),
    add: (pid: string, botId: string) => post<RosterEntry>(`/api/projects/${enc(pid)}/roster`, { bot_id: botId }),
    remove: (pid: string, botId: string) => del(`/api/projects/${enc(pid)}/roster/${enc(botId)}`),
  },

  agents: {
    list: () => get<Agent[]>("/api/agents"),
    get: (id: string) => get<Agent>(`/api/agents/${enc(id)}`),
    activity: (id: string) => get<Activity>(`/api/agents/${enc(id)}/activity`),
  },

  tasks: {
    list: (pid: string) => get<Task[]>(`/api/projects/${enc(pid)}/tasks`),
    create: (pid: string, b: { assigned_to: string; title: string; description?: string }) =>
      post<Task>(`/api/projects/${enc(pid)}/tasks`, b),
    get: (pid: string, tid: string) => get<Task & { results: TaskResult[] }>(`/api/projects/${enc(pid)}/tasks/${enc(tid)}`),
    setStatus: (pid: string, tid: string, status: TaskStatus) =>
      patch<Task>(`/api/projects/${enc(pid)}/tasks/${enc(tid)}`, { status }),
    changes: (pid: string, limit = 30) => get<FileChange[]>(`/api/projects/${enc(pid)}/changes?limit=${limit}`),
    diff: (uuid: string) => get<DiffBody>(`/api/diffs/${enc(uuid)}`),
  },

  checkpoints: {
    list: (pid: string) => get<Checkpoint[]>(`/api/projects/${enc(pid)}/checkpoints`),
    restore: (pid: string, id: string) => post<RestoreResult>(`/api/projects/${enc(pid)}/checkpoints/${enc(id)}/restore`, {}),
  },

  chat: {
    threads: (pid: string) => get<Thread[]>(`/api/projects/${enc(pid)}/threads`),
    messages: (pid: string, botId: string, limit = 200) =>
      get<Message[]>(`/api/projects/${enc(pid)}/threads/${enc(botId)}/messages?limit=${limit}`),
    send: (pid: string, botId: string, text: string) =>
      post<{ turn_id: string | null; queued: boolean; position?: number }>(
        `/api/projects/${enc(pid)}/threads/${enc(botId)}/messages`,
        { text },
      ),
    /** Report a problem: the team lead writes a fix plan, the cats fix it, QA verifies. */
    report: (pid: string, botId: string, text: string) =>
      post<{ turn_id: string | null; queued: boolean; position?: number }>(
        `/api/projects/${enc(pid)}/threads/${enc(botId)}/report`,
        { text },
      ),
    /** Run again the last message whose model was turned away (after `models.replace`). */
    retry: (pid: string) => post<{ turn_id: string | null; queued: boolean }>(`/api/projects/${enc(pid)}/retry`),
    currentTurn: () => get<Turn | null>("/api/turns/current"),
    abort: (turnId: string) => post<{ status: string }>(`/api/turns/${enc(turnId)}/abort`),
    steer: (turnId: string, text: string) => post<{ status: string }>(`/api/turns/${enc(turnId)}/steer`, { text }),
    approve: (id: string, decision: "allow" | "allow_always" | "deny") => post<void>(`/api/approvals/${enc(id)}`, { decision }),
    answer: (id: string, answer: string) => post<void>(`/api/asks/${enc(id)}`, { answer }),
  },

  skills: {
    list: (refresh = false) => get<Skill[]>(`/api/skills${refresh ? "?refresh=true" : ""}`),
    proposals: () => get<Skill[]>("/api/skills/proposals"),
    get: (id: string) => get<Skill>(`/api/skills/${enc(id)}`),
    create: (b: { name: string; description: string; body: string; target_roles?: string }) => post<Skill>("/api/skills", b),
    importFromUrl: (url: string) => post<{ message: string; skills: Skill[] }>("/api/skills/import", { url }),
    remove: (id: string) => del(`/api/skills/${enc(id)}`),
    assign: (id: string, agentIds: string[]) => put<Skill>(`/api/skills/${enc(id)}/assignments`, { agent_ids: agentIds }),
    setGlobal: (id: string, on: boolean) => put<Skill>(`/api/skills/${enc(id)}/global`, { on }),
    approve: (id: string, agentId: string) => post<Skill>(`/api/skills/${enc(id)}/approve`, { agent_id: agentId }),
    dismiss: (id: string) => post<void>(`/api/skills/${enc(id)}/dismiss`),
  },

  tools: {
    list: () => get<Tool[]>("/api/tools"),
    available: () => get<AvailableTool[]>("/api/tools/available"),
    get: (id: string) => get<Tool>(`/api/tools/${enc(id)}`),
    register: (handler: string) => post<Tool>("/api/tools", { handler }),
    importFromUrl: (url: string) => post<{ message: string; tools: Tool[] }>("/api/tools/import", { url }),
    remove: (id: string) => del(`/api/tools/${enc(id)}`),
    assign: (id: string, agentIds: string[]) => put<Tool>(`/api/tools/${enc(id)}/assignments`, { agent_ids: agentIds }),
    setGlobal: (id: string, on: boolean) => put<Tool>(`/api/tools/${enc(id)}/global`, { on }),
  },

  /** MCP integrations (Blender, Unity): live status, and a way to re-discover tools without restarting the backend. */
  mcp: {
    servers: (signal?: AbortSignal) => get<McpPayload>("/api/mcp/servers", signal),
    /** Re-read the config, re-discover every tool list and reconnect what is down. `hard` reconnects everything. */
    refresh: (hard = false) => post<McpPayload>("/api/mcp/refresh", hard ? { hard: true } : {}),
    reconnect: (id: string) => post<McpPayload & { server: McpServer }>(`/api/mcp/servers/${enc(id)}/reconnect`),
  },

  /** API keys are write-only: `set` sends one, nothing ever returns it. */
  keys: {
    list: () => get<KeyInfo[]>("/api/keys"),
    set: (id: string, key: string) => put<KeyInfo>(`/api/keys/${enc(id)}`, { key }),
    clear: (id: string) => del<KeyInfo>(`/api/keys/${enc(id)}`),
  },

  models: {
    get: () => get<ModelsState>("/api/models"),
    setRoles: (b: Partial<ModelsState["roles"]>) => put<ModelsState>("/api/models/roles", b),
    /** `opts.image`: OpenRouter's image-generation models instead of its chat models (Images role only). */
    hostModels: (host: string, opts?: { image?: boolean }) => get<HostModels>(`/api/models/hosts/${enc(host)}${opts?.image ? "?image=true" : ""}`),
    /** Ask the provider for its live model list right now (skips the short cache). */
    refreshHost: (host: string, opts?: { image?: boolean }) => post<HostModels>(`/api/hosts/${enc(host)}/refresh-models${opts?.image ? "?image=true" : ""}`),
    /** This project's model overrides. `roles[role] = null` (or host "inherit") goes back to the global pick. */
    getProject: (pid: string) => get<ProjectModelsState>(`/api/projects/${enc(pid)}/models`),
    setProject: (pid: string, roles: Partial<Record<RoleName, RoleBrain | null>>) =>
      put<ProjectModelsState>(`/api/projects/${enc(pid)}/models`, { roles }),
    health: (refresh = false) => get<ModelHealth>(`/api/models/health${refresh ? "?refresh=true" : ""}`),
    /** Switch every role on one model (globally and in every project) to another. */
    replace: (b: { from_host: string; from_model: string; to_host: string; to_model: string }) =>
      post<ModelsState & { changed: string[] }>("/api/models/replace", b),
  },

  jev: {
    /** The Jev ("System One") pre-execution gatekeeper's saved settings. Never includes the API key. */
    getSettings: () => get<JevSettings>("/api/jev/settings"),
    /** Where Jev runs and how it behaves. Its key is set with every other key, under Hosts. */
    setSettings: (b: JevSettingsUpdate) => put<JevSettings>("/api/jev/settings", b),
  },
  openrouter: {
    /** Cost/performance knobs: provider routing, the opt-in compression plugin, free-tier auxiliary routing. */
    getSettings: () => get<OpenRouterSettings>("/api/openrouter/settings"),
    setSettings: (b: Partial<OpenRouterSettings>) => put<OpenRouterSettings>("/api/openrouter/settings", b),
  },
};
