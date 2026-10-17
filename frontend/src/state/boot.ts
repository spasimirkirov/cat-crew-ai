// Start-up handshake with the backend.
//
// The desktop shell starts the Python engine and opens the window at the same time, and the engine takes
// a few seconds to listen. So "cannot reach the backend" is the *normal* state for the first moments and
// must never be shown as an error: keep asking quietly, and only give up when the engine has clearly
// failed (the shell says so, or the backend answered with a real error) or has been silent for too long.

import { ApiError } from "../lib/api";
import type { ShellBackendStatus } from "../lib/window";

/** The shell waits up to 25 s for the engine to report ready; a little longer here so it can speak first. */
export const BOOT_TIMEOUT_MS = 30_000;
/** Pauses between quiet attempts; the last one repeats. */
export const BOOT_DELAYS_MS = [250, 400, 600, 800, 1000];

export type BootResult = { ok: true } | { ok: false; error: string; detail?: string };

export interface BootDeps {
  /** Load the start-up data. Resolves when the UI has what it needs; rejects with the failure. */
  attempt: () => Promise<void>;
  /** The desktop shell's view of the engine (null when not in the desktop app). */
  shell: () => Promise<ShellBackendStatus | null>;
  /** True once something else (the socket's `hello` resync) has already loaded the data. */
  isReady: () => boolean;
  sleep: (ms: number) => Promise<void>;
  now: () => number;
  onAttempt?: (attempts: number) => void;
  timeoutMs?: number;
  delays?: number[];
}

const unreachable = (err: unknown) => err instanceof ApiError && err.status === 0;
const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

export async function runBoot(deps: BootDeps): Promise<BootResult> {
  const timeoutMs = deps.timeoutMs ?? BOOT_TIMEOUT_MS;
  const delays = deps.delays ?? BOOT_DELAYS_MS;
  const deadline = deps.now() + timeoutMs;

  for (let attempts = 0; ; ) {
    if (deps.isReady()) return { ok: true };

    // The shell knows things the network cannot tell us: the engine never started, or died.
    const shell = await deps.shell();
    if (shell?.state === "failed") {
      return { ok: false, error: "The CatCrewAI engine could not start.", detail: shell.detail };
    }

    try {
      await deps.attempt();
      return { ok: true };
    } catch (err) {
      if (deps.isReady()) return { ok: true };
      // The backend answered, so it is up: retrying will not fix a real error.
      if (!unreachable(err)) return { ok: false, error: message(err) };
    }

    attempts += 1;
    deps.onAttempt?.(attempts);
    if (deps.now() >= deadline) {
      return {
        ok: false,
        error: `The CatCrewAI engine did not answer within ${Math.round(timeoutMs / 1000)} seconds.`,
        detail: shell?.detail,
      };
    }
    await deps.sleep(delays[Math.min(attempts - 1, delays.length - 1)]);
  }
}
