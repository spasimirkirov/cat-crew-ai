import { isTauri } from "./config";

/**
 * The native window is created hidden (see src-tauri/tauri.conf.json) with the
 * app background colour, so nothing paints until the DOM has. We reveal it after
 * the first frame of the React tree is on screen: zero flicker, no white flash.
 */
export function revealWindowAfterFirstPaint(): void {
  if (!isTauri()) return;
  requestAnimationFrame(() =>
    requestAnimationFrame(async () => {
      try {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        await getCurrentWindow().show();
      } catch {
        /* the Rust side also shows the window after a short timeout */
      }
    }),
  );
}

/**
 * Really quit the app: the Rust side (`quit_app` in src-tauri/src/lib.rs) stops the Python
 * backend it started, then exits. The window's "X" button only hides to the tray instead.
 */
export async function quitApp(): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("quit_app");
}

/** What the desktop shell knows about the Python engine it started (`backend_status` in src-tauri). */
export interface ShellBackendStatus {
  state: "starting" | "ready" | "failed" | "external";
  /** Why it failed, with the tail of the engine's own output. Only set when `state` is "failed". */
  detail?: string;
}

/** The shell's view of the engine, or null outside the desktop app (or if the shell cannot say). */
export async function shellBackendStatus(): Promise<ShellBackendStatus | null> {
  if (!isTauri()) return null;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<ShellBackendStatus>("backend_status");
  } catch {
    return null;
  }
}
