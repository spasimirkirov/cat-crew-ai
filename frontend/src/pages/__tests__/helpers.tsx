import { act, fireEvent, render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { vi } from "vitest";

import { api } from "../../lib/api";
import type { Agent, Project, Telemetry } from "../../lib/types";
import { initialCore } from "../../state/reducer";
import { useStore } from "../../state/store";

export function makeAgent(over: Partial<Agent> & { id: string }): Agent {
  return {
    name: over.id.charAt(0).toUpperCase() + over.id.slice(1),
    role: "developer",
    title: "Specialist",
    description: "",
    avatar: "",
    color: "#22d3ee",
    skills: [],
    playbooks: [],
    tools: [],
    letta_tools: [],
    mcp_tools: [],
    essential_tools: [],
    max_rounds: 8,
    is_system: false,
    ...over,
  };
}

export function makeProject(over: Partial<Project> & { id: string }): Project {
  return { name: "Project", description: "", folders: [], bot_ids: [], created_at: null, phase: "", ...over };
}

/** Reset the zustand store to a clean, ready state (actions are kept). */
export function seedStore(patch: Partial<ReturnType<typeof initialCore>> = {}) {
  useStore.setState({ ...initialCore(), ready: true, connection: "open", ...patch });
}

export function agentsRecord(...agents: Agent[]): Record<string, Agent> {
  return Object.fromEntries(agents.map((a) => [a.id, a]));
}

function Where() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname}</div>;
}

/** Render `ui` at "/" inside a MemoryRouter; any other route renders only the location probe. */
export function renderPage(ui: ReactElement, path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/" element={ui} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** A tiny stand-in for user-event (not installed): async-shaped so tests read the same. */
export const user = {
  async click(el: Element) {
    await act(async () => {
      fireEvent.click(el);
    });
  },
  async type(el: Element, text: string) {
    const input = el as HTMLInputElement | HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: input.value + text } });
  },
  async clear(el: Element) {
    fireEvent.change(el, { target: { value: "" } });
  },
  async escape() {
    fireEvent.keyDown(document.body, { key: "Escape" });
  },
};

/** A quiet snapshot for the inspector's Telemetry tab: nothing configured, nothing measured. */
export function makeTelemetry(over: Partial<Telemetry> = {}): Telemetry {
  return {
    project_id: "p1",
    updated_at: "2026-01-01T00:00:00+00:00",
    context: null,
    context_limit: 64000,
    context_threshold: 54000,
    active: { role: "coding", host: "lmstudio", host_label: "LM Studio", model: "qwen-coder", source: "global", local: true, status: "ready", message: "" },
    openrouter: { host: "openrouter", configured: false, status: "no_key" },
    openrouter_cache: { host: "openrouter", calls: 0, prompt_tokens: 0, cached_tokens: 0, cache_hit_pct: 0, estimated_pct_spend_saved: 0, estimated_usd_saved: null },
    jev: {
      enabled: false,
      model: "jev-latest",
      confidence_threshold: 0.92,
      precheck_calls: 0,
      bypasses: 0,
      guard_calls: 0,
      blocked_tool_calls: 0,
      interceptions: 0,
      tokens_saved_estimate: 0,
      avg_decision_latency_ms: 0,
      estimated_usd_saved: null,
    },
    gpu: { available: false },
    ...over,
  };
}

/**
 * Stand in for the Telemetry tab's poll. With a snapshot it answers with it; without one the request never
 * finishes, which keeps the real network out of room tests that are not about telemetry (and leaves nothing
 * to resolve after the test has ended).
 */
export function mockTelemetry(snapshot?: Telemetry) {
  const spy = vi.spyOn(api.telemetry, "live");
  return snapshot ? spy.mockResolvedValue(snapshot) : spy.mockReturnValue(new Promise<Telemetry>(() => {}));
}
