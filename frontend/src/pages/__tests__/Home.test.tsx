import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../lib/api";
import type { Activity } from "../../lib/types";
import { useStore } from "../../state/store";
import { HomePage } from "../Home";
import { agentsRecord, makeAgent, makeProject, renderPage, seedStore, user } from "./helpers";

const minsAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const pulse = makeAgent({ id: "pulse", name: "Pulse", is_system: true });
const pixel = makeAgent({ id: "pixel", name: "Pixel", is_system: true, color: "#a78bfa" });
const cipher = makeAgent({ id: "cipher", name: "Cipher", is_system: true });

const alpha = makeProject({ id: "p1", name: "Alpha Site", description: "A marketing site", folders: ["/work/alpha"], bot_ids: ["pulse", "pixel", "ghost"], created_at: minsAgo(5), phase: "build" });
const beta = makeProject({ id: "p2", name: "Beta API", folders: [], bot_ids: [], created_at: minsAgo(300), phase: "" });
const gamma = makeProject({ id: "p3", name: "Gamma CLI", folders: ["/work/gamma"], bot_ids: ["cipher"], created_at: minsAgo(3 * 24 * 60), phase: "done" });

const working = (id: string): Activity => ({ bot_id: id, status: "coding", state: "working", text: "Editing", updated_at: null });

function seed(extra: Parameters<typeof seedStore>[0] = {}) {
  seedStore({ agents: agentsRecord(pulse, pixel, cipher), projects: [beta, gamma, alpha], ...extra });
}

beforeEach(() => {
  vi.spyOn(api.projects, "list").mockImplementation(async () => useStore.getState().projects);
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("Home launcher", () => {
  it("shows a loading state until the store is ready", () => {
    seedStore({ ready: false });
    renderPage(<HomePage />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    expect(screen.queryByText("Alpha Site")).toBeNull();
  });

  it("shows an empty state with no projects", () => {
    seed({ projects: [] });
    renderPage(<HomePage />);
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
  });

  it("lists projects newest first with phase chips, crew avatars and relative dates, and never mentions tasks", () => {
    seed();
    const { container } = renderPage(<HomePage />);

    const names = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(names).toEqual(["Alpha Site", "Beta API", "Gamma CLI"]);

    const alphaCard = screen.getByText("Alpha Site").closest("article")!;
    expect(within(alphaCard).getByText("A marketing site")).toBeInTheDocument();
    expect(within(alphaCard).getByText("/work/alpha").parentElement).toHaveAttribute("title", "/work/alpha");
    expect(within(alphaCard).getByTestId("phase-chip")).toHaveTextContent("Build");
    expect(within(alphaCard).getByText("Created 5 mins ago")).toBeInTheDocument();
    // unknown bot ids are skipped
    expect(within(alphaCard).getByRole("img", { name: "Crew: Pulse, Pixel" })).toBeInTheDocument();

    const betaCard = screen.getByText("Beta API").closest("article")!;
    expect(within(betaCard).getByTestId("phase-chip")).toHaveTextContent("Not started");
    expect(within(betaCard).getByText("Created 5 hours ago")).toBeInTheDocument();
    expect(within(betaCard).getByText("No workspace folder")).toBeInTheDocument();

    const gammaCard = screen.getByText("Gamma CLI").closest("article")!;
    expect(within(gammaCard).getByTestId("phase-chip")).toHaveTextContent("Finished");
    // older than a day: an exact date, not "N days ago"
    expect(within(gammaCard).getByText(/^Created /).textContent).not.toMatch(/ago/);

    // Hard product rule: no work items on Home.
    expect(screen.queryByText(/task/i)).toBeNull();
    expect(container.textContent).not.toMatch(/task/i);
  });

  it("shows a live 'N cats working' chip from the store activity", () => {
    seed({ activity: { pulse: working("pulse"), pixel: working("pixel") } });
    renderPage(<HomePage />);
    const card = screen.getByText("Alpha Site").closest("article")!;
    expect(within(card).getByTestId("working-chip")).toHaveTextContent("2 cats working");
    // a project whose cats are idle shows no chip
    expect(within(screen.getByText("Gamma CLI").closest("article")!).queryByTestId("working-chip")).toBeNull();

    act(() => useStore.setState({ activity: { pulse: working("pulse") } }));
    expect(within(card).getByTestId("working-chip")).toHaveTextContent("1 cat working");
  });

  it("does not count cats as working here while a turn runs in another project", () => {
    seed({
      activity: { pulse: working("pulse") },
      currentTurn: { id: "t1", project_id: "p2", bot_id: "crew", started_at: minsAgo(1), phase: "build", kind: "room" },
    });
    renderPage(<HomePage />);
    expect(screen.queryByTestId("working-chip")).toBeNull();
  });

  it("navigates into a project when its card is clicked", async () => {
    seed();
    renderPage(<HomePage />);
    await user.click(screen.getByRole("button", { name: "Alpha Site" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/project/p1");
  });

  it("refreshes the project list on mount", async () => {
    seed();
    renderPage(<HomePage />);
    await waitFor(() => expect(api.projects.list).toHaveBeenCalled());
  });

  describe("new project", () => {
    it("requires a name", async () => {
      seed();
      const create = vi.spyOn(api.projects, "create");
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      const dialog = screen.getByRole("dialog", { name: "New project" });
      await user.click(within(dialog).getByRole("button", { name: "Create project" }));
      expect(within(dialog).getByRole("alert")).toHaveTextContent("Give the project a name.");
      expect(within(dialog).getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
      expect(create).not.toHaveBeenCalled();
    });

    it("creates the project with the folder and no crew, then opens its room", async () => {
      seed();
      const create = vi.spyOn(api.projects, "create").mockResolvedValue(makeProject({ id: "new1", name: "Fresh" }));
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      const dialog = screen.getByRole("dialog", { name: "New project" });

      // cats join by themselves when a task needs them: there is nothing to pick
      expect(within(dialog).queryByText(/Crew \(optional\)/)).toBeNull();
      expect(within(dialog).queryByRole("button", { name: /Pulse/ })).toBeNull();
      expect(within(dialog).getByText(/Cats join the project on their own when a task needs them/)).toBeInTheDocument();

      await user.type(within(dialog).getByLabelText("Name"), "  Fresh  ");
      await user.type(within(dialog).getByLabelText("Description"), "Something new");
      await user.type(within(dialog).getByLabelText("Workspace folder"), "/work/fresh");
      await user.click(within(dialog).getByRole("button", { name: "Create project" }));

      await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/project/new1"));
      expect(create).toHaveBeenCalledWith({ name: "Fresh", description: "Something new", folders: ["/work/fresh"] });
      expect(useStore.getState().projects.some((p) => p.id === "new1")).toBe(true);
    });

    it("sends the kickoff brief only when one is written, and an empty folder list without a folder", async () => {
      seed();
      const create = vi.spyOn(api.projects, "create").mockResolvedValue(makeProject({ id: "new2", name: "Go" }));
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      const dialog = screen.getByRole("dialog", { name: "New project" });
      await user.type(within(dialog).getByLabelText("Name"), "Go");
      await user.type(within(dialog).getByLabelText(/Kickoff brief/), "Build a landing page");
      await user.click(within(dialog).getByRole("button", { name: "Create project" }));
      await waitFor(() => expect(create).toHaveBeenCalled());
      expect(create).toHaveBeenCalledWith({ name: "Go", description: "", folders: [], kickoff: "Build a landing page" });
    });

    it("shows backend errors inline and stays open", async () => {
      seed();
      vi.spyOn(api.projects, "create").mockRejectedValue(new ApiError(422, "That folder is not a directory."));
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      const dialog = screen.getByRole("dialog", { name: "New project" });
      await user.type(within(dialog).getByLabelText("Name"), "Nope");
      await user.click(within(dialog).getByRole("button", { name: "Create project" }));
      expect(await within(dialog).findByRole("alert")).toHaveTextContent("That folder is not a directory.");
      expect(screen.queryByTestId("location")).toBeNull();
      expect(within(dialog).getByRole("button", { name: "Create project" })).toBeEnabled();
    });

    it("closes on Escape", async () => {
      seed();
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      await user.escape();
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  describe("folder browser", () => {
    const tree: Record<string, { path: string; parent: string | null; entries: { name: string; path: string; is_dir: boolean }[] }> = {
      "": {
        path: "/home/u",
        parent: "/home",
        entries: [
          { name: "code", path: "/home/u/code", is_dir: true },
          { name: "notes.txt", path: "/home/u/notes.txt", is_dir: false },
        ],
      },
      "/home/u/code": { path: "/home/u/code", parent: "/home/u", entries: [{ name: "site", path: "/home/u/code/site", is_dir: true }] },
      "/home/u": { path: "/home/u", parent: "/home", entries: [{ name: "code", path: "/home/u/code", is_dir: true }] },
    };

    beforeEach(() => {
      vi.spyOn(api.projects, "browse").mockImplementation(async (p = "") => {
        if (!(p in tree)) throw new ApiError(404, "Not found");
        return tree[p];
      });
    });

    it("descends into folders, goes up, and fills the workspace field", async () => {
      seed();
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      await user.click(screen.getByRole("button", { name: /Browse/ }));

      expect(await screen.findByTestId("browse-path")).toHaveTextContent("/home/u");
      expect(await screen.findByRole("button", { name: "code" })).toBeInTheDocument();
      expect(screen.queryByText("notes.txt")).toBeNull(); // files are not offered

      await user.click(screen.getByRole("button", { name: "code" }));
      await waitFor(() => expect(screen.getByTestId("browse-path")).toHaveTextContent("/home/u/code"));
      expect(api.projects.browse).toHaveBeenLastCalledWith("/home/u/code");

      await user.click(screen.getByRole("button", { name: "Up" }));
      await waitFor(() => expect(screen.getByTestId("browse-path")).toHaveTextContent(/^\/home\/u$/));

      await user.click(screen.getByRole("button", { name: "code" }));
      await waitFor(() => expect(screen.getByTestId("browse-path")).toHaveTextContent("/home/u/code"));
      await user.click(screen.getByRole("button", { name: "Use this folder" }));

      // back on the form, with the folder filled in and earlier input kept
      const dialog = screen.getByRole("dialog", { name: "New project" });
      expect(within(dialog).getByLabelText("Workspace folder")).toHaveValue("/home/u/code");
    });

    it("makes a new folder inside the current one and steps into it, ready to be chosen", async () => {
      seed();
      const create = vi.spyOn(api.projects, "createFolder").mockResolvedValue({ path: "/home/u/my-site", parent: "/home/u", entries: [] });
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      await user.click(screen.getByRole("button", { name: /Browse/ }));
      await waitFor(() => expect(screen.getByTestId("browse-path")).toHaveTextContent("/home/u"));

      await user.click(screen.getByRole("button", { name: /New folder/ }));
      const name = screen.getByLabelText("New folder name");
      await user.type(name, "my-site");
      await user.click(screen.getByRole("button", { name: "Create" }));

      expect(create).toHaveBeenCalledWith("/home/u", "my-site");
      await waitFor(() => expect(screen.getByTestId("browse-path")).toHaveTextContent("/home/u/my-site"));
      expect(screen.queryByLabelText("New folder name")).toBeNull(); // the field closes
      expect(screen.getByText("No sub-folders here.")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Use this folder" }));
      const dialog = screen.getByRole("dialog", { name: "New project" });
      expect(within(dialog).getByLabelText("Workspace folder")).toHaveValue("/home/u/my-site");
    });

    it("shows why a folder could not be made and keeps the field open", async () => {
      seed();
      vi.spyOn(api.projects, "createFolder").mockRejectedValue(new ApiError(409, "'code' already exists in this folder."));
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      await user.click(screen.getByRole("button", { name: /Browse/ }));
      await waitFor(() => expect(screen.getByTestId("browse-path")).toHaveTextContent("/home/u"));
      await user.click(screen.getByRole("button", { name: /New folder/ }));
      await user.type(screen.getByLabelText("New folder name"), "code");
      await user.click(screen.getByRole("button", { name: "Create" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
      expect(screen.getByLabelText("New folder name")).toHaveValue("code");
      expect(screen.getByTestId("browse-path")).toHaveTextContent("/home/u"); // did not move
    });

    it("asks for a name, and Escape closes only the name field, not the dialog", async () => {
      seed();
      const create = vi.spyOn(api.projects, "createFolder");
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      await user.click(screen.getByRole("button", { name: /Browse/ }));
      await waitFor(() => expect(screen.getByTestId("browse-path")).toHaveTextContent("/home/u"));
      await user.click(screen.getByRole("button", { name: /New folder/ }));
      await user.click(screen.getByRole("button", { name: "Create" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Type a name");
      expect(create).not.toHaveBeenCalled();
      fireEvent.keyDown(screen.getByLabelText("New folder name"), { key: "Escape" });
      expect(screen.queryByLabelText("New folder name")).toBeNull();
      expect(screen.getByRole("dialog", { name: "Choose a workspace folder" })).toBeInTheDocument();
    });

    it("cannot make a folder on the drive list (there is nothing to put it in)", async () => {
      seed();
      vi.spyOn(api.projects, "browse").mockResolvedValue({ path: "", parent: null, entries: [{ name: "C:\\", path: "C:\\", is_dir: true }] });
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      await user.click(screen.getByRole("button", { name: /Browse/ }));
      await screen.findByRole("button", { name: "C:\\" });
      expect(screen.getByRole("button", { name: /New folder/ })).toBeDisabled();
    });

    it("Escape steps back from the browser to the form instead of closing everything", async () => {
      seed();
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: /new project/i }));
      await user.type(screen.getByLabelText("Name"), "Keep me");
      await user.click(screen.getByRole("button", { name: /Browse/ }));
      await screen.findByTestId("browse-path");
      await user.escape();
      expect(screen.getByRole("dialog", { name: "New project" })).toBeInTheDocument();
      expect(screen.getByLabelText("Name")).toHaveValue("Keep me");
    });

    it("Open folder creates a project named after the folder and opens it", async () => {
      seed();
      const create = vi.spyOn(api.projects, "create").mockResolvedValue(makeProject({ id: "opened", name: "code" }));
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: "Open folder" }));
      await screen.findByRole("button", { name: "code" });
      await user.click(screen.getByRole("button", { name: "code" }));
      await waitFor(() => expect(screen.getByTestId("browse-path")).toHaveTextContent("/home/u/code"));
      await user.click(screen.getByRole("button", { name: "Open this folder" }));
      await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/project/opened"));
      expect(create).toHaveBeenCalledWith({ name: "code", folders: ["/home/u/code"] });
    });

    it("Open folder shows a backend failure inline", async () => {
      seed();
      vi.spyOn(api.projects, "create").mockRejectedValue(new ApiError(422, "Not a directory."));
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: "Open folder" }));
      await screen.findByRole("button", { name: "code" });
      await user.click(screen.getByRole("button", { name: "Open this folder" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Not a directory.");
    });
  });

  describe("project menu", () => {
    it("renames a project through the API and updates the card", async () => {
      seed();
      const patch = vi.spyOn(api.projects, "patch").mockResolvedValue({ ...alpha, name: "Alpha Prime" });
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: "Actions for Alpha Site" }));
      await user.click(screen.getByRole("menuitem", { name: /rename/i }));
      const dialog = screen.getByRole("dialog", { name: "Rename project" });
      const input = within(dialog).getByLabelText("Name");
      await user.clear(input);
      await user.type(input, "Alpha Prime");
      await user.click(within(dialog).getByRole("button", { name: "Save" }));
      await waitFor(() => expect(screen.getByText("Alpha Prime")).toBeInTheDocument());
      expect(patch).toHaveBeenCalledWith("p1", { name: "Alpha Prime", description: "A marketing site" });
      expect(screen.queryByText("Alpha Site")).toBeNull();
    });

    it("deletes after confirmation", async () => {
      seed();
      const remove = vi.spyOn(api.projects, "remove").mockResolvedValue(undefined);
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: "Actions for Beta API" }));
      await user.click(screen.getByRole("menuitem", { name: /delete/i }));
      const dialog = screen.getByRole("dialog", { name: "Delete project" });
      expect(remove).not.toHaveBeenCalled();
      await user.click(within(dialog).getByRole("button", { name: "Delete project" }));
      await waitFor(() => expect(screen.queryByText("Beta API")).toBeNull());
      expect(remove).toHaveBeenCalledWith("p2");
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("surfaces the 409 message when a turn is still running", async () => {
      seed();
      vi.spyOn(api.projects, "remove").mockRejectedValue(new ApiError(409, "Stop the running turn before deleting this project."));
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: "Actions for Alpha Site" }));
      await user.click(screen.getByRole("menuitem", { name: /delete/i }));
      const dialog = screen.getByRole("dialog", { name: "Delete project" });
      await user.click(within(dialog).getByRole("button", { name: "Delete project" }));
      expect(await within(dialog).findByRole("alert")).toHaveTextContent("Stop the running turn before deleting this project.");
      // still there
      expect(useStore.getState().projects.some((p) => p.id === "p1")).toBe(true);
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      expect(screen.getByText("Alpha Site")).toBeInTheDocument();
    });

    it("opening the menu does not navigate", async () => {
      seed();
      renderPage(<HomePage />);
      await user.click(screen.getByRole("button", { name: "Actions for Alpha Site" }));
      expect(screen.queryByTestId("location")).toBeNull();
      expect(screen.getByRole("menu")).toBeInTheDocument();
      fireEvent.mouseDown(document.body);
      expect(screen.queryByRole("menu")).toBeNull();
    });
  });
});
