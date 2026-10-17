import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import type { KeyInfo, KeySource } from "../../lib/types";
import { IntegrationsPage } from "../Integrations";
import { renderPage, user } from "./helpers";

/** The fake backend's key state: what the UI is allowed to know, never a key. */
const server: { brave: KeySource } = { brave: "none" };

const keyList = (): KeyInfo[] => [
  { id: "lmstudio", title: "LM Studio", subtitle: "Optional: only needed when the LM Studio server is set to require an API key.", group: "host", configured: false, source: "none", signup: "" },
  { id: "brave", title: "Brave Search", subtitle: "Free-tier web hits.", group: "service", configured: server.brave !== "none", source: server.brave, signup: "https://brave.com/search/api/" },
];

beforeEach(() => {
  server.brave = "none";
  vi.spyOn(api.mcp, "servers").mockResolvedValue({
    servers: [
      { id: "blender", label: "Blender MCP", icon: "🧊", description: "", enabled: true, transport: "stdio", target: "", status: "disconnected", ready: false, bridge: "unknown", app_label: "Blender", error: "Blender is not running.", error_kind: "", note: "", setup_hint: "", bound_to: ["pixel"], bound_cats: [{ id: "pixel", name: "Pixel" }], tools_total: 0, tools_exposed: 0, tools_on_demand: 0, tool_names: [], connected_at: null, checked_at: null, next_retry_in: null },
      { id: "unity", label: "Unity MCP", icon: "🎮", description: "", enabled: true, transport: "streamable-http", target: "", status: "connected", ready: true, bridge: "ok", app_label: "Unity", error: "", error_kind: "", note: "", setup_hint: "", bound_to: ["pulse"], bound_cats: [{ id: "pulse", name: "Pulse" }], tools_total: 12, tools_exposed: 12, tools_on_demand: 0, tool_names: [], connected_at: null, checked_at: null, next_retry_in: null },
    ],
    problems: [],
    sdk_installed: true,
    running: true,
    enabled: true,
  });
  vi.spyOn(api.keys, "list").mockImplementation(async () => keyList());
});
afterEach(() => {
  vi.restoreAllMocks();
});

async function ready() {
  renderPage(<IntegrationsPage />);
  await screen.findByRole("region", { name: "MCP Integrations" });
}

const services = () => screen.getByRole("region", { name: "Other services" });

describe("MCP Tools & Integrations page", () => {
  it("has an MCP Integrations card: Blender for Pixel, Unity for Pulse", async () => {
    await ready();
    const card = screen.getByRole("region", { name: "MCP Integrations" });
    await waitFor(() => expect(within(card).getByTestId("mcp-badge-unity")).toHaveTextContent("Connected"));
    expect(within(card).getByTestId("mcp-badge-blender")).toHaveTextContent("Offline");
    expect(within(card).getByText(/Bound to 🎨 Pixel/)).toBeInTheDocument();
    expect(within(card).getByText(/Bound to 💻 Pulse/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /Refresh \/ Reconnect MCP/ })).toBeInTheDocument();
  });

  it("lists the optional services and saves their keys", async () => {
    const set = vi.spyOn(api.keys, "set").mockImplementation(async (id) => {
      server.brave = "saved";
      return keyList().find((k) => k.id === id)!;
    });
    await ready();
    const rows = within(services()).getAllByRole("listitem");
    expect(rows).toHaveLength(1); // web search only
    expect(within(rows[0]).getByText("Brave Search")).toBeInTheDocument();
    expect(within(rows[0]).queryByText("No key")).toBeNull(); // optional: no warning

    await user.click(within(rows[0]).getByRole("button", { name: "Set Brave Search key" }));
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: "brave-key-1" } });
    await user.click(screen.getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(set).toHaveBeenCalledWith("brave", "brave-key-1"));
    await waitFor(() => expect(within(services()).getAllByText("Key saved")).toHaveLength(1));
  });

  it("still shows the MCP panel when there are no service keys to list", async () => {
    vi.spyOn(api.keys, "list").mockResolvedValue([]);
    await ready();
    expect(screen.queryByRole("region", { name: "Other services" })).toBeNull();
  });
});
