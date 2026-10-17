import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../lib/api";
import type { Skill } from "../../lib/types";
import { useStore } from "../../state/store";
import { SkillsPage } from "../Skills";
import { AGENTS, agent, seedStore, skill } from "./matrixFixtures";

const BUNDLED = skill("s_bundled", "static-web", { type: "bundled", description: "Websites on this PC", agent_ids: ["pulse"] });
const CUSTOM = skill("s_custom", "release-notes", { type: "custom", description: "Write changelogs" });
const MARKET = skill("s_market", "seo-audit", { type: "marketplace", description: "Audit landing pages" });
const AUTO = skill("s_auto", "log-triage", { type: "auto_generated", description: "Read logs first" });

function renderPage() {
  return render(
    <MemoryRouter>
      <SkillsPage />
    </MemoryRouter>,
  );
}

function mockList(list: Skill[] = [BUNDLED, CUSTOM, MARKET, AUTO]) {
  return vi.spyOn(api.skills, "list").mockResolvedValue(structuredClone(list));
}

const rowOf = (name: string) => screen.getByRole("button", { name: `Open ${name}` }).closest("tr") as HTMLElement;
const cell = (skillName: string, agentName: string) => screen.getByRole("checkbox", { name: `Assign ${skillName} to ${agentName}` });

beforeEach(() => {
  seedStore();
  vi.spyOn(api.skills, "proposals").mockResolvedValue([]);
  vi.spyOn(api.agents, "list").mockResolvedValue(AGENTS);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SkillsPage", () => {
  it("never shows a Cat Crew column, whichever id the orchestrator has in the store", async () => {
    // Regression: "Could not update … Unknown agent 'chief'". Cat Crew is the orchestrator, not
    // an agent: skills belong to specialist cats only.
    seedStore([...AGENTS, agent("crew", { name: "Cat Crew", role: "room" }), agent("chief", { name: "Chief", role: "chief" })]);
    const assign = vi.spyOn(api.skills, "assign").mockResolvedValue({ ...BUNDLED, agent_ids: ["pulse", "pixel"] });
    mockList();
    renderPage();
    await screen.findByRole("button", { name: "Open static-web" });
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent ?? "");
    expect(headers.some((h) => /cat crew|chief/i.test(h))).toBe(false);
    expect(screen.queryByRole("checkbox", { name: /to Cat Crew|to Chief/ })).toBeNull();
    fireEvent.click(cell("static-web", "Pixel"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("s_bundled", ["pulse", "pixel"]));
    expect(assign.mock.calls[0][1]).not.toContain("chief");
    expect(assign.mock.calls[0][1]).not.toContain("crew");
  });

  it("renders skill rows and agent columns, core cats first", async () => {
    mockList();
    renderPage();
    expect(await screen.findByRole("button", { name: "Open static-web" })).toBeInTheDocument();
    expect(screen.getByText("Playbooks that teach the cats how to work")).toBeInTheDocument();
    expect(screen.getByText("Showing 4 of 4 skills")).toBeInTheDocument();

    const headers = screen.getAllByRole("columnheader").map((h) => h.querySelector("span.truncate")?.textContent ?? h.textContent?.trim());
    expect(headers.slice(0, 6)).toEqual(["Skill", "Global", "Pulse", "Aegis", "Pixel", "Scout"]);

    // pre-assigned cell is on, others off
    expect(cell("static-web", "Pulse")).toHaveAttribute("aria-checked", "true");
    expect(cell("static-web", "Aegis")).toHaveAttribute("aria-checked", "false");
    // type chips
    const row = within(rowOf("seo-audit"));
    expect(row.getByText("marketplace")).toBeInTheDocument();
    expect(within(rowOf("log-triage")).getByText("auto")).toBeInTheDocument();
  });

  it("asks the backend for agents when the store has none", async () => {
    seedStore([]);
    mockList();
    renderPage();
    expect(await screen.findByRole("checkbox", { name: "Assign static-web to Pulse" })).toBeInTheDocument();
    expect(api.agents.list).toHaveBeenCalled();
  });

  it("toggling a cell sends the FULL agent list and keeps the new state", async () => {
    mockList();
    const assign = vi.spyOn(api.skills, "assign").mockResolvedValue({ ...BUNDLED, agent_ids: ["pulse", "aegis"] });
    renderPage();
    await screen.findByRole("button", { name: "Open static-web" });

    fireEvent.click(cell("static-web", "Aegis"));
    expect(cell("static-web", "Aegis")).toHaveAttribute("aria-checked", "true"); // optimistic
    await waitFor(() => expect(assign).toHaveBeenCalledWith("s_bundled", ["pulse", "aegis"]));
    await waitFor(() => expect(cell("static-web", "Aegis")).not.toBeDisabled());
    expect(cell("static-web", "Aegis")).toHaveAttribute("aria-checked", "true");

    // un-assign sends the list without that cat
    assign.mockResolvedValue({ ...BUNDLED, agent_ids: ["aegis"] });
    fireEvent.click(cell("static-web", "Pulse"));
    await waitFor(() => expect(assign).toHaveBeenLastCalledWith("s_bundled", ["aegis"]));
  });

  it("rolls back and shows an inline error when assign fails", async () => {
    mockList();
    vi.spyOn(api.skills, "assign").mockRejectedValue(new ApiError(500, "disk is full"));
    renderPage();
    await screen.findByRole("button", { name: "Open static-web" });

    fireEvent.click(cell("static-web", "Aegis"));
    expect(cell("static-web", "Aegis")).toHaveAttribute("aria-checked", "true");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not update static-web: disk is full");
    expect(cell("static-web", "Aegis")).toHaveAttribute("aria-checked", "false");
    expect(cell("static-web", "Pulse")).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("button", { name: "Dismiss error" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("the Global toggle locks the row's agent cells", async () => {
    mockList();
    const setGlobal = vi.spyOn(api.skills, "setGlobal").mockResolvedValue({ ...CUSTOM, is_global: true });
    renderPage();
    await screen.findByRole("button", { name: "Open release-notes" });
    expect(cell("release-notes", "Pulse")).not.toBeDisabled();

    fireEvent.click(screen.getByRole("switch", { name: "Make release-notes global" }));
    await waitFor(() => expect(setGlobal).toHaveBeenCalledWith("s_custom", true));

    for (const name of ["Pulse", "Aegis", "Pixel", "Scout"]) {
      await waitFor(() => expect(cell("release-notes", name)).toBeDisabled());
      expect(cell("release-notes", name)).toHaveAttribute("aria-checked", "true");
    }
    expect(within(rowOf("release-notes")).getAllByTitle("Global: attached to every cat")).toHaveLength(4);
    // other rows stay editable
    expect(cell("seo-audit", "Pulse")).not.toBeDisabled();
  });

  it("filters by type and by search text", async () => {
    mockList();
    renderPage();
    await screen.findByRole("button", { name: "Open static-web" });

    expect(screen.getByRole("button", { name: /^Bundled/ })).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: /^Marketplace/ }));
    expect(screen.getByRole("button", { name: "Open seo-audit" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open static-web" })).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 4 skills")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^All/ }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search skills" }), { target: { value: "changelog" } });
    expect(screen.getByRole("button", { name: "Open release-notes" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open seo-audit" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search skills" }), { target: { value: "zzzz" } });
    expect(screen.getByText("No skills match")).toBeInTheDocument();
  });

  it("shows loading, error with retry, and empty states", async () => {
    const list = vi.spyOn(api.skills, "list").mockRejectedValueOnce(new ApiError(0, "Cannot reach the CatCrewAI backend. Is it running?"));
    renderPage();
    expect(screen.getByText("Loading skills")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the CatCrewAI backend");

    list.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No skills yet")).toBeInTheDocument();
  });

  describe("Hermes proposals", () => {
    const PROPOSAL = skill("p1", "npm-release-flow", { type: "auto_generated", description: "Bump, build, tag, publish", agent_ids: ["pulse"] });
    const OTHER = skill("p2", "screenshot-triage", { type: "auto_generated" });

    it("approve asks for an agent, then approves and removes the card", async () => {
      mockList();
      const proposals = vi.spyOn(api.skills, "proposals").mockResolvedValue([PROPOSAL, OTHER]);
      // like the real backend: once approved, the proposal is no longer listed
      const approve = vi.spyOn(api.skills, "approve").mockImplementation(async () => {
        proposals.mockResolvedValue([OTHER]);
        return { ...PROPOSAL, agent_ids: ["aegis"] };
      });
      renderPage();
      const panel = await screen.findByRole("region", { name: "Proposed skills" });
      expect(within(panel).getByText("npm-release-flow")).toBeInTheDocument();
      expect(within(panel).getByText("Bump, build, tag, publish")).toBeInTheDocument();

      fireEvent.click(within(panel).getAllByRole("button", { name: "Approve" })[0]);
      const select = within(panel).getByRole("combobox", { name: "Assign npm-release-flow to" });
      expect(select).toHaveValue("pulse"); // defaults to the discovering cat
      fireEvent.change(select, { target: { value: "aegis" } });
      fireEvent.click(within(panel).getByRole("button", { name: "Assign and approve" }));

      await waitFor(() => expect(approve).toHaveBeenCalledWith("p1", "aegis"));
      await waitFor(() => expect(within(panel).queryByText("npm-release-flow")).not.toBeInTheDocument());
      expect(within(panel).getByText("screenshot-triage")).toBeInTheDocument();
    });

    it("dismiss calls the backend and removes the card; a failure stays inline", async () => {
      mockList();
      const proposals = vi.spyOn(api.skills, "proposals").mockResolvedValue([PROPOSAL]);
      const dismiss = vi
        .spyOn(api.skills, "dismiss")
        .mockRejectedValueOnce(new ApiError(500, "boom"))
        .mockImplementation(async () => {
          proposals.mockResolvedValue([]);
        });
      renderPage();
      const panel = await screen.findByRole("region", { name: "Proposed skills" });

      fireEvent.click(within(panel).getByRole("button", { name: "Dismiss" }));
      expect(await within(panel).findByRole("alert")).toHaveTextContent("boom");
      expect(within(panel).getByText("npm-release-flow")).toBeInTheDocument();

      fireEvent.click(within(panel).getByRole("button", { name: "Dismiss" }));
      await waitFor(() => expect(dismiss).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(screen.queryByRole("region", { name: "Proposed skills" })).not.toBeInTheDocument());
    });

    it("refetches when the store's proposals change (skill.proposed event)", async () => {
      mockList();
      const proposals = vi.spyOn(api.skills, "proposals").mockResolvedValue([]);
      renderPage();
      await screen.findByRole("button", { name: "Open static-web" });
      expect(screen.queryByRole("region", { name: "Proposed skills" })).not.toBeInTheDocument();
      const before = proposals.mock.calls.length;

      proposals.mockResolvedValue([PROPOSAL]);
      act(() => useStore.getState().applyEvent({ v: 1, seq: 9, ts: "", type: "skill.proposed", project_id: null, data: { skill: PROPOSAL, agent_id: "pulse", task_id: "t1" } }));
      expect(await screen.findByRole("region", { name: "Proposed skills" })).toBeInTheDocument();
      expect(proposals.mock.calls.length).toBeGreaterThan(before);
    });
  });

  describe("dialogs", () => {
    it("New skill: name is required, submit calls create and the list reloads", async () => {
      const list = mockList([CUSTOM]);
      const created = skill("s_new", "deploy-checklist", { description: "Ship it" });
      const create = vi.spyOn(api.skills, "create").mockResolvedValue(created);
      renderPage();
      await screen.findByRole("button", { name: "Open release-notes" });

      fireEvent.click(screen.getByRole("button", { name: "New skill" }));
      const dialog = await screen.findByRole("dialog", { name: "New skill" });
      const submit = within(dialog).getByRole("button", { name: "Create skill" });
      expect(submit).toBeDisabled();

      fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "  deploy-checklist " } });
      fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "Ship it" } });
      fireEvent.change(within(dialog).getByLabelText("Playbook (Markdown)"), { target: { value: "# Steps\n1. go" } });
      expect(submit).toBeEnabled();
      list.mockResolvedValue([CUSTOM, created]);
      fireEvent.click(submit);

      await waitFor(() => expect(create).toHaveBeenCalledWith({ name: "deploy-checklist", description: "Ship it", body: "# Steps\n1. go" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(await screen.findByRole("button", { name: "Open deploy-checklist" })).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("Created deploy-checklist.");
    });

    it("New skill shows the backend's error inline and stays open", async () => {
      mockList([CUSTOM]);
      vi.spyOn(api.skills, "create").mockRejectedValue(new ApiError(409, "A skill with that name already exists"));
      renderPage();
      await screen.findByRole("button", { name: "Open release-notes" });
      fireEvent.click(screen.getByRole("button", { name: "New skill" }));
      const dialog = await screen.findByRole("dialog", { name: "New skill" });
      fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "release-notes" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Create skill" }));
      expect(await within(dialog).findByRole("alert")).toHaveTextContent("already exists");
    });

    it("Import from URL calls the backend, reloads and shows the returned message", async () => {
      const list = mockList([CUSTOM]);
      const imp = vi.spyOn(api.skills, "importFromUrl").mockResolvedValue({ message: "Imported 1 skill: seo-audit", skills: [MARKET] });
      renderPage();
      await screen.findByRole("button", { name: "Open release-notes" });

      fireEvent.click(screen.getByRole("button", { name: "Import from URL" }));
      const dialog = await screen.findByRole("dialog", { name: "Import skill from URL" });
      const submit = within(dialog).getByRole("button", { name: "Import" });
      expect(submit).toBeDisabled();
      fireEvent.change(within(dialog).getByLabelText("URL"), { target: { value: "https://example.com/SKILL.md" } });
      list.mockResolvedValue([CUSTOM, MARKET]);
      fireEvent.click(submit);

      await waitFor(() => expect(imp).toHaveBeenCalledWith("https://example.com/SKILL.md"));
      expect(await screen.findByText("Imported 1 skill: seo-audit")).toBeInTheDocument();
      expect(await screen.findByRole("button", { name: "Open seo-audit" })).toBeInTheDocument();
    });
  });

  describe("drawer", () => {
    it("opens on row click with the playbook details", async () => {
      mockList();
      const get = vi.spyOn(api.skills, "get").mockResolvedValue({
        ...MARKET,
        body: "# SEO audit\n\nCheck titles.",
        source_url: "https://example.com/seo/SKILL.md",
        target_roles: ["marketing"],
        version: "2.3.0",
      });
      renderPage();
      await screen.findByRole("button", { name: "Open seo-audit" });
      fireEvent.click(rowOf("seo-audit"));

      const drawer = await screen.findByRole("dialog", { name: "seo-audit" });
      await waitFor(() => expect(get).toHaveBeenCalledWith("s_market"));
      expect(await within(drawer).findByLabelText("Playbook markdown")).toHaveTextContent("Check titles.");
      expect(within(drawer).getByText("marketing")).toBeInTheDocument();
      expect(within(drawer).getByText("https://example.com/seo/SKILL.md")).toBeInTheDocument();
      expect(within(drawer).getByText("backend/skills/seo-audit/SKILL.md")).toBeInTheDocument();
      expect(within(drawer).getByText("v2.3.0")).toBeInTheDocument();

      fireEvent.keyDown(window, { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("delete is disabled (with an explanation) for bundled skills", async () => {
      mockList();
      vi.spyOn(api.skills, "get").mockResolvedValue({ ...BUNDLED, body: "x" });
      const remove = vi.spyOn(api.skills, "remove").mockResolvedValue(undefined);
      renderPage();
      await screen.findByRole("button", { name: "Open static-web" });
      fireEvent.click(rowOf("static-web"));
      const drawer = await screen.findByRole("dialog", { name: "static-web" });
      const del = within(drawer).getByRole("button", { name: "Delete skill" });
      expect(del).toBeDisabled();
      expect(del.parentElement).toHaveAttribute("title", expect.stringMatching(/cannot be deleted/i));
      fireEvent.click(del);
      expect(remove).not.toHaveBeenCalled();
    });

    it("deleting a custom skill asks for confirmation first", async () => {
      const list = mockList();
      vi.spyOn(api.skills, "get").mockResolvedValue({ ...CUSTOM, body: "x" });
      const remove = vi.spyOn(api.skills, "remove").mockResolvedValue(undefined);
      renderPage();
      await screen.findByRole("button", { name: "Open release-notes" });
      fireEvent.click(rowOf("release-notes"));
      const drawer = await screen.findByRole("dialog", { name: "release-notes" });

      fireEvent.click(within(drawer).getByRole("button", { name: "Delete skill" }));
      expect(remove).not.toHaveBeenCalled();
      expect(within(drawer).getByText(/Delete "release-notes" for good\?/)).toBeInTheDocument();
      fireEvent.click(within(drawer).getByRole("button", { name: "Cancel" }));
      expect(remove).not.toHaveBeenCalled();

      fireEvent.click(within(drawer).getByRole("button", { name: "Delete skill" }));
      list.mockResolvedValue([BUNDLED, MARKET, AUTO]);
      fireEvent.click(within(drawer).getByRole("button", { name: "Confirm delete" }));
      await waitFor(() => expect(remove).toHaveBeenCalledWith("s_custom"));
      await waitFor(() => expect(screen.queryByRole("button", { name: "Open release-notes" })).not.toBeInTheDocument());
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("clicking a toggle does not open the drawer", async () => {
      mockList();
      vi.spyOn(api.skills, "assign").mockResolvedValue({ ...CUSTOM, agent_ids: ["pulse"] });
      renderPage();
      await screen.findByRole("button", { name: "Open release-notes" });
      fireEvent.click(cell("release-notes", "Pulse"));
      await waitFor(() => expect(api.skills.assign).toHaveBeenCalled());
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("includes non-core cats as extra columns", async () => {
    seedStore([agent("pulse"), agent("scout", { is_system: false })]);
    mockList([CUSTOM]);
    renderPage();
    expect(await screen.findByRole("checkbox", { name: "Assign release-notes to Scout" })).toBeInTheDocument();
  });
});

describe("SkillsPage essential playbooks", () => {
  it("shows an essential playbook as locked for its cat, while other cats still toggle", async () => {
    const WEB = skill("s_web", "static-web", { type: "bundled", agent_ids: ["pulse"], locked_agent_ids: ["pulse"] });
    mockList([WEB]);
    const assign = vi.spyOn(api.skills, "assign").mockResolvedValue({ ...WEB, agent_ids: ["pulse", "aegis"] });
    renderPage();

    const locked = await screen.findByRole("checkbox", { name: "static-web is essential for Pulse and always on" });
    expect(locked).toBeDisabled();
    expect(locked).toHaveAttribute("aria-checked", "true");
    fireEvent.click(locked);
    expect(assign).not.toHaveBeenCalled();

    fireEvent.click(cell("static-web", "Aegis"));
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect([...assign.mock.calls[0][1]].sort()).toEqual(["aegis", "pulse"]);
  });

  it("says in the drawer which cats cannot lose it", async () => {
    const WEB = skill("s_web", "static-web", { type: "bundled", agent_ids: ["pulse"], locked_agent_ids: ["pulse"] });
    mockList([WEB]);
    vi.spyOn(api.skills, "get").mockResolvedValue({ ...WEB, body: "# Web" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Open static-web" }));
    expect(await screen.findByText(/Essential for Pulse: it cannot be unassigned/)).toBeInTheDocument();
  });
});
