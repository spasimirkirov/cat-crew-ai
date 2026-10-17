// Where the backend lives. The desktop shell starts it on this loopback address;
// `VITE_API_URL` overrides it for dev (e.g. a backend on another port).

const raw = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://127.0.0.1:8765";

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

export const isTauri = (): boolean =>
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in (window as unknown as Record<string, unknown>);
