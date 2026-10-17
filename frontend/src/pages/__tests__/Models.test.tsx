import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { api, ApiError } from "../../lib/api";
import type { HostModels, KeyInfo, KeySource, ModelHealth, ModelsState } from "../../lib/types";
import { useModelHealth } from "../../state/modelHealth";
import { ModelsPage } from "../Models";
import { renderPage, user } from "./helpers";

/** The fake backend's key state: what the UI is allowed to know, never a key. */
const server: { gemini: KeySource; brave: KeySource } = { gemini: "none", brave: "none" };

const keyList = (): KeyInfo[] => [
  { id: "lmstudio", title: "LM Studio", subtitle: "Optional: only needed when the LM Studio server is set to require an API key.", group: "host", configured: false, source: "none", signup: "" },
  { id: "deepseek", title: "DeepSeek", subtitle: "Very cheap chat models.", group: "host", configured: true, source: "saved", signup: "https://platform.deepseek.com/api_keys" },
  { id: "gemini", title: "Google AI Studio", subtitle: "Free Gemini models.", group: "host", configured: server.gemini !== "none", source: server.gemini, signup: "https://aistudio.google.com/apikey" },
  { id: "brave", title: "Brave Search", subtitle: "Free-tier web hits.", group: "service", configured: server.brave !== "none", source: server.brave, signup: "https://brave.com/search/api/" },
];

const state = (): ModelsState => ({
  roles: {
    chat: { host: "lmstudio", model: "demo-model" },
    coding: { host: "lmstudio", model: "demo-model" },
    memory: { host: "", model: "" },
    vision: { host: "", model: "" },
    planning: { host: "", model: "" },
    review: { host: "", model: "" },
    images: { host: "comfyui", model: "" },
  },
  hosts: [
    { id: "lmstudio", label: "LM Studio", enabled: true, cloud: false },
    { id: "deepseek", label: "DeepSeek", enabled: true, cloud: true, configured: true },
    { id: "gemini", label: "Google Gemini", enabled: server.gemini !== "none", cloud: true, configured: server.gemini !== "none", key_source: server.gemini },
  ],
});

const model = (name: string, capabilities: string[], loaded = false) => ({ name, display_name: name, loaded, capabilities, context_length: 0 });

const probeData: Record<string, HostModels> = {
  lmstudio: {
    host: "lmstudio",
    online: true,
    error: "",
    error_kind: "",
    checked_at: 1,
    models: [model("demo-model", ["tools", "chat"], true), model("big-model", ["vision", "tools", "chat"]), model("think-model", ["reasoning", "tools", "chat"])],
  },
  deepseek: { host: "deepseek", online: false, error: "unreachable", error_kind: "unreachable", checked_at: 1, models: [] },
  gemini: { host: "gemini", online: false, error: "unreachable", error_kind: "unreachable", checked_at: 1, models: [] },
};

const healthy: ModelHealth = { checked_at: 1, ok: true, hosts: {}, issues: [] };
let healthReport: ModelHealth = healthy;

let setRoles: MockInstance<typeof api.models.setRoles>;

beforeEach(() => {
  server.gemini = "none";
  server.brave = "none";
  healthReport = healthy;
  useModelHealth.setState({ report: null, noticeOpen: false, started: false });
  vi.spyOn(api.models, "health").mockImplementation(async () => healthReport);
  vi.spyOn(api.models, "refreshHost").mockImplementation(async (h) => probeData[h]);
  vi.spyOn(api.keys, "list").mockImplementation(async () => keyList());
  vi.spyOn(api.models, "get").mockImplementation(async () => state());
  vi.spyOn(api.models, "hostModels").mockImplementation(async (h) => probeData[h]);
  vi.spyOn(api.jev, "getSettings").mockResolvedValue({
    enabled: false,
    backend: "openrouter",
    guard_tool_calls: false,
    skip_tools_precheck: false,
    model: "jev-latest",
    confidence_threshold: 0.92,
    api_key_configured: false,
    key_source: "none",
  });
  vi.spyOn(api.openrouter, "getSettings").mockResolvedValue({
    provider_sort: "",
    allow_fallbacks: true,
    compression_enabled: false,
    free_tier_enabled: false,
    free_tier_model: "meta-llama/llama-3.3-70b-instruct:free",
  });
  setRoles = vi.spyOn(api.models, "setRoles").mockImplementation(async (b) => {
    const next = state();
    Object.assign(next.roles, b);
    return next;
  });
});
afterEach(() => {
  vi.restoreAllMocks();
});

const roleCard = (name: string) => screen.getByRole("region", { name });
const modelBox = (card: HTMLElement) => within(card).getByRole("combobox", { name: "Model" });

/** Open a card's model dropdown and choose an option, like a user would. */
async function pick(card: HTMLElement, name: string | RegExp) {
  await user.click(modelBox(card));
  await user.click(within(card).getByRole("option", { name }));
}

async function ready() {
  renderPage(<ModelsPage />);
  await screen.findByRole("region", { name: "Chat" });
  // wait for the model lists to arrive
  await waitFor(() => expect(modelBox(roleCard("Chat"))).toHaveTextContent("demo-model"));
}

describe("Models page", () => {
  it("says these are the global defaults that a project's settings can override", async () => {
    await ready();
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("🌐 Global Default Settings");
    expect(note).toHaveTextContent("These model selections apply across all crew operations unless specifically overridden in an individual Project's Settings.");
  });

  it("shows a role card per brain with the saved host and model", async () => {
    await ready();
    for (const name of ["Chat", "Coding", "Memory", "Vision", "Planning", "Images"]) expect(roleCard(name)).toBeInTheDocument();
    expect(within(roleCard("Chat")).getByLabelText("Host")).toHaveValue("lmstudio");
    expect(modelBox(roleCard("Coding"))).toHaveTextContent("demo-model");
    expect(within(roleCard("Memory")).getByLabelText("Host")).toHaveValue("");
    expect(within(roleCard("Memory")).getByLabelText("Model")).toBeDisabled();
  });

  it("shows colour-coded capability badges beside every model, in the list and on the selection", async () => {
    await ready();
    const chat = roleCard("Chat");
    // the selected model carries its badges on the closed dropdown
    const closed = modelBox(chat);
    const caps = (el: HTMLElement) => Array.from(el.querySelectorAll("[data-cap]")).map((b) => b.getAttribute("data-cap"));
    expect(within(closed).queryByText("Loaded")).toBeNull(); // a LM Studio leftover: every cloud model "was loaded"
    expect(caps(closed)).toEqual(["tools"]);

    await user.click(closed);
    const options = within(within(chat).getByRole("listbox", { name: "Models" })).getAllByRole("option");
    expect(options).toHaveLength(3);
    expect(caps(options[0])).toEqual(["tools"]);
    expect(caps(options[1])).toEqual(["vision", "tools"]);
    expect(caps(options[2])).toEqual(["reasoning", "tools"]);
    // icons only: the capability names are for screen readers and the tooltip, not visible text
    const visionBadge = options[1].querySelector('[data-cap="vision"]')!;
    expect(visionBadge).toHaveAttribute("title", "Accepts images");
    expect(visionBadge.querySelector(".sr-only")).toHaveTextContent("Vision");
    expect(within(options[0]).queryByText("chat")).toBeNull(); // every model chats: not a badge
  });

  it("picks a model with the keyboard (arrows, Enter, Escape)", async () => {
    await ready();
    const box = modelBox(roleCard("Coding"));
    const key = (k: string) => fireEvent.keyDown(box, { key: k });
    box.focus();
    key("ArrowDown"); // opens on the current model
    expect(box).toHaveAttribute("aria-expanded", "true");
    key("ArrowDown");
    key("Enter");
    expect(box).toHaveAttribute("aria-expanded", "false");
    expect(box).toHaveTextContent("big-model");
    key("ArrowDown");
    key("ArrowDown");
    key("Escape");
    expect(box).toHaveAttribute("aria-expanded", "false");
    expect(box).toHaveTextContent("big-model"); // Escape does not change the choice
  });

  it("offers Not set (same as Chat) on the optional roles and Vision and Planning slots", async () => {
    await ready();
    for (const name of ["Coding", "Memory", "Vision", "Planning"]) {
      expect(within(within(roleCard(name)).getByLabelText("Host")).getByRole("option", { name: "Not set (same as Chat)" })).toBeInTheDocument();
    }
    expect(within(within(roleCard("Chat")).getByLabelText("Host")).getByRole("option", { name: "Not set" })).toBeInTheDocument();
    expect(within(roleCard("Vision")).getByLabelText("Model")).toHaveAttribute("placeholder", "Follows the Chat model");
  });

  it("each role lists only the models that can do its job", async () => {
    await ready();
    const listed = async (name: string) => {
      const card = roleCard(name);
      await user.click(within(card).getByRole("combobox", { name: "Model" }));
      const names = within(within(card).getByRole("listbox", { name: "Models" }))
        .getAllByRole("option")
        .map((o) => o.querySelector(".truncate")?.textContent);
      return { card, names };
    };
    fireEvent.change(within(roleCard("Vision")).getByLabelText("Host"), { target: { value: "lmstudio" } });
    await waitFor(() => expect(within(roleCard("Vision")).getByRole("combobox", { name: "Model" })).toBeEnabled());
    const vision = await listed("Vision");
    expect(vision.names).toEqual(["big-model"]); // vision models only
    expect(within(vision.card).getByTestId("required-cap")).toHaveTextContent("Vision models only");
    expect(within(vision.card).queryByRole("checkbox", { name: "Vision" })).toBeNull(); // no point filtering what is required
    await user.click(within(vision.card).getByRole("option", { name: /big-model/ }));

    fireEvent.change(within(roleCard("Planning")).getByLabelText("Host"), { target: { value: "lmstudio" } });
    await waitFor(() => expect(within(roleCard("Planning")).getByRole("combobox", { name: "Model" })).toBeEnabled());
    const planning = await listed("Planning");
    expect(planning.names).toEqual(["think-model"]); // reasoning models only
    await user.click(within(planning.card).getByRole("option", { name: /think-model/ }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(setRoles).toHaveBeenCalledWith({
        vision: { host: "lmstudio", model: "big-model" },
        planning: { host: "lmstudio", model: "think-model" },
      }),
    );
  });

  describe("unavailable models", () => {
    const issue = {
      role: "coding",
      role_label: "Coding",
      host: "lmstudio",
      host_label: "LM Studio",
      model: "demo-model",
      reason: "missing" as const,
      message: "The model 'demo-model' assigned to 'Coding' is no longer available on LM Studio.",
    };

    it("highlights the invalid card with the reason and clears it once another model is chosen", async () => {
      healthReport = { checked_at: 1, ok: false, hosts: {}, issues: [issue] };
      await ready();
      const coding = roleCard("Coding");
      await waitFor(() => expect(coding).toHaveAttribute("data-invalid", "true"));
      expect(within(coding).getByRole("alert")).toHaveTextContent("no longer available");
      expect(within(coding).getByText("Needs a new model")).toBeInTheDocument();
      expect(modelBox(coding)).toHaveAttribute("aria-invalid", "true");
      expect(roleCard("Chat")).not.toHaveAttribute("data-invalid");

      await pick(coding, /^big-model/);
      expect(coding).not.toHaveAttribute("data-invalid");
      expect(modelBox(coding)).not.toHaveAttribute("aria-invalid");
    });

    it("looks again after a save, so a fixed card stays fixed", async () => {
      healthReport = { checked_at: 1, ok: false, hosts: {}, issues: [issue] };
      await ready();
      await waitFor(() => expect(roleCard("Coding")).toHaveAttribute("data-invalid", "true"));
      await pick(roleCard("Coding"), /^big-model/);
      healthReport = healthy;
      await user.click(within(roleCard("Coding")).getByRole("button", { name: "Save" }));
      await waitFor(() => expect(vi.mocked(api.models.health).mock.calls.length).toBeGreaterThan(1));
      expect(vi.mocked(api.models.health)).toHaveBeenLastCalledWith(true);
      expect(roleCard("Coding")).not.toHaveAttribute("data-invalid");
    });
  });

  it("every role has an (i) that explains what it does and what kind of model suits it", async () => {
    await ready();
    for (const name of ["Chat", "Coding", "Memory", "Vision", "Planning", "Review", "Images"]) {
      const info = within(roleCard(name)).getByRole("button", { name: `About the ${name} role` });
      const tip = document.getElementById(info.getAttribute("aria-describedby") ?? "");
      expect(tip).toHaveAttribute("role", "tooltip");
      expect(tip).toHaveTextContent("What it does");
      expect(tip).toHaveTextContent("What kind of model");
    }
  });

  it("the (i) tooltip lives outside the card, so no sidebar or card edge can clip it, and opens on hover", async () => {
    await ready();
    const info = within(roleCard("Chat")).getByRole("button", { name: "About the Chat role" });
    const tip = document.getElementById(info.getAttribute("aria-describedby") ?? "")!;
    expect(tip.parentElement).toBe(document.body);
    expect(roleCard("Chat").contains(tip)).toBe(false);
    expect(tip).toHaveAttribute("data-open", "false");
    fireEvent.mouseEnter(info);
    expect(tip).toHaveAttribute("data-open", "true");
    expect(tip).toHaveClass("fixed");
    fireEvent.mouseLeave(info);
    expect(tip).toHaveAttribute("data-open", "false");
  });

  it("Review follows Coding when it is not set", async () => {
    await ready();
    const review = roleCard("Review");
    expect(within(within(review).getByLabelText("Host")).getByRole("option", { name: "Not set (same as Coding)" })).toBeInTheDocument();
    expect(within(review).getByLabelText("Model")).toHaveAttribute("placeholder", "Follows the Coding model");
  });

  describe("hybrid strategy guide", () => {
    it("is collapsed until opened and lists the recommendations and the 8,000-token rule", async () => {
      await ready();
      const toggle = screen.getByRole("button", { name: /Recommended Hybrid Strategy & Token Optimization/ });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByText("Chat / Dispatcher")).toBeNull();
      await user.click(toggle);
      expect(toggle).toHaveAttribute("aria-expanded", "true");
      for (const role of ["Chat / Dispatcher", "Coding", "Memory / Summarization", "Planning", "Review", "Vision"]) {
        expect(within(screen.getByRole("region", { name: /Hybrid Strategy/ })).getAllByText(role).length).toBeGreaterThan(0);
      }
      expect(screen.getByText(/8,000 tokens/)).toBeInTheDocument();
      await user.click(toggle);
      expect(screen.queryByText("Chat / Dispatcher")).toBeNull();
    });
  });

  it("disables cloud hosts without a key and says why", async () => {
    await ready();
    const host = within(roleCard("Chat")).getByLabelText("Host");
    const gemini = within(host).getByRole("option", { name: "Google Gemini (no API key)" });
    expect(gemini).toBeDisabled();
    expect(within(host).getByRole("option", { name: "DeepSeek" })).toBeEnabled();
  });

  it("send only the changed roles when saving all changes", async () => {
    await ready();
    const save = screen.getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();

    await pick(roleCard("Coding"), /^big-model/);
    expect(within(roleCard("Coding")).getByText("Unsaved")).toBeInTheDocument();
    expect(within(roleCard("Chat")).queryByText("Unsaved")).toBeNull();
    expect(save).toBeEnabled();
    await user.click(save);

    await waitFor(() => expect(setRoles).toHaveBeenCalledTimes(1));
    expect(setRoles).toHaveBeenCalledWith({ coding: { host: "lmstudio", model: "big-model" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled());
    expect(within(roleCard("Coding")).queryByText("Unsaved")).toBeNull();
  });

  it("saves one card on its own and leaves other unsaved edits pending", async () => {
    await ready();
    await pick(roleCard("Coding"), /^big-model/);
    await pick(roleCard("Chat"), /^big-model/);
    await user.click(within(roleCard("Chat")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setRoles).toHaveBeenCalledTimes(1));
    expect(setRoles).toHaveBeenCalledWith({ chat: { host: "lmstudio", model: "big-model" } });
    await waitFor(() => expect(within(roleCard("Chat")).queryByText("Unsaved")).toBeNull());
    expect(within(roleCard("Coding")).getByText("Unsaved")).toBeInTheDocument();
  });

  it("changing the host clears the model and sends the new pair", async () => {
    await ready();
    fireEvent.change(within(roleCard("Memory")).getByLabelText("Host"), { target: { value: "lmstudio" } });
    await waitFor(() => expect(within(roleCard("Memory")).getByRole("combobox", { name: "Model" })).toBeEnabled());
    await pick(roleCard("Memory"), /^big-model/);
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(setRoles).toHaveBeenCalledWith({ memory: { host: "lmstudio", model: "big-model" } }));
  });

  it("shows a muted 'Host offline' note and still lets the user type a model", async () => {
    await ready();
    const chat = roleCard("Chat");
    fireEvent.change(within(chat).getByLabelText("Host"), { target: { value: "deepseek" } });
    const note = await within(chat).findByText(/Host offline/);
    expect(note).toHaveTextContent("unreachable");
    const input = within(chat).getByLabelText("Model");
    expect(input.tagName).toBe("INPUT");
    fireEvent.change(input, { target: { value: "deepseek-chat" } });
    await user.click(within(chat).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setRoles).toHaveBeenCalledWith({ chat: { host: "deepseek", model: "deepseek-chat" } }));
  });

  it("images use ComfyUI with a typed model name and never probe it", async () => {
    await ready();
    const images = roleCard("Images");
    expect(within(images).getByLabelText("Host")).toHaveValue("comfyui");
    fireEvent.change(within(images).getByLabelText("Model"), { target: { value: "sdxl.safetensors" } });
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(setRoles).toHaveBeenCalledWith({ images: { host: "comfyui", model: "sdxl.safetensors" } }));
    expect(api.models.hostModels).not.toHaveBeenCalledWith("comfyui");
  });

  it("images can be switched Off", async () => {
    await ready();
    const images = roleCard("Images");
    const host = within(images).getByLabelText("Host");
    expect(Array.from((host as HTMLSelectElement).options).map((o) => o.value)).toEqual(["off", "comfyui"]);
    fireEvent.change(host, { target: { value: "off" } });
    expect(within(images).getByLabelText("Model")).toBeDisabled();
    expect(within(images).getByPlaceholderText("Image generation is off")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(setRoles).toHaveBeenCalledWith({ images: { host: "off", model: "" } }));
  });

  it("vision can be switched Off for a chat model that cannot see", async () => {
    await ready();
    const vision = roleCard("Vision");
    const host = within(vision).getByLabelText("Host");
    expect(within(host).getByRole("option", { name: "Off" })).toBeInTheDocument();
    expect(within(within(roleCard("Coding")).getByLabelText("Host")).queryByRole("option", { name: "Off" })).toBeNull();
    fireEvent.change(host, { target: { value: "off" } });
    expect(within(vision).getByLabelText("Model")).toBeDisabled();
    expect(within(vision).getByPlaceholderText(/Vision is off/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(setRoles).toHaveBeenCalledWith({ vision: { host: "off", model: "" } }));
  });

  it('offers OpenRouter as a second Images host, with its own image-model list, when it is configured', async () => {
    vi.spyOn(api.models, "get").mockImplementation(async () => {
      const s = state();
      s.hosts = [...s.hosts, { id: "openrouter", label: "OpenRouter", enabled: true, cloud: true, configured: true }];
      return s;
    });
    const hostModels = vi.spyOn(api.models, "hostModels").mockImplementation(async (h, opts) =>
      h === "openrouter" && opts?.image
        ? { host: "openrouter", online: true, error: "", error_kind: "", checked_at: 1, models: [model("google/gemini-2.5-flash-image", ["image"])] }
        : probeData[h],
    );
    await ready();
    const images = roleCard("Images");
    const hostSelect = within(images).getByLabelText("Host") as HTMLSelectElement;
    expect(Array.from(hostSelect.options).map((o) => o.value)).toEqual(["off", "comfyui", "openrouter"]);
    fireEvent.change(hostSelect, { target: { value: "openrouter" } });
    await waitFor(() => expect(hostModels).toHaveBeenCalledWith("openrouter", { image: true }));
    await pick(images, /gemini-2.5-flash-image/);
    await user.click(within(images).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setRoles).toHaveBeenCalledWith({ images: { host: "openrouter", model: "google/gemini-2.5-flash-image" } }));
  });

  it('"Same As Chat" copies the Chat role\'s host and model into another card, but not onto Chat itself', async () => {
    await ready();
    expect(within(roleCard("Chat")).queryByRole("button", { name: "Same As Chat" })).toBeNull();
    const memory = roleCard("Memory");
    expect(within(memory).getByLabelText("Host")).toHaveValue("");
    await user.click(within(memory).getByRole("button", { name: "Same As Chat" }));
    expect(within(memory).getByLabelText("Host")).toHaveValue("lmstudio");
    await waitFor(() => expect(modelBox(memory)).toHaveTextContent("demo-model"));
    expect(within(memory).getByText("Unsaved")).toBeInTheDocument();
  });

  it("shows a save error from the backend on the card", async () => {
    await ready();
    setRoles.mockRejectedValueOnce(new ApiError(422, "Unknown host 'nope'."));
    await pick(roleCard("Coding"), /^big-model/);
    await user.click(within(roleCard("Coding")).getByRole("button", { name: "Save" }));
    expect(await within(roleCard("Coding")).findByRole("alert")).toHaveTextContent("Unknown host 'nope'.");
    expect(within(roleCard("Coding")).getByText("Unsaved")).toBeInTheDocument();
  });

  describe("hosts panel", () => {
    it("lists every host with enabled and key badges", async () => {
      await ready();
      const panel = screen.getByRole("region", { name: "Hosts" });
      const rows = within(panel).getAllByRole("listitem");
      expect(rows).toHaveLength(3);
      expect(within(rows[0]).getByText("LM Studio")).toBeInTheDocument();
      expect(within(rows[0]).getByText("Enabled")).toBeInTheDocument();
      expect(within(rows[0]).queryByText("No key")).toBeNull(); // a local host does not need one
      expect(within(rows[0]).queryByText("Key saved")).toBeNull();
      expect(within(rows[1]).getByText("Key saved")).toBeInTheDocument();
      expect(within(rows[2]).getByText("Disabled")).toBeInTheDocument();
      expect(within(rows[2]).getByText("No key")).toBeInTheDocument();
      expect(within(panel).getByText(/never shown again/)).toBeInTheDocument();
    });

    it("asks the provider for its live list when Refresh is pressed", async () => {
      await ready();
      const calls = () => vi.mocked(api.models.hostModels).mock.calls.filter((c) => c[0] === "lmstudio").length;
      expect(calls()).toBe(1); // probed once (cached list), shared by the cards
      expect(api.models.refreshHost).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "Refresh LM Studio" }));
      await waitFor(() => expect(api.models.refreshHost).toHaveBeenCalledWith("lmstudio"));
      await waitFor(() => expect(modelBox(roleCard("Chat"))).toHaveTextContent("demo-model"));
    });

    it("reports probe results without inventing detail", async () => {
      await ready();
      const panel = screen.getByRole("region", { name: "Hosts" });
      await waitFor(() => expect(within(panel).getByText("Online, 3 models")).toBeInTheDocument());
      expect(within(panel).getByText("Offline")).toBeInTheDocument();
      expect(within(panel).getByText("Not checked")).toBeInTheDocument(); // gemini: not enabled, no key, so never probed
    });
  });

  describe("API keys", () => {
    const hosts = () => screen.getByRole("region", { name: "Hosts" });
    const KEY = "AIza-my-secret-key-0123";

    it("offers Set key on hosts without one and Change key on the others", async () => {
      await ready();
      expect(within(hosts()).getByRole("button", { name: "Set Google Gemini key" })).toBeInTheDocument();
      expect(within(hosts()).getByRole("button", { name: "Change DeepSeek key" })).toBeInTheDocument();
      expect(within(hosts()).getByRole("button", { name: "Set LM Studio key" })).toBeInTheDocument();
    });

    it("saves a pasted key through a password field, then shows the host as ready and never displays the key", async () => {
      const set = vi.spyOn(api.keys, "set").mockImplementation(async (id) => {
        server.gemini = "saved";
        return keyList().find((k) => k.id === id)!;
      });
      const { container } = renderPage(<ModelsPage />);
      await screen.findByRole("region", { name: "Hosts" });

      await user.click(within(hosts()).getByRole("button", { name: "Set Google Gemini key" }));
      const dialog = screen.getByRole("dialog", { name: "Google Gemini API key" });
      const input = within(dialog).getByLabelText("API key");
      expect(input).toHaveAttribute("type", "password");
      expect(input).toHaveAttribute("autocomplete", "off");
      expect(within(dialog).getByRole("button", { name: "Save key" })).toBeDisabled(); // nothing pasted yet
      expect(dialog).toHaveTextContent("https://aistudio.google.com/apikey");

      fireEvent.change(input, { target: { value: `  ${KEY}  ` } });
      await user.click(within(dialog).getByRole("button", { name: "Save key" }));

      await waitFor(() => expect(set).toHaveBeenCalledWith("gemini", KEY)); // trimmed
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      const row = within(hosts()).getByText("Google Gemini").closest("li")!;
      await waitFor(() => expect(within(row).getByText("Key saved")).toBeInTheDocument());
      expect(within(row).getByText("Enabled")).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: "Change Google Gemini key" })).toBeInTheDocument();
      expect(container.textContent).not.toContain(KEY);
      expect(container.innerHTML).not.toContain(KEY);
      // the new host is probed right away
      await waitFor(() => expect(api.models.refreshHost).toHaveBeenCalledWith("gemini"));
    });

    it("submits with Enter and can reveal the key while typing", async () => {
      const set = vi.spyOn(api.keys, "set").mockImplementation(async (id) => keyList().find((k) => k.id === id)!);
      await ready();
      await user.click(within(hosts()).getByRole("button", { name: "Set Google Gemini key" }));
      const input = screen.getByLabelText("API key");
      fireEvent.change(input, { target: { value: KEY } });
      await user.click(screen.getByRole("button", { name: "Show key" }));
      expect(input).toHaveAttribute("type", "text");
      await user.click(screen.getByRole("button", { name: "Hide key" }));
      expect(input).toHaveAttribute("type", "password");
      fireEvent.submit(input.closest("form")!);
      await waitFor(() => expect(set).toHaveBeenCalledWith("gemini", KEY));
    });

    it("keeps the dialog open and shows the reason when the backend rejects the key", async () => {
      vi.spyOn(api.keys, "set").mockRejectedValue(new ApiError(422, "A key has no spaces or line breaks. Paste only the key itself, without a Bearer prefix."));
      await ready();
      await user.click(within(hosts()).getByRole("button", { name: "Set Google Gemini key" }));
      fireEvent.change(screen.getByLabelText("API key"), { target: { value: "Bearer abc" } });
      await user.click(screen.getByRole("button", { name: "Save key" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Paste only the key itself");
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(screen.getByLabelText("API key")).toHaveValue("Bearer abc");
      expect(screen.getByRole("button", { name: "Save key" })).toBeEnabled();
    });

    it("cancel closes the dialog without calling the backend", async () => {
      const set = vi.spyOn(api.keys, "set");
      await ready();
      await user.click(within(hosts()).getByRole("button", { name: "Set Google Gemini key" }));
      fireEvent.change(screen.getByLabelText("API key"), { target: { value: KEY } });
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(set).not.toHaveBeenCalled();
      // reopening starts empty: the key did not linger
      await user.click(within(hosts()).getByRole("button", { name: "Set Google Gemini key" }));
      expect(screen.getByLabelText("API key")).toHaveValue("");
    });

    it("removes a saved key only after a second click", async () => {
      server.gemini = "saved";
      const clear = vi.spyOn(api.keys, "clear").mockImplementation(async (id) => {
        server.gemini = "none";
        return keyList().find((k) => k.id === id)!;
      });
      await ready();
      await user.click(within(hosts()).getByRole("button", { name: "Change Google Gemini key" }));
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByLabelText("New key")).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Remove saved key" }));
      expect(clear).not.toHaveBeenCalled();
      await user.click(within(dialog).getByRole("button", { name: "Click again to remove" }));
      await waitFor(() => expect(clear).toHaveBeenCalledWith("gemini"));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      const row = within(hosts()).getByText("Google Gemini").closest("li")!;
      await waitFor(() => expect(within(row).getByText("No key")).toBeInTheDocument());
      expect(within(row).getByText("Not checked")).toBeInTheDocument();
    });

    it("says when the key in use comes from an environment variable and has nothing to remove", async () => {
      server.gemini = "environment";
      await ready();
      const row = within(hosts()).getByText("Google Gemini").closest("li")!;
      expect(within(row).getByText("Env key")).toBeInTheDocument();
      await user.click(within(row).getByRole("button", { name: "Change Google Gemini key" }));
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent("environment variable");
      expect(within(dialog).queryByRole("button", { name: /remove/i })).toBeNull();
    });

    it("keeps unsaved role edits when a key is saved", async () => {
      vi.spyOn(api.keys, "set").mockImplementation(async (id) => {
        server.gemini = "saved";
        return keyList().find((k) => k.id === id)!;
      });
      await ready();
      await pick(roleCard("Coding"), /^big-model/);
      expect(within(roleCard("Coding")).getByText("Unsaved")).toBeInTheDocument();

      await user.click(within(hosts()).getByRole("button", { name: "Set Google Gemini key" }));
      fireEvent.change(screen.getByLabelText("API key"), { target: { value: KEY } });
      await user.click(screen.getByRole("button", { name: "Save key" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      const row = within(hosts()).getByText("Google Gemini").closest("li")!;
      await waitFor(() => expect(within(row).getByText("Key saved")).toBeInTheDocument());
      expect(modelBox(roleCard("Coding"))).toHaveTextContent("big-model");
      expect(within(roleCard("Coding")).getByText("Unsaved")).toBeInTheDocument();
    });

    it("still works when the key details cannot be loaded", async () => {
      vi.mocked(api.keys.list).mockRejectedValue(new ApiError(0, "Cannot reach the CatCrewAI backend. Is it running?"));
      await ready();
      expect(within(hosts()).getByRole("button", { name: "Set Google Gemini key" })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Other services" })).toBeNull();
    });
  });

  it("never renders a secret, even if the payload carried one", async () => {
    const leaky = state();
    (leaky.hosts[1] as unknown as Record<string, unknown>).api_key = "sk-SECRET-123";
    (leaky.hosts[1] as unknown as Record<string, unknown>).token = "tok-SECRET-456";
    vi.mocked(api.models.get).mockImplementation(async () => leaky);
    const { container } = renderPage(<ModelsPage />);
    await screen.findByRole("region", { name: "Hosts" });
    await waitFor(() => expect(screen.getByText("Key saved")).toBeInTheDocument());
    expect(container.textContent).not.toMatch(/SECRET/);
    expect(container.innerHTML).not.toMatch(/SECRET/);
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });

  it("offers a retry when the models cannot be loaded", async () => {
    vi.mocked(api.models.get).mockRejectedValueOnce(new ApiError(0, "Cannot reach the CatCrewAI backend. Is it running?"));
    renderPage(<ModelsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the CatCrewAI backend");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Chat" })).toBeInTheDocument();
  });
});
