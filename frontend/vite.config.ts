import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The Tauri dev server expects a fixed port; the backend allow-lists these origins.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  base: "./",
  server: { host: "127.0.0.1", port: 1420, strictPort: true },
  build: { target: "es2021", sourcemap: false },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
    css: false,
  },
});
