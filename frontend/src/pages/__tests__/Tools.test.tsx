import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../lib/api";
import type { AvailableTool, Tool } from "../../lib/types";
import { ToolsPage } from "../Tools";
import { AGENTS, agent, seedStore, tool } from "./matrixFixtures";

const WRITE = tool("t_write", "write_file", { handler: "backend.tool_functions.pulse_tools:write_file", description: "Write a file in the workspace", agent_ids: ["pulse"] });
const AUDIT = tool("t_audit", "http_endpoint_audit", { handler: "backend.tool_functions.aegis_tools:http_endpoint_audit", description: "Hit an endpoint" });

function renderPage() {
  return render(
    <MemoryRouter>
      <ToolsPage />
    </MemoryRouter>,
  );
}
const mockList = (list: Tool[] = [WRITE, AUDIT]) => vi.spyOn(api.tools, "list").mockResolvedValue(structuredClone(list));
const rowOf = (name: string) => screen.getByRole("button", { name: `Open ${name}` }).closest("tr") as HTMLElement;
const has = (toolName: string, agentName: string) => screen.queryByRole("img", { name: `${agentName} has ${toolName}` });

const AVAILABLE: AvailableTool[] = [
  { handler: "backend.tool_functions.pulse_tools:write_file", name: "write_file", description: "Write a file", registered: true },
  { handler: "backend.tool_functions.aegis_tools:run_test_suite", name: "run_test_suite", description: "Run the project's tests", registered: false },
  { handler: "backend.tool_functions.forge_tools:vps_health_check", name: "vps_health_check", description: "Check host health", registered: false },
];

beforeEach(() => {
  seedStore();
  vi.spyOn(api.agents, "list").mockResolvedValue(AGENTS);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ToolsPage", () => {
  it("never shows a Cat Crew column, whichever id the orchestrator has in the store", async () => {
    seedStore([...AGENTS, agent("crew", { name: "Cat Crew", role: "room" }), agent("chief", { name: "Chief", role: "chief" })]);
    mockList();
    renderPage();
    await screen.findByRole("button", { name: "Open write_file" });
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent ?? "");
    expect(headers.some((h) => /cat crew|chief/i.test(h))).toBe(false);
    expect(screen.queryByRole("img", { name: /^(Cat Crew|Chief) has/ })).toBeNull();
  });

  it("renders tool rows with handler paths and shows which cats have each one, read-only", async () => {
    mockList();
    renderPage();
    expect(await screen.findByRole("button", { name: "Open write_file" })).toBeInTheDocument();
    expect(screen.getByText("backend.tool_functions.pulse_tools:write_file")).toBeInTheDocument();
    expect(screen.getByText("Write a file in the workspace")).toBeInTheDocument();
    expect(screen.getByText("Showing 2 of 2 tools")).toBeInTheDocument();

    const headers = screen.getAllByRole("columnheader").map((h) => h.querySelector("span.truncate")?.textContent ?? h.textContent?.trim());
    expect(headers.slice(0, 6)).toEqual(["Tool", "Global", "Pulse", "Aegis", "Pixel", "Scout"]);
    expect(has("write_file", "Pulse")).toBeInTheDocument();
    expect(has("write_file", "Aegis")).toBeNull();
    // Nothing to toggle: the bundles are built in.
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("a global tool shows on every cat", async () => {
    mockList([WRITE, { ...AUDIT, is_global: true }]);
    renderPage();
    await screen.findByRole("button", { name: "Open http_endpoint_audit" });
    expect(screen.getByRole("img", { name: "http_endpoint_audit is global" })).toBeInTheDocument();
    for (const name of ["Pulse", "Aegis", "Pixel", "Scout"]) expect(has("http_endpoint_audit", name)).toBeInTheDocument();
    expect(within(rowOf("http_endpoint_audit")).getAllByTitle("Global: every cat has it")).toHaveLength(5);
    expect(has("write_file", "Pixel")).toBeNull();
  });

  it("searches by name, handler and description", async () => {
    mockList();
    renderPage();
    await screen.findByRole("button", { name: "Open write_file" });
    const box = screen.getByRole("searchbox", { name: "Search tools" });

    fireEvent.change(box, { target: { value: "aegis_tools" } });
    expect(screen.queryByRole("button", { name: "Open write_file" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open http_endpoint_audit" })).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 2 tools")).toBeInTheDocument();

    fireEvent.change(box, { target: { value: "workspace" } });
    expect(screen.getByRole("button", { name: "Open write_file" })).toBeInTheDocument();

    fireEvent.change(box, { target: { value: "nope" } });
    expect(screen.getByText("No tools match")).toBeInTheDocument();
  });

  it("shows an empty state and an error state", async () => {
    const list = vi.spyOn(api.tools, "list").mockRejectedValueOnce(new ApiError(500, "db locked"));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("db locked");
    list.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No tools registered")).toBeInTheDocument();
  });

  describe("drawer", () => {
    it("shows the handler, description and pretty-printed JSON schema", async () => {
      mockList();
      const schema = { type: "function", function: { name: "write_file", parameters: { type: "object", properties: { path: { type: "string" } } } } };
      const get = vi.spyOn(api.tools, "get").mockResolvedValue({ ...WRITE, schema });
      renderPage();
      await screen.findByRole("button", { name: "Open write_file" });
      fireEvent.click(rowOf("write_file"));

      const drawer = await screen.findByRole("dialog", { name: "write_file" });
      await waitFor(() => expect(get).toHaveBeenCalledWith("t_write"));
      const pre = await within(drawer).findByLabelText("JSON schema");
      expect(pre.textContent).toBe(JSON.stringify(schema, null, 2));
      expect(pre.textContent).toContain('\n  "function": {');
      expect(within(drawer).getByText("backend.tool_functions.pulse_tools:write_file")).toBeInTheDocument();
      expect(within(drawer).getByText("Write a file in the workspace")).toBeInTheDocument();
    });

    it("delete asks for confirmation, then removes the tool and reloads", async () => {
      const list = mockList();
      vi.spyOn(api.tools, "get").mockResolvedValue({ ...AUDIT, schema: {} });
      const remove = vi.spyOn(api.tools, "remove").mockResolvedValue(undefined);
      renderPage();
      await screen.findByRole("button", { name: "Open http_endpoint_audit" });
      fireEvent.click(rowOf("http_endpoint_audit"));
      const drawer = await screen.findByRole("dialog", { name: "http_endpoint_audit" });

      fireEvent.click(within(drawer).getByRole("button", { name: "Delete tool" }));
      expect(remove).not.toHaveBeenCalled();
      list.mockResolvedValue([WRITE]);
      fireEvent.click(within(drawer).getByRole("button", { name: "Confirm delete" }));
      await waitFor(() => expect(remove).toHaveBeenCalledWith("t_audit"));
      await waitFor(() => expect(screen.queryByRole("button", { name: "Open http_endpoint_audit" })).not.toBeInTheDocument());
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  describe("Register tool", () => {
    it("lists only unregistered functions, filters them, and registers one", async () => {
      const list = mockList([WRITE]);
      vi.spyOn(api.tools, "available").mockResolvedValue(structuredClone(AVAILABLE));
      const register = vi.spyOn(api.tools, "register").mockResolvedValue(tool("t_new", "run_test_suite"));
      renderPage();
      await screen.findByRole("button", { name: "Open write_file" });

      fireEvent.click(screen.getByRole("button", { name: "Register tool" }));
      const dialog = await screen.findByRole("dialog", { name: "Register tool" });
      expect(await within(dialog).findByText("run_test_suite")).toBeInTheDocument();
      expect(within(dialog).getByText("vps_health_check")).toBeInTheDocument();
      expect(within(dialog).queryByText("write_file")).not.toBeInTheDocument(); // already registered

      fireEvent.change(within(dialog).getByRole("searchbox", { name: "Search available tools" }), { target: { value: "health" } });
      expect(within(dialog).queryByText("run_test_suite")).not.toBeInTheDocument();
      fireEvent.change(within(dialog).getByRole("searchbox", { name: "Search available tools" }), { target: { value: "" } });

      list.mockResolvedValue([WRITE, tool("t_new", "run_test_suite")]);
      fireEvent.click(within(dialog).getByRole("button", { name: "Register run_test_suite" }));
      await waitFor(() => expect(register).toHaveBeenCalledWith("backend.tool_functions.aegis_tools:run_test_suite"));
      // it leaves the modal list and shows up in the matrix behind it
      await waitFor(() => expect(within(dialog).queryByText("run_test_suite")).not.toBeInTheDocument());
      expect(await screen.findByRole("button", { name: "Open run_test_suite" })).toBeInTheDocument();
      expect(screen.getByText("Registered run_test_suite.")).toBeInTheDocument();
    });

    it("shows the backend's 422 message inline and keeps the row", async () => {
      mockList([WRITE]);
      vi.spyOn(api.tools, "available").mockResolvedValue(structuredClone(AVAILABLE));
      vi.spyOn(api.tools, "register").mockRejectedValue(new ApiError(422, "Cannot import aegis_tools: No module named 'playwright'"));
      renderPage();
      await screen.findByRole("button", { name: "Open write_file" });
      fireEvent.click(screen.getByRole("button", { name: "Register tool" }));
      const dialog = await screen.findByRole("dialog", { name: "Register tool" });
      fireEvent.click(await within(dialog).findByRole("button", { name: "Register run_test_suite" }));
      expect(await within(dialog).findByRole("alert")).toHaveTextContent("No module named 'playwright'");
      expect(within(dialog).getByText("run_test_suite")).toBeInTheDocument();
    });

    it("says so when everything is already registered", async () => {
      mockList([WRITE]);
      vi.spyOn(api.tools, "available").mockResolvedValue([{ ...AVAILABLE[0] }]);
      renderPage();
      await screen.findByRole("button", { name: "Open write_file" });
      fireEvent.click(screen.getByRole("button", { name: "Register tool" }));
      expect(await screen.findByText("Everything is registered")).toBeInTheDocument();
    });
  });

  it("Import from URL calls the backend, reloads and shows its message", async () => {
    const list = mockList([WRITE]);
    const imp = vi.spyOn(api.tools, "importFromUrl").mockResolvedValue({ message: "Registered 1 tool", tools: [AUDIT] });
    renderPage();
    await screen.findByRole("button", { name: "Open write_file" });
    fireEvent.click(screen.getByRole("button", { name: "Import from URL" }));
    const dialog = await screen.findByRole("dialog", { name: "Import tool from URL" });
    fireEvent.change(within(dialog).getByLabelText("URL"), { target: { value: "https://example.com/t.py" } });
    list.mockResolvedValue([WRITE, AUDIT]);
    fireEvent.click(within(dialog).getByRole("button", { name: "Import" }));
    await waitFor(() => expect(imp).toHaveBeenCalledWith("https://example.com/t.py"));
    expect(await screen.findByText("Registered 1 tool")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Open http_endpoint_audit" })).toBeInTheDocument();
  });
});

describe("ToolsPage bundle tools", () => {
  it("marks a tool from a cat's bundle as built in", async () => {
    const PATCH = tool("t_patch", "write_file_patch", { agent_ids: ["pulse"], locked_agent_ids: ["pulse"] });
    mockList([PATCH, AUDIT]);
    renderPage();
    const mark = await screen.findByRole("img", { name: "Pulse has write_file_patch" });
    expect(mark).toHaveAttribute("title", "Built into this cat's bundle");
    expect(has("write_file_patch", "Aegis")).toBeNull();
  });

  it("does not offer to delete a bundle tool", async () => {
    const PATCH = tool("t_patch", "write_file_patch", { agent_ids: ["pulse"], locked_agent_ids: ["pulse"] });
    mockList([PATCH]);
    vi.spyOn(api.tools, "get").mockResolvedValue(PATCH);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Open write_file_patch" }));
    expect(await screen.findByText(/In the bundle of Pulse: it cannot be deleted/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete tool/ })).toBeNull();
  });
});
