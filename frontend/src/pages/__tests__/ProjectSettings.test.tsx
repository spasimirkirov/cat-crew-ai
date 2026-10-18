import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { api } from "../../lib/api";
import type { EffectiveModel, HostModels, ProjectModelsState, RoleName } from "../../lib/types";
import { ProjectSettingsPage } from "../ProjectSettings";
import { makeProject, seedStore, user } from "./helpers";

const ROLE_NAMES: RoleName[] = ["chat", "coding", "memory", "vision", "planning", "review", "fallback", "images"];

const eff = (host: string, model: string, source: EffectiveModel["source"] = "global"): EffectiveModel => ({ host, model, source, explicit: source !== "chat" && source !== "coding" });

/** Global picks: Chat on Groq, Coding on DeepSeek, everything else follows Chat. */
const globalEffective = (): ProjectModelsState["global_effective"] => ({
  chat: eff("groq", "qwen-chat"),
  coding: eff("deepseek", "deepseek-v4-pro"),
  memory: eff("groq", "qwen-chat", "chat"),
  vision: eff("groq", "qwen-chat", "chat"),
  planning: eff("groq", "qwen-chat", "chat"),
  review: eff("deepseek", "deepseek-v4-pro", "coding"),
  fallback: { host: "", model: "", source: "global", explicit: false },
  images: eff("comfyui", "sdxl"),
});

let overrides: ProjectModelsState["overrides"] = {};

const stateNow = (): ProjectModelsState => ({
  project_id: "p1",
  overrides,
  effective: Object.fromEntries(ROLE_NAMES.map((r) => [r, overrides[r] ? { ...overrides[r]!, source: "project", explicit: true } : globalEffective()[r]])) as ProjectModelsState["effective"],
  global: Object.fromEntries(ROLE_NAMES.map((r) => [r, { host: globalEffective()[r].host, model: globalEffective()[r].model }])) as ProjectModelsState["global"],
  global_effective: globalEffective(),
  hosts: [
    { id: "groq", label: "Groq", enabled: true, cloud: true, configured: true },
    { id: "anthropic", label: "Anthropic", enabled: false, cloud: true, configured: false },
    { id: "deepseek", label: "DeepSeek", enabled: true, cloud: true, configured: true },
    { id: "gemini", label: "Google Gemini", enabled: true, cloud: true, configured: true },
  ],
});

const model = (name: string) => ({ name, display_name: name, loaded: false, capabilities: ["tools", "chat"], context_length: 0 });
const probes: Record<string, HostModels> = {
  groq: { host: "groq", online: true, error: "", error_kind: "", checked_at: 1, models: [model("qwen-chat")] },
  deepseek: { host: "deepseek", online: true, error: "", error_kind: "", checked_at: 1, models: [model("deepseek-chat"), model("deepseek-v4-pro")] },
  gemini: { host: "gemini", online: false, error: "unreachable", error_kind: "unreachable", checked_at: 1, models: [] },
};

let setProject: MockInstance<typeof api.models.setProject>;

beforeEach(() => {
  overrides = {};
  seedStore({ projects: [makeProject({ id: "p1", name: "Site" })] });
  vi.spyOn(api.models, "getProject").mockImplementation(async () => stateNow());
  vi.spyOn(api.models, "hostModels").mockImplementation(async (h) => probes[h]);
  vi.spyOn(api.models, "refreshHost").mockImplementation(async (h) => probes[h]);
  setProject = vi.spyOn(api.models, "setProject").mockImplementation(async (_pid, roles) => {
    for (const [role, pick] of Object.entries(roles)) {
      if (pick) overrides = { ...overrides, [role]: pick };
      else {
        const { [role as RoleName]: _gone, ...rest } = overrides;
        overrides = rest;
      }
    }
    return stateNow();
  });
});
afterEach(() => vi.restoreAllMocks());

async function open() {
  render(
    <MemoryRouter initialEntries={["/project/p1/settings"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/project/:pid/settings" element={<ProjectSettingsPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByTestId("override-chat");
}

const row = (role: string) => screen.getByTestId(`override-${role}`);
const hostSelect = (role: string) => within(row(role)).getByLabelText("Host") as HTMLSelectElement;

describe("Project settings: model overrides", () => {
  it("has a card with the six roles, each defaulting to the global setting", async () => {
    await open();
    expect(screen.getByRole("heading", { name: "Project Model Overrides" })).toBeInTheDocument();
    for (const r of ROLE_NAMES) {
      expect(hostSelect(r)).toHaveValue("");
      expect(hostSelect(r).options[0]).toHaveTextContent(/^🌐 Use Global Setting/);
    }
  });

  it("labels the first option with what the global setting currently is", async () => {
    await open();
    expect(hostSelect("coding").options[0]).toHaveTextContent("🌐 Use Global Setting (DeepSeek: deepseek-v4-pro)");
    expect(hostSelect("chat").options[0]).toHaveTextContent("🌐 Use Global Setting (Groq: qwen-chat)");
    expect(hostSelect("memory").options[0]).toHaveTextContent("🌐 Use Global Setting (Groq: qwen-chat)"); // follows Chat
    expect(hostSelect("images").options[0]).toHaveTextContent("🌐 Use Global Setting (ComfyUI: sdxl)");
  });

  it("offers the hosts (Groq, Anthropic, DeepSeek, Gemini) and disables one without a key", async () => {
    await open();
    const options = Array.from(hostSelect("coding").options);
    expect(options.slice(1).map((o) => o.textContent)).toEqual(["Groq", "Anthropic (no API key)", "DeepSeek", "Google Gemini"]);
    expect(options.find((o) => o.value === "anthropic")).toBeDisabled();
    expect(Array.from(hostSelect("images").options).map((o) => o.value)).toEqual(["", "off", "comfyui"]);
  });

  it("saves a project pick for one role and leaves the others on global", async () => {
    await open();
    fireEvent.change(hostSelect("memory"), { target: { value: "deepseek" } });
    await waitFor(() => expect(within(row("memory")).getByRole("combobox", { name: "Model" })).toBeInTheDocument());
    await user.click(within(row("memory")).getByRole("combobox", { name: "Model" }));
    await user.click(within(row("memory")).getByRole("option", { name: /deepseek-chat/ }));
    await user.click(screen.getByRole("button", { name: /save overrides/i }));
    expect(setProject).toHaveBeenCalledWith("p1", { memory: { host: "deepseek", model: "deepseek-chat" } });
    await waitFor(() => expect(within(row("memory")).getByText("Overridden")).toBeInTheDocument());
    expect(hostSelect("coding")).toHaveValue("");
  });

  it("lets a model be typed when the host is offline", async () => {
    await open();
    fireEvent.change(hostSelect("planning"), { target: { value: "gemini" } });
    const input = await within(row("planning")).findByPlaceholderText("Type a model name");
    fireEvent.change(input, { target: { value: "gemini-2.5-pro" } });
    await user.click(screen.getByRole("button", { name: /save overrides/i }));
    expect(setProject).toHaveBeenCalledWith("p1", { planning: { host: "gemini", model: "gemini-2.5-pro" } });
  });

  it("refuses a host with no model and does not call the API", async () => {
    await open();
    fireEvent.change(hostSelect("planning"), { target: { value: "gemini" } });
    await user.click(screen.getByRole("button", { name: /save overrides/i }));
    expect(await within(row("planning")).findByRole("alert")).toHaveTextContent("Pick a model");
    expect(setProject).not.toHaveBeenCalled();
  });

  it("shows an existing override and goes back to global by sending null", async () => {
    overrides = { coding: { host: "deepseek", model: "deepseek-chat" } };
    await open();
    expect(hostSelect("coding")).toHaveValue("deepseek");
    expect(within(row("coding")).getByText("Overridden")).toBeInTheDocument();
    fireEvent.change(hostSelect("coding"), { target: { value: "" } });
    await user.click(screen.getByRole("button", { name: /save overrides/i }));
    expect(setProject).toHaveBeenCalledWith("p1", { coding: null });
    await waitFor(() => expect(within(row("coding")).queryByText("Overridden")).not.toBeInTheDocument());
  });

  it("has nothing to save until something changes, and can reset every role at once", async () => {
    overrides = { coding: { host: "deepseek", model: "deepseek-chat" }, vision: { host: "groq", model: "qwen-chat" } };
    await open();
    expect(screen.getByRole("button", { name: /save overrides/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /use global for all/i }));
    expect(hostSelect("coding")).toHaveValue("");
    expect(hostSelect("vision")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: /save overrides/i }));
    expect(setProject).toHaveBeenCalledWith("p1", { coding: null, vision: null });
  });

  it('"Same As Chat" copies the Chat role\'s host and model, and the per-card reset button clears an override', async () => {
    await open();
    await user.click(within(row("planning")).getByRole("button", { name: "Same As Chat" }));
    expect(hostSelect("planning")).toHaveValue("groq");
    await waitFor(() => expect(within(row("planning")).getByRole("combobox", { name: "Model" })).toHaveTextContent("qwen-chat"));
    expect(within(row("planning")).getByText("Unsaved")).toBeInTheDocument();
    await user.click(within(row("planning")).getByRole("button", { name: "Use global" }));
    expect(hostSelect("planning")).toHaveValue("");
  });

  it("shows the server's reason when saving fails", async () => {
    setProject.mockRejectedValueOnce(new Error("Unknown host"));
    await open();
    fireEvent.change(hostSelect("images"), { target: { value: "comfyui" } });
    fireEvent.change(await within(row("images")).findByPlaceholderText("Checkpoint name"), { target: { value: "sdxl" } });
    await user.click(screen.getByRole("button", { name: /save overrides/i }));
    expect(await screen.findByText(/Unknown host/)).toBeInTheDocument();
  });
});
