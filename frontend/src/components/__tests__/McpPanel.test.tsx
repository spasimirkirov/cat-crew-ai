import { render, screen, waitFor, within } from "@testing-library/react";
import { user as userEvent } from "../../pages/__tests__/helpers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../lib/api";
import type { McpPayload, McpServer } from "../../lib/types";
import { badgeOf, McpPanel, pollDelay } from "../models/McpPanel";

const server = (over: Partial<McpServer> & { id: string }): McpServer => ({
  label: over.id === "unity" ? "Unity MCP" : "Blender MCP",
  icon: over.id === "unity" ? "🎮" : "🧊",
  description: "",
  enabled: true,
  transport: over.id === "unity" ? "streamable-http" : "stdio",
  target: "",
  status: "connected",
  ready: true,
  bridge: "ok",
  app_label: over.id === "unity" ? "Unity" : "Blender",
  error: "",
  error_kind: "",
  note: "",
  setup_hint: "",
  bound_to: [over.id === "unity" ? "pulse" : "pixel"],
  bound_cats: [over.id === "unity" ? { id: "pulse", name: "Pulse" } : { id: "pixel", name: "Pixel" }],
  tools_total: 30,
  tools_exposed: 24,
  tools_on_demand: 6,
  tool_names: [],
  connected_at: null,
  checked_at: null,
  next_retry_in: null,
  ...over,
});

const payload = (servers: McpServer[], over: Partial<McpPayload> = {}): McpPayload => ({ servers, problems: [], sdk_installed: true, running: true, enabled: true, ...over });

const both = () =>
  payload([
    server({ id: "blender", status: "disconnected", ready: false, error: "Blender is not running.", setup_hint: "Run install_addon.py", tools_total: 0, tools_exposed: 0, tools_on_demand: 0 }),
    server({ id: "unity" }),
  ]);

beforeEach(() => {
  vi.spyOn(api.mcp, "servers").mockImplementation(async () => both());
});
afterEach(() => vi.restoreAllMocks());

describe("McpPanel", () => {
  it("shows Blender offline (bound to Pixel) and Unity connected (bound to Pulse)", async () => {
    render(<McpPanel />);
    await screen.findByText("Unity MCP");
    expect(screen.getByRole("region", { name: "MCP Integrations" })).toBeInTheDocument();
    expect(screen.getByTestId("mcp-badge-blender")).toHaveTextContent("Offline");
    expect(screen.getByTestId("mcp-badge-unity")).toHaveTextContent("Connected");
    expect(screen.getByText(/Bound to 🎨 Pixel/)).toBeInTheDocument();
    expect(screen.getByText(/Bound to 💻 Pulse/)).toBeInTheDocument();
    expect(screen.getByText(/24 tools · 6 on demand/)).toBeInTheDocument();
    expect(screen.getByText("Blender is not running.")).toBeInTheDocument();
    expect(screen.getByText("Run install_addon.py")).toBeInTheDocument();
  });

  it("says the server is up but the program is not answering", () => {
    const s = server({ id: "unity", ready: false, bridge: "down", note: "The MCP server is running but Unity does not answer." });
    expect(badgeOf(s).text).toBe("Server up · Unity not answering");
  });

  it("maps every status to one badge", () => {
    expect(badgeOf(server({ id: "blender", status: "connecting", ready: false })).text).toBe("Connecting…");
    expect(badgeOf(server({ id: "blender", status: "reconnecting", ready: false })).text).toBe("Reconnecting…");
    expect(badgeOf(server({ id: "blender", status: "disabled", ready: false })).text).toBe("Disabled");
    expect(badgeOf(server({ id: "blender", enabled: false })).text).toBe("Disabled");
  });

  it("Refresh / Reconnect MCP calls the backend and shows the new state", async () => {
    const refresh = vi.spyOn(api.mcp, "refresh").mockResolvedValue(payload([server({ id: "blender" }), server({ id: "unity" })]));
    render(<McpPanel />);
    await screen.findByText("Unity MCP");
    await userEvent.click(screen.getByRole("button", { name: /Refresh \/ Reconnect MCP/ }));
    expect(refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId("mcp-badge-blender")).toHaveTextContent("Connected"));
  });

  it("reconnects one server from its own button", async () => {
    const reconnect = vi.spyOn(api.mcp, "reconnect").mockResolvedValue({ ...payload([server({ id: "blender" }), server({ id: "unity" })]), server: server({ id: "blender" }) });
    render(<McpPanel />);
    await screen.findByText("Unity MCP");
    await userEvent.click(screen.getByRole("button", { name: "Reconnect Blender MCP" }));
    expect(reconnect).toHaveBeenCalledWith("blender");
    await waitFor(() => expect(screen.getByTestId("mcp-badge-blender")).toHaveTextContent("Connected"));
  });

  it("shows a failed refresh without losing the list", async () => {
    vi.spyOn(api.mcp, "refresh").mockRejectedValue(new ApiError(500, "boom"));
    render(<McpPanel />);
    await screen.findByText("Unity MCP");
    await userEvent.click(screen.getByRole("button", { name: /Refresh \/ Reconnect MCP/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByText("Unity MCP")).toBeInTheDocument();
  });

  it("survives a backend that cannot be reached", async () => {
    vi.spyOn(api.mcp, "servers").mockRejectedValue(new ApiError(0, "Cannot reach the CatCrewAI backend. Is it running?"));
    render(<McpPanel />);
    expect(await screen.findByText(/Cannot reach/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(); // the poll retries by itself, so it does not shout
  });

  it("explains a switched-off subsystem and a missing SDK", async () => {
    vi.spyOn(api.mcp, "servers").mockResolvedValue(payload([server({ id: "blender", status: "disabled", ready: false })], { enabled: false }));
    const { unmount } = render(<McpPanel />);
    expect(await screen.findByText(/switched off \(CATCREW_MCP=0\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Refresh \/ Reconnect MCP/ })).toBeDisabled();
    unmount();
    vi.spyOn(api.mcp, "servers").mockResolvedValue(payload([server({ id: "blender", status: "disconnected", ready: false })], { sdk_installed: false }));
    render(<McpPanel />);
    expect(await screen.findByText(/pip install -r requirements.txt/)).toBeInTheDocument();
  });

  it("polls fast while something connects and slowly when settled", () => {
    expect(pollDelay(null)).toBe(8000);
    expect(pollDelay(payload([server({ id: "blender", status: "reconnecting", ready: false })]))).toBe(3000);
    expect(pollDelay(payload([server({ id: "blender", status: "disconnected", ready: false })]))).toBe(8000);
    expect(pollDelay(payload([server({ id: "blender" }), server({ id: "unity" })]))).toBe(20000);
  });

  it("stops polling when it unmounts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { unmount } = render(<McpPanel />);
      await screen.findByText("Unity MCP");
      const calls = vi.mocked(api.mcp.servers).mock.calls.length;
      unmount();
      await vi.advanceTimersByTimeAsync(60000);
      expect(vi.mocked(api.mcp.servers).mock.calls.length).toBe(calls);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("badge text stays inside its row", () => {
  it("lists one row per server", async () => {
    render(<McpPanel />);
    await screen.findByText("Unity MCP");
    const rows = within(screen.getByRole("region", { name: "MCP Integrations" })).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
  });
});
