import { create } from "zustand";

import { api, ApiError } from "../lib/api";
import type { Agent, Message, Project, ServerEvent, Skill, Turn } from "../lib/types";
import { ROOM_ID } from "../lib/types";
import { shellBackendStatus } from "../lib/window";
import { runBoot } from "./boot";
import {
  bootStarting,
  emptyRoom,
  emptyThread,
  initialCore,
  mergeSnapshot,
  reduce,
  type CoreState,
  type RoomState,
} from "./reducer";

export interface Actions {
  applyEvent: (ev: ServerEvent) => void;
  bootstrap: () => Promise<void>;
  /** The start-up handshake: keep asking quietly until the engine answers, or clearly will not. */
  connect: () => Promise<void>;
  /** Refetch everything the UI is showing (called on every WebSocket `hello`). */
  resync: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  refreshProposals: () => Promise<void>;
  loadProject: (pid: string) => Promise<void>;
  loadThread: (pid: string, botId: string) => Promise<void>;
  setActiveThread: (t: { pid: string; botId: string } | null) => void;
  sendMessage: (pid: string, botId: string, text: string) => Promise<{ queued: boolean }>;
  setError: (msg: string | null) => void;
  clearFresh: (key: string) => void;
}

export type Store = CoreState & Actions;

const byCreated = <T extends { created_at: string }>(a: T, b: T) => a.created_at.localeCompare(b.created_at);

export function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

let resyncing: Promise<void> | null = null;
let connecting: Promise<void> | null = null;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

type SetState = (partial: Partial<Store> | ((s: Store) => Partial<Store>)) => void;

/** Fetch `/api/bootstrap` and load it. Rejects (with the ApiError) so the caller decides what a failure means. */
async function loadBootstrap(set: SetState): Promise<void> {
  const boot = await api.bootstrap();
  set((s) => {
    // Wholesale replace: an agent deleted elsewhere must disappear here too.
    const agents: Record<string, Agent> = {};
    for (const a of boot.agents) agents[a.id] = a;
    const activity = { ...s.activity };
    for (const a of boot.activity) activity[a.bot_id] = a;
    const turns: Record<string, Turn> = {};
    for (const t of boot.turns ?? (boot.current_turn ? [boot.current_turn] : [])) turns[t.project_id] = t;
    return { ready: true, agents, activity, projects: boot.projects, currentTurn: boot.current_turn, turns, lastError: null };
  });
}

export const useStore = create<Store>()((set, get) => ({
  ...initialCore(),

  applyEvent: (ev) => {
    const patch = reduce(get(), ev);
    if (patch) set(patch);
  },

  setError: (msg) => set({ lastError: msg }),

  clearFresh: (key) =>
    set((s) => {
      if (!(key in s.fresh)) return s;
      const fresh = { ...s.fresh };
      delete fresh[key];
      return { fresh };
    }),

  bootstrap: async () => {
    try {
      await loadBootstrap(set);
    } catch (err) {
      set({ lastError: errorText(err) });
    }
  },

  connect: () => {
    if (connecting) return connecting;
    set({ boot: bootStarting() });
    connecting = runBoot({
      attempt: () => loadBootstrap(set),
      shell: shellBackendStatus,
      isReady: () => get().ready,
      sleep,
      now: Date.now,
      onAttempt: (attempts) => set((s) => ({ boot: { ...s.boot, attempts } })),
    })
      .then((res) => {
        if (res.ok) return;
        set((s) => (s.ready ? s : { boot: { ...s.boot, phase: "failed", error: res.error, detail: res.detail ?? null } }));
      })
      .finally(() => {
        connecting = null;
      });
    return connecting;
  },

  refreshProjects: async () => {
    try {
      set({ projects: await api.projects.list() });
    } catch (err) {
      set({ lastError: errorText(err) });
    }
  },

  refreshProposals: async () => {
    try {
      const proposals: Skill[] = await api.skills.proposals();
      set({ proposals });
    } catch {
      /* the badge is optional */
    }
  },

  loadProject: async (pid) => {
    set((s) => (s.rooms[pid] ? s : { rooms: { ...s.rooms, [pid]: emptyRoom() } }));
    try {
      const [roster, tasks, changes] = await Promise.all([
        api.roster.list(pid),
        api.tasks.list(pid),
        api.tasks.changes(pid, 40),
      ]);
      set((s) => {
        const prev = s.rooms[pid] ?? emptyRoom();
        const activity = { ...s.activity };
        const agents = { ...s.agents };
        for (const e of roster) {
          if (!e.is_room) agents[e.agent.id] = e.agent; // Cat Crew is not an agent (no matrix column)
          activity[e.bot_id] = activity[e.bot_id] ?? e.activity;
        }
        const liveOnly = prev.roster.filter((e) => !roster.some((r) => r.bot_id === e.bot_id));
        const room: RoomState = {
          ...prev,
          loaded: true,
          roster: [...roster, ...liveOnly],
          tasks: mergeSnapshot(tasks, prev.tasks, byCreated),
          changes: mergeSnapshot(
            changes,
            prev.changes,
            (a, b) => b.created_at.localeCompare(a.created_at),
            (c) => c.diff_uuid,
          ),
        };
        return { agents, activity, rooms: { ...s.rooms, [pid]: room }, lastError: null };
      });
    } catch (err) {
      set({ lastError: errorText(err) });
    }
  },

  loadThread: async (pid, botId) => {
    try {
      const fetched = await api.chat.messages(pid, botId);
      set((s) => {
        const room = s.rooms[pid] ?? emptyRoom();
        const t = room.threads[botId] ?? emptyThread();
        const merged: Message[] = mergeSnapshot(fetched, t.messages, byCreated);
        return {
          rooms: { ...s.rooms, [pid]: { ...room, threads: { ...room.threads, [botId]: { ...t, messages: merged, loaded: true } } } },
        };
      });
    } catch (err) {
      set({ lastError: errorText(err) });
    }
  },

  setActiveThread: (t) =>
    set((s) => {
      if (!t) return { activeThread: null };
      const room = s.rooms[t.pid];
      if (!room) return { activeThread: t };
      const th = room.threads[t.botId] ?? emptyThread();
      return {
        activeThread: t,
        rooms: { ...s.rooms, [t.pid]: { ...room, threads: { ...room.threads, [t.botId]: { ...th, unread: 0 } } } },
      };
    }),

  sendMessage: async (pid, botId, text) => {
    const res = await api.chat.send(pid, botId || ROOM_ID, text);
    return { queued: res.queued };
  },

  resync: async () => {
    if (resyncing) return resyncing;
    resyncing = (async () => {
      await get().bootstrap();
      const { rooms, activeThread } = get();
      await Promise.all(Object.keys(rooms).map((pid) => get().loadProject(pid)));
      if (activeThread) await get().loadThread(activeThread.pid, activeThread.botId);
      void get().refreshProposals();
    })().finally(() => {
      resyncing = null;
    });
    return resyncing;
  },
}));

export function projectById(projects: Project[], id: string | undefined): Project | undefined {
  return id ? projects.find((p) => p.id === id) : undefined;
}
