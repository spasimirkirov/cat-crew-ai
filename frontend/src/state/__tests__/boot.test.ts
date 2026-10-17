import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api";
import type { ShellBackendStatus } from "../../lib/window";
import { runBoot, type BootDeps } from "../boot";

const down = () => new ApiError(0, "Cannot reach the CatCrewAI backend. Is it running?");

/** A fake clock: `sleep` advances it, so the deadline logic runs without real waiting. */
function harness(over: Partial<BootDeps> = {}) {
  let t = 0;
  const sleeps: number[] = [];
  let ready = false;
  const deps: BootDeps = {
    attempt: vi.fn().mockResolvedValue(undefined),
    shell: vi.fn().mockResolvedValue(null),
    isReady: () => ready,
    sleep: async (ms) => {
      sleeps.push(ms);
      t += ms;
    },
    now: () => t,
    ...over,
  };
  return { deps, sleeps, setReady: () => (ready = true), clock: () => t };
}

describe("runBoot", () => {
  it("succeeds at once when the engine is already up", async () => {
    const h = harness();
    expect(await runBoot(h.deps)).toEqual({ ok: true });
    expect(h.deps.attempt).toHaveBeenCalledTimes(1);
    expect(h.sleeps).toEqual([]);
  });

  it("keeps asking quietly while the engine is unreachable, then succeeds (the engine is just starting)", async () => {
    const attempt = vi.fn().mockRejectedValueOnce(down()).mockRejectedValueOnce(down()).mockRejectedValueOnce(down()).mockResolvedValue(undefined);
    const onAttempt = vi.fn();
    const h = harness({ attempt, onAttempt });
    expect(await runBoot(h.deps)).toEqual({ ok: true });
    expect(attempt).toHaveBeenCalledTimes(4);
    expect(onAttempt.mock.calls.map((c) => c[0])).toEqual([1, 2, 3]);
    expect(h.sleeps).toEqual([250, 400, 600]); // backing off, never a failure in between
  });

  it("repeats the last delay and gives up only after the deadline", async () => {
    const h = harness({ attempt: vi.fn().mockRejectedValue(down()), timeoutMs: 5000 });
    const res = await runBoot(h.deps);
    expect(res).toEqual({ ok: false, error: "The CatCrewAI engine did not answer within 5 seconds.", detail: undefined });
    expect(h.sleeps.slice(0, 5)).toEqual([250, 400, 600, 800, 1000]);
    expect(h.sleeps.slice(5).every((s) => s === 1000)).toBe(true);
    expect(h.clock()).toBeGreaterThanOrEqual(4000);
    expect(h.clock()).toBeLessThanOrEqual(6000);
  });

  it("does not retry a real error: the backend answered, so it is up", async () => {
    const attempt = vi.fn().mockRejectedValue(new ApiError(500, "Internal Server Error"));
    const h = harness({ attempt });
    expect(await runBoot(h.deps)).toEqual({ ok: false, error: "Internal Server Error" });
    expect(attempt).toHaveBeenCalledTimes(1);
    expect(h.sleeps).toEqual([]);
  });

  it("fails at once, with the reason, when the desktop shell says the engine could not start", async () => {
    const status: ShellBackendStatus = { state: "failed", detail: "python: command not found" };
    const h = harness({ shell: vi.fn().mockResolvedValue(status), attempt: vi.fn().mockRejectedValue(down()) });
    expect(await runBoot(h.deps)).toEqual({ ok: false, error: "The CatCrewAI engine could not start.", detail: "python: command not found" });
    expect(h.deps.attempt).not.toHaveBeenCalled();
  });

  it("keeps waiting while the shell says it is starting, and hands its detail to a timeout", async () => {
    const h = harness({
      shell: vi.fn().mockResolvedValue({ state: "starting" } satisfies ShellBackendStatus),
      attempt: vi.fn().mockRejectedValue(down()),
      timeoutMs: 1000,
    });
    const res = await runBoot(h.deps);
    expect(res.ok).toBe(false);
    expect(vi.mocked(h.deps.shell).mock.calls.length).toBeGreaterThan(1); // asked again on every round
  });

  it("stops as soon as something else has loaded the data (the socket's hello resync)", async () => {
    let h!: ReturnType<typeof harness>;
    const attempt = vi.fn().mockImplementation(async () => {
      h.setReady(); // the resync won the race while this request was failing
      throw down();
    });
    h = harness({ attempt });
    expect(await runBoot(h.deps)).toEqual({ ok: true });
    expect(attempt).toHaveBeenCalledTimes(1);
    expect(h.sleeps).toEqual([]);
  });
});
