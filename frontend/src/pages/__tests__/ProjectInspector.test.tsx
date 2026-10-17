import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import type { ContextUsage, RosterEntry, Task } from "../../lib/types";
import { emptyRoom } from "../../state/reducer";
import { useStore } from "../../state/store";
import { ProjectRoomPage } from "../ProjectRoom";
import { makeAgent, makeProject, makeTelemetry, mockTelemetry, seedStore } from "./helpers";

const OPEN_KEY = "catcrew.inspectorOpen";
const TAB_KEY = "catcrew.inspectorTab";
const WIDTH_KEY = "catcrew.inspectorWidth";
const SIDEBAR_KEY = "catcrew.projectSidebarWidth";

const entry = (id: string): RosterEntry => ({
  bot_id: id,
  agent: makeAgent({ id }),
  is_room: id === "crew",
  joined_at: null,
  activity: { bot_id: id, status: "idle", state: "idle", text: "", updated_at: null },
});
const task = (id: string, status: Task["status"]): Task => ({
  id, project_id: "p1", assigned_to: "pulse", created_by: "crew", title: `Task ${id}`, description: "", status,
  created_at: "2026-01-01T00:00:01+00:00", updated_at: "2026-01-01T00:00:01+00:00", latest_summary: "",
});
const reading = (percent: number): ContextUsage => ({
  host: "lmstudio", model: "qwen", tokens: percent * 100, system_tokens: 0, limit: 10_000, percent, level: "ok", compressed: false,
  updated_at: "2026-01-01T00:00:00+00:00",
});

function mount() {
  return render(
    <MemoryRouter initialEntries={["/project/p1"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/project/:pid" element={<ProjectRoomPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
const seedRoom = (over: Partial<ReturnType<typeof emptyRoom>> = {}) =>
  seedStore({
    projects: [makeProject({ id: "p1", name: "Site" })],
    rooms: { p1: { ...emptyRoom(), loaded: true, roster: [entry("crew"), entry("pulse")], ...over } },
  });
const setViewport = (w: number) => Object.defineProperty(window, "innerWidth", { value: w, configurable: true, writable: true });
const sidebar = () => screen.getByLabelText("Project sidebar");
const inspector = () => screen.getByLabelText("Inspector", { selector: "aside" });
const inspectorHandle = () => screen.getByRole("separator", { name: "Resize inspector" });
const toggle = () => screen.getByRole("button", { name: "Toggle inspector" });

beforeEach(() => {
  if (!("PointerEvent" in window)) Object.defineProperty(window, "PointerEvent", { value: MouseEvent, configurable: true, writable: true });
  window.localStorage.clear();
  setViewport(1400);
  seedRoom();
  useStore.setState({ loadProject: vi.fn(async () => undefined), loadThread: vi.fn(async () => undefined), setActiveThread: vi.fn() });
  mockTelemetry();
});
afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("left sidebar: the fleet and nothing else", () => {
  it("lists the fleet, with no tabs and no way to add a cat by hand", () => {
    mount();
    const aside = within(sidebar());
    expect(aside.getByRole("heading", { name: "Fleet" })).toBeInTheDocument();
    expect(aside.getByTestId("fleet")).toBeInTheDocument();
    expect(aside.getByText("Pulse")).toBeInTheDocument();
    expect(aside.queryByRole("tablist")).not.toBeInTheDocument();
    expect(aside.queryByRole("tab")).not.toBeInTheDocument();
    expect(aside.queryByText("Tasks")).not.toBeInTheDocument();
    expect(aside.queryByText("Changes")).not.toBeInTheDocument();
    expect(screen.queryByText(/Add a cat/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add a cat/ })).not.toBeInTheDocument();
  });

  it("counts the cats that joined (the orchestrator is not counted)", () => {
    mount();
    expect(within(sidebar()).getByTitle("Cats working in this project")).toHaveTextContent("1");
  });

  it("tells a fresh project that cats join by themselves", () => {
    seedRoom({ roster: [entry("crew")] });
    mount();
    expect(within(sidebar()).getByText(/Cats join here the moment/)).toBeInTheDocument();
    expect(within(sidebar()).queryByTitle("Cats working in this project")).not.toBeInTheDocument();
  });

  it("keeps its resizable width", () => {
    window.localStorage.setItem(SIDEBAR_KEY, "400");
    mount();
    expect(sidebar()).toHaveStyle({ width: "400px" });
  });
});

describe("right inspector toggle", () => {
  it("is open on a wide window and remembers a closed choice", () => {
    mount();
    expect(inspector()).toBeInTheDocument();
    expect(toggle()).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(toggle());
    expect(screen.queryByLabelText("Inspector", { selector: "aside" })).not.toBeInTheDocument();
    expect(toggle()).toHaveAttribute("aria-pressed", "false");
    expect(window.localStorage.getItem(OPEN_KEY)).toBe("0");
    fireEvent.click(toggle());
    expect(inspector()).toBeInTheDocument();
    expect(window.localStorage.getItem(OPEN_KEY)).toBe("1");
  });

  it("sits in the header next to the terminal and settings buttons", () => {
    mount();
    const header = toggle().parentElement as HTMLElement;
    expect(within(header).getByRole("button", { name: "Toggle terminal" })).toBeInTheDocument();
    expect(within(header).getByRole("link", { name: "Project settings" })).toBeInTheDocument();
    const order = Array.from(header.querySelectorAll("button, a")).map((el) => el.getAttribute("aria-label"));
    expect(order.indexOf("Toggle inspector")).toBeLessThan(order.indexOf("Toggle terminal"));
    expect(order.indexOf("Toggle terminal")).toBeLessThan(order.indexOf("Project settings"));
  });

  it("starts closed on a narrow window unless the viewer opened it before", () => {
    setViewport(1000);
    const { unmount } = mount();
    expect(screen.queryByLabelText("Inspector", { selector: "aside" })).not.toBeInTheDocument();
    unmount();
    window.localStorage.setItem(OPEN_KEY, "1");
    mount();
    expect(inspector()).toBeInTheDocument();
  });

  it("follows a stored closed choice even on a wide window", () => {
    window.localStorage.setItem(OPEN_KEY, "0");
    mount();
    expect(screen.queryByLabelText("Inspector", { selector: "aside" })).not.toBeInTheDocument();
  });

  it("polls nothing while closed", async () => {
    window.localStorage.setItem(OPEN_KEY, "0");
    const live = mockTelemetry(makeTelemetry());
    mount();
    await act(async () => {
      await Promise.resolve();
    });
    expect(live).not.toHaveBeenCalled();
    fireEvent.click(toggle());
    await screen.findByTestId("telemetry");
    expect(live).toHaveBeenCalledWith("p1", expect.anything());
  });

  it("still works when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mount();
    expect(inspector()).toBeInTheDocument();
    fireEvent.click(toggle());
    expect(screen.queryByLabelText("Inspector", { selector: "aside" })).not.toBeInTheDocument();
  });

  it("marks the button when the context is nearly full", () => {
    seedRoom({ context: reading(90) });
    mount();
    expect(screen.getByTestId("inspector-alert")).toBeInTheDocument();
  });

  it("does not mark it while the context has room", () => {
    seedRoom({ context: reading(70) });
    mount();
    expect(screen.queryByTestId("inspector-alert")).not.toBeInTheDocument();
  });
});

describe("inspector tabs", () => {
  it("opens on Telemetry, and Tasks and Changes moved here from the sidebar", () => {
    seedRoom({ tasks: [task("a", "in_progress"), task("b", "pending"), task("c", "done")] });
    mount();
    const tabs = within(inspector()).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Telemetry", "Tasks2", "Changes"]);
    expect(within(inspector()).getByRole("tab", { name: /Telemetry/ })).toHaveAttribute("aria-selected", "true");
    expect(within(inspector()).getByTestId("telemetry-loading")).toBeInTheDocument(); // the poll is still out

    fireEvent.click(within(inspector()).getByRole("tab", { name: /Tasks/ }));
    expect(within(inspector()).getAllByTestId("task-row")).toHaveLength(3);
    expect(within(inspector()).getByText("Task a")).toBeInTheDocument();

    fireEvent.click(within(inspector()).getByRole("tab", { name: /Changes/ }));
    expect(within(inspector()).getByText("No file changes yet")).toBeInTheDocument();
    expect(within(inspector()).queryByTestId("telemetry")).not.toBeInTheDocument();
  });

  it("shows a change row for each file the cats wrote", () => {
    seedRoom({
      changes: [{ id: "c1", task_id: "a", project_id: "p1", agent_id: "pulse", file_path: "src/app.py", diff_uuid: "d1", created_at: "2026-01-01T00:00:02+00:00" }],
    });
    vi.spyOn(api.tasks, "diff").mockResolvedValue({ diff_uuid: "d1", file_path: "src/app.py", agent_id: "pulse", diff_content: "+x" } as never);
    mount();
    fireEvent.click(within(inspector()).getByRole("tab", { name: /Changes/ }));
    expect(within(inspector()).getByTestId("change-row")).toHaveTextContent("src/app.py");
    expect(within(inspector()).getByRole("tab", { name: /Changes/ })).toHaveTextContent("1");
  });

  it("remembers the last tab", () => {
    const { unmount } = mount();
    fireEvent.click(within(inspector()).getByRole("tab", { name: /Tasks/ }));
    expect(window.localStorage.getItem(TAB_KEY)).toBe("tasks");
    unmount();
    mount();
    expect(within(inspector()).getByRole("tab", { name: /Tasks/ })).toHaveAttribute("aria-selected", "true");
  });

  it("waits for the project's data before listing tasks", () => {
    seedRoom({ loaded: false });
    window.localStorage.setItem(TAB_KEY, "tasks");
    mount();
    expect(within(inspector()).queryByTestId("task-row")).not.toBeInTheDocument();
    expect(within(inspector()).queryByText("No tasks yet")).not.toBeInTheDocument();
  });
});

describe("inspector resize", () => {
  it("starts at 340px and widens when its left edge is dragged to the left", () => {
    mount();
    expect(inspector()).toHaveStyle({ width: "340px" });
    fireEvent.pointerDown(inspectorHandle(), { clientX: 1000, button: 0 });
    fireEvent.pointerMove(window, { clientX: 900 });
    expect(inspector()).toHaveStyle({ width: "440px" });
    fireEvent.pointerUp(window, { clientX: 920 });
    expect(inspector()).toHaveStyle({ width: "420px" });
    expect(window.localStorage.getItem(WIDTH_KEY)).toBe("420");
  });

  it("stops at 300px and at 560px", () => {
    mount();
    fireEvent.pointerDown(inspectorHandle(), { clientX: 1000, button: 0 });
    fireEvent.pointerMove(window, { clientX: 1400 });
    expect(inspector()).toHaveStyle({ width: "300px" });
    fireEvent.pointerMove(window, { clientX: 0 });
    expect(inspector()).toHaveStyle({ width: "560px" });
    fireEvent.pointerUp(window, { clientX: 0 });
  });

  it("answers the arrow keys (left grows it), Home and a double click", () => {
    mount();
    fireEvent.keyDown(inspectorHandle(), { key: "ArrowLeft" });
    expect(inspector()).toHaveStyle({ width: "356px" });
    fireEvent.keyDown(inspectorHandle(), { key: "ArrowRight", shiftKey: true });
    expect(inspector()).toHaveStyle({ width: "308px" });
    fireEvent.keyDown(inspectorHandle(), { key: "Home" });
    expect(inspector()).toHaveStyle({ width: "340px" });
    fireEvent.keyDown(inspectorHandle(), { key: "ArrowLeft", shiftKey: true });
    fireEvent.doubleClick(inspectorHandle());
    expect(inspector()).toHaveStyle({ width: "340px" });
  });

  it("yields to the sidebar so the chat keeps its room, and springs back when the sidebar shrinks", () => {
    window.localStorage.setItem(WIDTH_KEY, "500");
    window.localStorage.setItem(SIDEBAR_KEY, "560");
    const { unmount } = mount();
    expect(sidebar()).toHaveStyle({ width: "560px" });
    expect(inspector()).toHaveStyle({ width: "360px" }); // 1400 - 480 (chat) - 560 (sidebar)
    unmount();
    window.localStorage.setItem(SIDEBAR_KEY, "240");
    mount();
    expect(inspector()).toHaveStyle({ width: "500px" });
  });

  it("shrinks when the window shrinks", () => {
    window.localStorage.setItem(WIDTH_KEY, "500");
    mount();
    expect(inspector()).toHaveStyle({ width: "500px" });
    setViewport(1150);
    fireEvent(window, new Event("resize"));
    expect(inspector()).toHaveStyle({ width: "350px" }); // 1150 - 480 - 320
  });

  it("gives way live while the sidebar is being dragged wider", () => {
    window.localStorage.setItem(WIDTH_KEY, "560");
    mount();
    expect(inspector()).toHaveStyle({ width: "560px" }); // 1400 - 480 - 320 = 600: fits
    const handle = screen.getByRole("separator", { name: "Resize sidebar" });
    fireEvent.pointerDown(handle, { clientX: 320, button: 0 });
    fireEvent.pointerMove(window, { clientX: 560 });
    fireEvent.pointerUp(window, { clientX: 560 });
    expect(sidebar()).toHaveStyle({ width: "560px" });
    expect(inspector()).toHaveStyle({ width: "360px" });
  });
});

describe("telemetry inside the room", () => {
  it("shows the gauge the moment a context.usage event lands in the store", async () => {
    mockTelemetry(makeTelemetry());
    mount();
    await screen.findByTestId("telemetry");
    expect(screen.getByText(/Nothing measured yet/)).toBeInTheDocument();
    await act(async () => {
      useStore.getState().applyEvent({
        v: 1, seq: 1, ts: "2026-01-01T00:00:00+00:00", type: "context.usage", project_id: "p1",
        data: { host: "lmstudio", model: "qwen", tokens: 4120, system_tokens: 800, limit: 8192, percent: 50, level: "ok", compressed: false },
      } as never);
    });
    expect(screen.getByTestId("context-line")).toHaveTextContent("Context: 4,120 / 8,192 tokens (50% used)");
  });
});
