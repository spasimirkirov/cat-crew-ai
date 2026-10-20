import { describe, expect, it } from "vitest";

import { resolveApiBase } from "./config";

const page = (origin: string) => {
  const url = new URL(origin);
  return { protocol: url.protocol, origin: url.origin, port: url.port };
};

describe("resolveApiBase", () => {
  it("uses the page's own origin when the backend served it (browser mode, any port)", () => {
    expect(resolveApiBase(undefined, page("http://127.0.0.1:51234"), false)).toBe("http://127.0.0.1:51234");
    expect(resolveApiBase(undefined, page("http://localhost:8765"), false)).toBe("http://localhost:8765");
  });

  it("keeps the desktop address inside Tauri and on the Vite dev server", () => {
    expect(resolveApiBase(undefined, page("http://tauri.localhost"), true)).toBe("http://127.0.0.1:8765");
    expect(resolveApiBase(undefined, { protocol: "tauri:", origin: "tauri://localhost", port: "" }, false)).toBe("http://127.0.0.1:8765");
    expect(resolveApiBase(undefined, page("http://127.0.0.1:1420"), false)).toBe("http://127.0.0.1:8765");
  });

  it("lets VITE_API_URL win", () => {
    expect(resolveApiBase("http://127.0.0.1:9000", page("http://127.0.0.1:51234"), false)).toBe("http://127.0.0.1:9000");
  });
});
