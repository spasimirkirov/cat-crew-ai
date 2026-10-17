import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import type { BlockedModel, HostModels, ModelsState } from "../../lib/types";
import { user } from "../../pages/__tests__/helpers";
import { BlockedModelCard } from "../room/BlockedModelCard";

const FREE = "google/gemma-4-26b-a4b-it:free";

const issue: BlockedModel = {
  host: "openrouter",
  model: FREE,
  kind: "rate_limited",
  status: 429,
  message: "rate-limited upstream",
  free: true,
  at: 1,
  roles: [
    { role: "chat", label: "Chat", source: "global" },
    { role: "coding", label: "Coding", source: "project" },
  ],
};

const model = (name: string) => ({ name, display_name: name, loaded: false, capabilities: ["chat", "tools"], context_length: 0 });

const state = (): ModelsState =>
  ({
    roles: {},
    hosts: [
      { id: "openrouter", label: "OpenRouter", enabled: true, cloud: true, configured: true, key_source: "saved" },
      { id: "deepseek", label: "DeepSeek", enabled: true, cloud: true, configured: true, key_source: "environment" },
      { id: "groq", label: "Groq", enabled: false, cloud: true, configured: false, key_source: "none" },
    ],
  }) as unknown as ModelsState;

const lists: Record<string, HostModels> = {
  openrouter: { host: "openrouter", online: true, error: "", error_kind: "", checked_at: 1, models: [model(FREE), model("deepseek/deepseek-v4-pro")] },
  deepseek: { host: "deepseek", online: true, error: "", error_kind: "", checked_at: 1, models: [model("deepseek-chat")] },
};

afterEach(() => vi.restoreAllMocks());

describe("BlockedModelCard", () => {
  it("names the model and the roles on it, and offers only hosts with a key", async () => {
    vi.spyOn(api.models, "get").mockResolvedValue(state());
    vi.spyOn(api.models, "hostModels").mockImplementation(async (h) => lists[h]);
    render(<BlockedModelCard projectId="p1" issue={issue} />);
    const card = await screen.findByTestId("blocked-model");
    expect(card).toHaveTextContent(`${FREE} is rate-limited`);
    expect(card).toHaveTextContent("It is a free model");
    expect(card).toHaveTextContent("Used by: Chat, Coding (this project).");
    const host = await within(card).findByLabelText("Switch to host");
    expect(Array.from((host as HTMLSelectElement).options).map((o) => o.value)).toEqual(["openrouter", "deepseek"]);
    expect(screen.getByRole("button", { name: /switch everywhere/i })).toBeDisabled(); // no model picked yet
  });

  it("switches every role to the picked model, then retries the message", async () => {
    vi.spyOn(api.models, "get").mockResolvedValue(state());
    vi.spyOn(api.models, "hostModels").mockImplementation(async (h) => lists[h]);
    const replace = vi.spyOn(api.models, "replace").mockResolvedValue({ ...state(), changed: ["chat (global)"] } as never);
    const retry = vi.spyOn(api.chat, "retry").mockResolvedValue({ turn_id: "t2", queued: false });
    render(<BlockedModelCard projectId="p1" issue={issue} />);
    await user.click(await screen.findByRole("combobox", { name: "Model" }));
    expect(screen.queryByRole("option", { name: new RegExp(FREE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) })).toBeNull(); // not the blocked one
    await user.click(screen.getByRole("option", { name: /deepseek-v4-pro/ }));
    await user.click(screen.getByRole("button", { name: /switch everywhere/i }));
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({ from_host: "openrouter", from_model: FREE, to_host: "openrouter", to_model: "deepseek/deepseek-v4-pro" }),
    );
    await waitFor(() => expect(retry).toHaveBeenCalledWith("p1"));
  });

  it("can be dismissed", async () => {
    vi.spyOn(api.models, "get").mockResolvedValue(state());
    vi.spyOn(api.models, "hostModels").mockImplementation(async (h) => lists[h]);
    render(<BlockedModelCard projectId="p1" issue={issue} />);
    await user.click(await screen.findByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("blocked-model")).toBeNull();
  });
});
