// Pure state + reducer for live events. No React, no fetch: everything that
// happens on `/ws/events` is applied here, so it is unit-testable and the UI
// stays a dumb projection of this state.

import type {
  ActiveCat,
  BlockedModel,
  Activity,
  Agent,
  Approval,
  Ask,
  ContextUsage,
  FileChange,
  LiveStep,
  Message,
  Project,
  RosterEntry,
  ServerEvent,
  Skill,
  Task,
  Turn,
} from "../lib/types";
import { ROOM_ID, isRoomId } from "../lib/types";
import type { ConnectionStatus } from "./ws";

export const MAX_LIVE_STEPS = 80;
export const MAX_CHANGES = 60;

export interface ThreadState {
  messages: Message[];
  /** Live execution-card steps of the running turn, ordered by `n`. */
  steps: LiveStep[];
  turnId: string | null;
  loaded: boolean;
  unread: number;
}

export interface RoomState {
  loaded: boolean;
  roster: RosterEntry[];
  tasks: Task[];
  changes: FileChange[];
  threads: Record<string, ThreadState>;
  approvals: Approval[];
  asks: Ask[];
  phase: { phase: string; bot_id: string; label: string } | null;
  /** Who holds the engine right now (set by `cat.swapped`, cleared when the turn ends). */
  activeCat: ActiveCat | null;
  /** The latest context-window reading of the running conversation (`context.usage`); kept after the turn ends. */
  context: ContextUsage | null;
  /** A model the last turn could not use (rate limit, no credit): the switch-and-retry card. */
  modelIssue: BlockedModel | null;
}

/** Where the start-up handshake with the backend stands (drives the splash screen, never an error toast). */
export interface BootState {
  phase: "starting" | "failed";
  /** Quiet attempts so far. */
  attempts: number;
  /** When this handshake began (ms since epoch). */
  since: number;
  error: string | null;
  /** The engine's own words about a failed start (the tail of its output), when the shell has them. */
  detail: string | null;
}

export const bootStarting = (since = Date.now()): BootState => ({ phase: "starting", attempts: 0, since, error: null, detail: null });

export interface CoreState {
  connection: ConnectionStatus;
  /** The start-up data has been loaded at least once: the splash gives way to the app. */
  ready: boolean;
  boot: BootState;
  agents: Record<string, Agent>;
  activity: Record<string, Activity>;
  projects: Project[];
  rooms: Record<string, RoomState>;
  /** The most recently started running turn (kept for one-lane callers); see `turns`. */
  currentTurn: Turn | null;
  /** Every running turn by project id: each project has its own lane, several may run at once. */
  turns: Record<string, Turn>;
  proposals: Skill[];
  /** `${projectId}:${botId}` -> when the card popped in (drives the pop-in animation). */
  fresh: Record<string, number>;
  activeThread: { pid: string; botId: string } | null;
  lastError: string | null;
}

export const emptyThread = (): ThreadState => ({ messages: [], steps: [], turnId: null, loaded: false, unread: 0 });

export const emptyRoom = (): RoomState => ({
  loaded: false,
  roster: [],
  tasks: [],
  changes: [],
  threads: {},
  approvals: [],
  asks: [],
  phase: null,
  activeCat: null,
  context: null,
  modelIssue: null,
});

export const initialCore = (): CoreState => ({
  connection: "connecting",
  ready: false,
  boot: bootStarting(),
  agents: {},
  activity: {},
  projects: [],
  rooms: {},
  currentTurn: null,
  turns: {},
  proposals: [],
  fresh: {},
  activeThread: null,
  lastError: null,
});

// -- small helpers ------------------------------------------------------------------

export function upsertById<T extends { id: string }>(list: T[], item: T): T[] {
  const i = list.findIndex((x) => x.id === item.id);
  if (i === -1) return [...list, item];
  const next = list.slice();
  next[i] = item;
  return next;
}

export function upsertRoster(roster: RosterEntry[], entry: RosterEntry): { roster: RosterEntry[]; isNew: boolean } {
  const i = roster.findIndex((r) => r.bot_id === entry.bot_id);
  if (i !== -1) {
    const next = roster.slice();
    next[i] = entry;
    return { roster: next, isNew: false };
  }
  // Cat Crew always stays first.
  return { roster: [...roster, entry], isNew: true };
}

export function upsertMessage(list: Message[], msg: Message): Message[] {
  return upsertById(list, msg);
}

export function upsertStep(steps: LiveStep[], step: LiveStep): LiveStep[] {
  const i = steps.findIndex((s) => s.n === step.n);
  const next = steps.slice();
  if (i === -1) next.push(step);
  else next[i] = step;
  next.sort((a, b) => a.n - b.n);
  return next.length > MAX_LIVE_STEPS ? next.slice(next.length - MAX_LIVE_STEPS) : next;
}

function withRoom(s: CoreState, pid: string, fn: (r: RoomState) => RoomState): Partial<CoreState> | null {
  const room = s.rooms[pid];
  if (!room) return null; // never-opened project: its data is fetched when it is opened
  return { rooms: { ...s.rooms, [pid]: fn(room) } };
}

function withThread(r: RoomState, botId: string, fn: (t: ThreadState) => ThreadState): RoomState {
  const t = r.threads[botId] ?? emptyThread();
  return { ...r, threads: { ...r.threads, [botId]: fn(t) } };
}

/** Merge a REST snapshot with live items that arrived while it was in flight. */
export function mergeSnapshot<T>(
  snapshot: T[],
  live: T[],
  order: (a: T, b: T) => number,
  key: (x: T) => string = (x) => (x as unknown as { id: string }).id,
): T[] {
  const byKey = new Map(snapshot.map((x) => [key(x), x] as const));
  for (const item of live) if (!byKey.has(key(item))) byKey.set(key(item), item);
  return [...byKey.values()].sort(order);
}

// -- the reducer --------------------------------------------------------------------

/** Returns the state patch for one server event, or null when nothing changes. */
export function reduce(s: CoreState, ev: ServerEvent, now: number = Date.now()): Partial<CoreState> | null {
  const pid = ev.project_id ?? "";
  switch (ev.type) {
    case "hello":
      return null; // handled by the owner (resync)

    case "bot.staffed": {
      const { agent, entry } = ev.data;
      // Cat Crew is the roster's first card but not an agent: it stays out of `agents`,
      // which feeds the Skills / Tools matrices (only specialist cats have assignments).
      const patch: Partial<CoreState> = {
        agents: entry.is_room ? s.agents : { ...s.agents, [agent.id]: agent },
        activity: s.activity[agent.id] ? s.activity : { ...s.activity, [agent.id]: entry.activity },
      };
      const room = s.rooms[pid];
      if (room) {
        const { roster, isNew } = upsertRoster(room.roster, entry);
        patch.rooms = { ...s.rooms, [pid]: { ...room, roster } };
        if (isNew) patch.fresh = { ...s.fresh, [`${pid}:${agent.id}`]: now };
      }
      return patch;
    }

    case "bot.released":
      return withRoom(s, pid, (r) => ({ ...r, roster: r.roster.filter((e) => e.bot_id !== ev.data.bot_id) }));

    case "bot.activity":
      return { activity: { ...s.activity, [ev.data.bot_id]: ev.data } };

    case "phase.started":
      return withRoom(s, pid, (r) => ({ ...r, phase: ev.data }));

    case "cat.swapped":
      return withRoom(s, pid, (r) => ({ ...r, activeCat: ev.data }));

    case "context.usage":
      // The event has no timestamp of its own: stamp it here so the inspector can tell it from a polled reading.
      return withRoom(s, pid, (r) => ({ ...r, context: { ...ev.data, updated_at: new Date(now).toISOString() } }));

    case "task.changed": {
      const { task } = ev.data;
      return withRoom(s, task.project_id || pid, (r) => ({
        ...r,
        tasks: upsertById(r.tasks, task).sort((a, b) => a.created_at.localeCompare(b.created_at)),
      }));
    }

    case "file.changed": {
      const { change } = ev.data;
      return withRoom(s, pid, (r) => {
        if (r.changes.some((c) => c.diff_uuid === change.diff_uuid)) return r;
        return { ...r, changes: [change, ...r.changes].slice(0, MAX_CHANGES) };
      });
    }

    case "chat.message": {
      const { thread, message } = ev.data;
      const patch = withRoom(s, pid, (r) =>
        withThread(r, thread, (t) => {
          const known = t.messages.some((m) => m.id === message.id);
          const viewing = s.activeThread?.pid === pid && s.activeThread.botId === thread;
          // A finished assistant turn freezes its card into the message: drop the live steps.
          // Cat Crew's own chat reply also freezes its card; specialists' messages in the
          // Cat Crew thread do not (their turn is still running).
          const clearSteps = message.role === "assistant" && (thread !== ROOM_ID || (Boolean(message.bot_id) && isRoomId(message.bot_id)));
          return {
            ...t,
            messages: upsertMessage(t.messages, message),
            steps: clearSteps ? [] : t.steps,
            unread: !known && !viewing && message.role === "assistant" ? t.unread + 1 : t.unread,
          };
        }),
      );
      return patch;
    }

    case "chat.activity": {
      const { thread, turn_id, step } = ev.data;
      return withRoom(s, pid, (r) =>
        withThread(r, thread, (t) => ({
          ...t,
          turnId: turn_id,
          steps: t.turnId === turn_id ? upsertStep(t.steps, step) : [step],
        })),
      );
    }

    case "turn.started": {
      const room = s.rooms[pid];
      const turns = { ...s.turns, [ev.data.project_id || pid]: ev.data };
      if (room?.modelIssue) return { currentTurn: ev.data, turns, rooms: { ...s.rooms, [pid]: { ...room, modelIssue: null } } };
      return { currentTurn: ev.data, turns };
    }

    case "turn.finished":
    case "turn.failed": {
      const turns = Object.fromEntries(Object.entries(s.turns).filter(([, t]) => t.id !== ev.data.turn_id));
      const left = Object.values(turns).sort((a, b) => a.started_at.localeCompare(b.started_at));
      const currentTurn = s.currentTurn?.id === ev.data.turn_id ? left[left.length - 1] ?? null : s.currentTurn;
      const patch: Partial<CoreState> = { currentTurn, turns };
      const room = s.rooms[pid];
      if (room) {
        const threads: Record<string, ThreadState> = {};
        for (const [k, t] of Object.entries(room.threads)) threads[k] = { ...t, steps: [], turnId: null };
        const modelIssue = ev.type === "turn.failed" ? ev.data.model_issue ?? null : room.modelIssue;
        patch.rooms = { ...s.rooms, [pid]: { ...room, threads, phase: null, activeCat: null, approvals: [], asks: [], modelIssue } };
      }
      return patch;
    }

    case "approval.requested":
      return withRoom(s, pid, (r) => ({
        ...r,
        approvals: [...r.approvals.filter((a) => a.approval_id !== ev.data.approval_id), { ...ev.data, project_id: pid }],
      }));
    case "approval.resolved":
      return withRoom(s, pid, (r) => ({ ...r, approvals: r.approvals.filter((a) => a.approval_id !== ev.data.approval_id) }));
    case "ask.requested":
      return withRoom(s, pid, (r) => ({
        ...r,
        asks: [...r.asks.filter((a) => a.ask_id !== ev.data.ask_id), { ...ev.data, project_id: pid }],
      }));
    case "ask.resolved":
      return withRoom(s, pid, (r) => ({ ...r, asks: r.asks.filter((a) => a.ask_id !== ev.data.ask_id) }));

    case "skill.proposed":
      return { proposals: upsertById(s.proposals, ev.data.skill) };

    case "project.changed": {
      const { project } = ev.data;
      const i = s.projects.findIndex((p) => p.id === project.id);
      const projects = s.projects.slice();
      if (i === -1) projects.push(project);
      else projects[i] = project;
      return { projects };
    }

    default:
      return null;
  }
}

/** The running turn of project `pid`, or null. Reads `turns`, falling back to `currentTurn`. */
export function turnFor(s: Pick<CoreState, "turns" | "currentTurn">, pid: string): Turn | null {
  return s.turns[pid] ?? (s.currentTurn?.project_id === pid ? s.currentTurn : null);
}

/** Some other project than `pid` has a turn running. */
export function runningElsewhere(s: Pick<CoreState, "turns" | "currentTurn">, pid: string): boolean {
  return Object.values(s.turns).some((t) => t.project_id !== pid) || Boolean(s.currentTurn && s.currentTurn.project_id !== pid);
}
