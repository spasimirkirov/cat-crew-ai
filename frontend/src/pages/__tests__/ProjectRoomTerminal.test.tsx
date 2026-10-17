import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useStore } from "../../state/store";
import { ProjectRoomPage } from "../ProjectRoom";
import { makeProject, mockTelemetry, seedStore } from "./helpers";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 1;
  closed = false;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send() {}
  close() {
    this.closed = true;
  }
}

function room(path = "/project/p1") {
  return (
    <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/project/:pid" element={<ProjectRoomPage />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  seedStore({ projects: [makeProject({ id: "p1", name: "Site", folders: ["/work/site"] }), makeProject({ id: "p2", name: "Other" })] });
  // The room's own data loading is not what these tests are about.
  useStore.setState({ loadProject: vi.fn(async () => undefined), loadThread: vi.fn(async () => undefined), setActiveThread: vi.fn() });
  mockTelemetry();
});
afterEach(() => vi.unstubAllGlobals());

describe("project room terminal", () => {
  it("has no terminal until it is asked for, then a header button and Ctrl+` toggle it", () => {
    render(room());
    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
    expect(FakeSocket.instances).toHaveLength(0); // nothing is started for a terminal nobody opened

    const button = screen.getByRole("button", { name: "Toggle terminal" });
    fireEvent.click(button);
    expect(screen.getByTestId("terminal-panel")).toBeVisible();
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(FakeSocket.instances[0].url).toMatch(/\/ws\/projects\/p1\/terminal$/);

    fireEvent.keyDown(window, { code: "Backquote", key: "`", ctrlKey: true });
    expect(screen.getByTestId("terminal-panel")).not.toBeVisible();
    expect(FakeSocket.instances).toHaveLength(1); // hidden, not restarted: a running command survives
    expect(FakeSocket.instances[0].closed).toBe(false);

    fireEvent.keyDown(window, { code: "Backquote", key: "`", metaKey: true });
    expect(screen.getByTestId("terminal-panel")).toBeVisible();
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("ignores a plain backquote and closes the shell when the room is left", () => {
    const { unmount } = render(room());
    fireEvent.keyDown(window, { code: "Backquote", key: "`" });
    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
    fireEvent.keyDown(window, { code: "Backquote", key: "`", ctrlKey: true });
    expect(FakeSocket.instances).toHaveLength(1);
    unmount();
    expect(FakeSocket.instances[0].closed).toBe(true);
  });

  it("links to the project's settings", () => {
    render(room());
    expect(screen.getByRole("link", { name: "Project settings" })).toHaveAttribute("href", "/project/p1/settings");
  });

  it("closes the old project's terminal and starts closed when the project changes", async () => {
    function Switch() {
      return (
        <MemoryRouter initialEntries={["/project/p1"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Routes>
            <Route path="/project/:pid" element={<ProjectRoomPage />} />
          </Routes>
          <NavTo />
        </MemoryRouter>
      );
    }
    // Navigate inside the same router instance.
    const { useNavigate } = await import("react-router-dom");
    function NavTo() {
      const go = useNavigate();
      return <button onClick={() => go("/project/p2")}>go p2</button>;
    }
    render(<Switch />);
    fireEvent.click(screen.getByRole("button", { name: "Toggle terminal" }));
    expect(FakeSocket.instances).toHaveLength(1);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "go p2" }));
    });
    expect(FakeSocket.instances[0].closed).toBe(true);
    expect(screen.queryByTestId("terminal-panel")).not.toBeInTheDocument();
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
