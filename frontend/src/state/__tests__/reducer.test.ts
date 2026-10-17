import { describe, expect, it } from "vitest";

import type { Agent, Message, RosterEntry, ServerEvent, Task } from "../../lib/types";
import { emptyRoom, initialCore, reduce, upsertStep, type CoreState } from "../reducer";

const PID = "p1";
let seq = 0;
const ev = <T extends ServerEvent["type"]>(type: T, data: unknown, project_id: string | null = PID): ServerEvent =>
  ({ v: 1, seq: ++seq, ts: "2026-01-01T00:00:00+00:00", type, project_id, data }) as unknown as ServerEvent;

const agent = (id: string): Agent => ({
  id, name: id, role: "developer", title: "", description: "", avatar: "", color: "#fff",
  skills: [], playbooks: [], tools: [], letta_tools: [], mcp_tools: [], max_rounds: 8, is_system: false,
});
const entry = (id: string): RosterEntry => ({
  bot_id: id, agent: agent(id), is_room: id === "crew", joined_at: null,
  activity: { bot_id: id, status: "idle", state: "idle", text: "", updated_at: null },
});
const task = (id: string, status: Task["status"], created = "2026-01-01T00:00:01+00:00"): Task => ({
  id, project_id: PID, assigned_to: "pulse", created_by: "lead", title: id, description: "", status,
  created_at: created, updated_at: created, latest_summary: "",
});
const msg = (id: string, role: Message["role"], text = "hi"): Message => ({ id, role, text, created_at: "2026-01-01T00:00:00+00:00" });

/** Apply events the way the store does. */
function apply(s: CoreState, ...events: ServerEvent[]): CoreState {
  return events.reduce((acc, e) => ({ ...acc, ...(reduce(acc, e, 1000) ?? {}) }), s);
}
const opened = (): CoreState => ({ ...initialCore(), rooms: { [PID]: { ...emptyRoom(), loaded: true } } });

describe("reducer: fleet pop-in", () => {
  it("Cat Crew joins the roster but never the agents map (it is not an agent: no matrix column)", () => {
    const s = apply(opened(), ev("bot.staffed", { bot_id: "crew", agent: agent("crew"), entry: entry("crew") }));
    expect(s.rooms[PID].roster.map((r) => r.bot_id)).toEqual(["crew"]);
    expect(s.agents.crew).toBeUndefined();
  });

  it("Cat Crew's own chat reply freezes its card and drops the live steps", () => {
    let s = apply(opened(), ev("chat.activity", { thread: "crew", turn_id: "t1", bot_id: "crew", step: { n: 1, line: "Thinking" } }));
    s = apply(s, ev("chat.message", { thread: "crew", message: { ...msg("m1", "assistant"), bot_id: "crew" } }));
    expect(s.rooms[PID].threads.crew.steps).toEqual([]);
  });

  it("adds a staffed cat to the roster and marks it fresh; a repeat is idempotent and not fresh again", () => {
    let s = apply(opened(), ev("bot.staffed", { bot_id: "pulse", agent: agent("pulse"), entry: entry("pulse") }));
    expect(s.rooms[PID].roster.map((r) => r.bot_id)).toEqual(["pulse"]);
    expect(s.fresh[`${PID}:pulse`]).toBe(1000);
    expect(s.agents.pulse).toBeTruthy();

    s = { ...s, fresh: {} };
    s = apply(s, ev("bot.staffed", { bot_id: "pulse", agent: agent("pulse"), entry: entry("pulse") }));
    expect(s.rooms[PID].roster).toHaveLength(1);
    expect(s.fresh).toEqual({});
  });

  it("ignores room data for a project that was never opened, but keeps global activity", () => {
    const s = apply(initialCore(), ev("task.changed", { task: task("t1", "pending") }), ev("bot.activity", { bot_id: "pulse", status: "coding", state: "working", text: "x", updated_at: null }, null));
    expect(s.rooms[PID]).toBeUndefined();
    expect(s.activity.pulse.status).toBe("coding");
  });

  it("removes a released cat", () => {
    let s = apply(opened(), ev("bot.staffed", { bot_id: "pulse", agent: agent("pulse"), entry: entry("pulse") }));
    s = apply(s, ev("bot.released", { bot_id: "pulse" }));
    expect(s.rooms[PID].roster).toEqual([]);
  });
});

describe("reducer: tasks and changes", () => {
  it("upserts tasks by id and keeps creation order", () => {
    const s = apply(opened(), ev("task.changed", { task: task("b", "pending", "2026-01-01T00:00:02+00:00") }), ev("task.changed", { task: task("a", "pending") }), ev("task.changed", { task: task("b", "done", "2026-01-01T00:00:02+00:00") }));
    expect(s.rooms[PID].tasks.map((t) => [t.id, t.status])).toEqual([["a", "pending"], ["b", "done"]]);
  });

  it("de-duplicates file changes by diff_uuid, newest first", () => {
    const change = (n: number) => ({ id: `c${n}`, task_id: "t", project_id: PID, agent_id: "pulse", file_path: `f${n}`, diff_uuid: `d${n}`, created_at: "" });
    const s = apply(opened(), ev("file.changed", { change: change(1) }), ev("file.changed", { change: change(2) }), ev("file.changed", { change: change(1) }));
    expect(s.rooms[PID].changes.map((c) => c.diff_uuid)).toEqual(["d2", "d1"]);
  });
});

describe("reducer: chat", () => {
  it("chat.message is an upsert by id and counts unread only once, for threads not being viewed", () => {
    let s = apply(opened(), ev("chat.message", { thread: "pulse", message: msg("m1", "assistant", "draft") }));
    expect(s.rooms[PID].threads.pulse.unread).toBe(1);
    s = apply(s, ev("chat.message", { thread: "pulse", message: msg("m1", "assistant", "final") }));
    const t = s.rooms[PID].threads.pulse;
    expect(t.messages).toHaveLength(1);
    expect(t.messages[0].text).toBe("final");
    expect(t.unread).toBe(1);

    s = { ...s, activeThread: { pid: PID, botId: "pulse" } };
    s = apply(s, ev("chat.message", { thread: "pulse", message: msg("m2", "assistant") }));
    expect(s.rooms[PID].threads.pulse.unread).toBe(1);
  });

  it("live steps are ordered by n, replaced on a new turn, and dropped when the cat's message lands", () => {
    const step = (turn: string, n: number, line: string) => ev("chat.activity", { thread: "pulse", turn_id: turn, bot_id: "pulse", step: { n, line } });
    let s = apply(opened(), step("t1", 2, "b"), step("t1", 1, "a"), step("t1", 2, "b2"));
    expect(s.rooms[PID].threads.pulse.steps.map((x) => x.line)).toEqual(["a", "b2"]);
    s = apply(s, step("t2", 1, "fresh"));
    expect(s.rooms[PID].threads.pulse.steps.map((x) => x.line)).toEqual(["fresh"]);
    s = apply(s, ev("chat.message", { thread: "pulse", message: msg("m1", "assistant") }));
    expect(s.rooms[PID].threads.pulse.steps).toEqual([]);
  });

  it("Cat Crew keeps its live steps while specialists work, until the turn ends", () => {
    let s = apply(opened(), ev("chat.activity", { thread: "crew", turn_id: "t1", bot_id: "crew", step: { n: 1, line: "Delegating" } }));
    s = apply(s, ev("chat.message", { thread: "crew", message: { ...msg("m1", "assistant"), bot_id: "pulse" } }));
    expect(s.rooms[PID].threads.crew.steps).toHaveLength(1);
    s = apply(s, ev("turn.finished", { turn_id: "t1", status: "ok" }));
    expect(s.rooms[PID].threads.crew.steps).toEqual([]);
  });

  it("upsertStep caps the live log", () => {
    let steps: { n: number; line: string }[] = [];
    for (let n = 1; n <= 100; n++) steps = upsertStep(steps, { n, line: String(n) });
    expect(steps).toHaveLength(80);
    expect(steps[steps.length - 1].n).toBe(100);
  });
});

describe("reducer: turns", () => {
  it("follows the persona swap and forgets it when the turn ends", () => {
    const turn = { id: "t9", project_id: PID, bot_id: "crew", started_at: "", phase: "", kind: "room" as const };
    let s = apply(opened(), ev("turn.started", turn), ev("cat.swapped", { cat_id: "forge", name: "Forge", phase: "devops", label: "Phase 3: DevOps", status: "Thinking" }));
    expect(s.rooms[PID].activeCat).toMatchObject({ cat_id: "forge", name: "Forge", status: "Thinking" });
    s = apply(s, ev("cat.swapped", { cat_id: "pulse", name: "Pulse", phase: "build", label: "Phase 4: Build", status: "Coding" }));
    expect(s.rooms[PID].activeCat?.cat_id).toBe("pulse");
    s = apply(s, ev("turn.finished", { turn_id: "t9", status: "ok" }));
    expect(s.rooms[PID].activeCat).toBeNull();
  });

  it("tracks the running turn and clears phase/approvals when it ends or fails", () => {
    const turn = { id: "t1", project_id: PID, bot_id: "crew", started_at: "", phase: "", kind: "room" as const };
    let s = apply(opened(), ev("turn.started", turn), ev("phase.started", { phase: "build", bot_id: "pulse", label: "Build" }), ev("approval.requested", { approval_id: "a1", bot_id: "pulse", tool: "run_command", detail: "npm i" }));
    expect(s.currentTurn?.id).toBe("t1");
    expect(s.rooms[PID].phase?.phase).toBe("build");
    expect(s.rooms[PID].approvals).toHaveLength(1);
    s = apply(s, ev("turn.failed", { turn_id: "t1", error: "boom" }));
    expect(s.currentTurn).toBeNull();
    expect(s.rooms[PID].phase).toBeNull();
    expect(s.rooms[PID].approvals).toEqual([]);
  });

  it("does not clear a different, newer turn", () => {
    const turn = { id: "t2", project_id: PID, bot_id: "crew", started_at: "", phase: "", kind: "room" as const };
    const s = apply(opened(), ev("turn.started", turn), ev("turn.finished", { turn_id: "t1", status: "ok" }));
    expect(s.currentTurn?.id).toBe("t2");
  });
});

describe("reducer: context usage", () => {
  const reading = { host: "lmstudio", model: "qwen", tokens: 4120, system_tokens: 900, limit: 8192, percent: 50, level: "ok", compressed: false };

  it("keeps the latest reading of the project and stamps it", () => {
    let s = apply(opened(), ev("context.usage", reading));
    expect(s.rooms[PID].context).toMatchObject({ tokens: 4120, limit: 8192, percent: 50 });
    expect(s.rooms[PID].context?.updated_at).toBe(new Date(1000).toISOString());
    s = apply(s, ev("context.usage", { ...reading, tokens: 7000, percent: 85, level: "warn" }));
    expect(s.rooms[PID].context?.tokens).toBe(7000);
  });

  it("survives the end of the turn (the gauge keeps its last value) and is per project", () => {
    let s = apply(opened(), ev("context.usage", reading));
    s = apply(s, ev("turn.finished", { turn_id: "t1", status: "ok" }));
    expect(s.rooms[PID].context?.tokens).toBe(4120);
    const other = apply({ ...opened(), rooms: { ...opened().rooms, p2: { ...emptyRoom(), loaded: true } } }, ev("context.usage", reading, "p2"));
    expect(other.rooms[PID].context).toBeNull();
    expect(other.rooms.p2.context?.tokens).toBe(4120);
  });

  it("ignores a reading for a project that was never opened", () => {
    expect(reduce(initialCore(), ev("context.usage", reading))).toBeNull();
  });
});

describe("reducer: a model the host turned away", () => {
  const issue = {
    host: "openrouter", model: "google/gemma-4-26b-a4b-it:free", kind: "rate_limited" as const, status: 429,
    message: "rate-limited upstream", free: true, at: 1, roles: [{ role: "coding", label: "Coding", source: "global" }],
  };
  const turn = { id: "t5", project_id: PID, bot_id: "crew", started_at: "", phase: "", kind: "room" as const };

  it("keeps the switch-and-retry offer after the failed turn, and drops it when a new turn starts", () => {
    let s = apply(opened(), ev("turn.started", turn), ev("turn.failed", { turn_id: "t5", error: "Error: 429", model_issue: issue }));
    expect(s.rooms[PID].modelIssue).toEqual(issue);
    s = apply(s, ev("turn.finished", { turn_id: "t0", status: "ok" }));
    expect(s.rooms[PID].modelIssue).toEqual(issue); // an unrelated finish keeps it
    s = apply(s, ev("turn.started", { ...turn, id: "t6" }));
    expect(s.rooms[PID].modelIssue).toBeNull();
  });

  it("a failure with no model issue shows no offer", () => {
    const s = apply(opened(), ev("turn.failed", { turn_id: "t7", error: "Error: boom" }));
    expect(s.rooms[PID].modelIssue).toBeNull();
  });
});
