// Where the backend lives:
// - `VITE_API_URL` wins (dev against a backend on another port);
// - in a browser tab the backend served the page itself (browser mode, `python -m backend --ui`),
//   so the API is the page's own origin, whatever port it picked;
// - otherwise the desktop shell's loopback address.

const DESKTOP_API = "http://127.0.0.1:8765";
// The Vite dev server: the page comes from Vite, the API from the backend.
const VITE_DEV_PORTS = new Set(["1420"]);

export const isTauri = (): boolean =>
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in (window as unknown as Record<string, unknown>);

export function resolveApiBase(
  envUrl: string | undefined,
  location: { protocol: string; origin: string; port: string } | undefined,
  tauri: boolean,
): string {
  if (envUrl) return envUrl;
  if (!tauri && location && /^https?:$/.test(location.protocol) && !VITE_DEV_PORTS.has(location.port)) return location.origin;
  return DESKTOP_API;
}

const raw = resolveApiBase(
  import.meta.env.VITE_API_URL as string | undefined,
  typeof window !== "undefined" ? window.location : undefined,
  isTauri(),
);

export const API_BASE = raw.replace(/\/+$/, "");
export const WS_BASE = API_BASE.replace(/^http/, "ws");

export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Avatar URLs come back as API paths ("/api/agents/pulse/avatar"). */
export function assetUrl(path: string): string {
  if (!path) return "";
  // Bundled static files (e.g. Cat Crew's /avatars/default.png) live next to the app, not the API.
  return path.startsWith("http") || path.startsWith("/avatars/") ? path : apiUrl(path);
}

/** Browser mode: the page was served by the backend itself (not the desktop shell, not Vite). */
export const isBrowserMode = (): boolean => !isTauri() && typeof window !== "undefined" && API_BASE === window.location.origin;
