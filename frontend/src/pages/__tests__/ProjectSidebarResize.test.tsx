import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clampWidth } from "../../state/useResizableWidth";
import { useStore } from "../../state/store";
import { ProjectRoomPage } from "../ProjectRoom";
import { makeProject, mockTelemetry, seedStore } from "./helpers";

const KEY = "catcrew.projectSidebarWidth";

function mount() {
  return render(
    <MemoryRouter initialEntries={["/project/p1"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/project/:pid" element={<ProjectRoomPage />} />
      </Routes>
    </MemoryRouter>,
  );
}
const sidebar = () => screen.getByLabelText("Project sidebar");
const handle = () => screen.getByRole("separator", { name: "Resize sidebar" });

beforeEach(() => {
  // jsdom has no PointerEvent: a MouseEvent carries the same clientX / button the handler reads.
  if (!("PointerEvent" in window)) Object.defineProperty(window, "PointerEvent", { value: MouseEvent, configurable: true, writable: true });
  window.localStorage.clear();
  Object.defineProperty(window, "innerWidth", { value: 1400, configurable: true, writable: true });
  seedStore({ projects: [makeProject({ id: "p1", name: "Site" })] });
  useStore.setState({ loadProject: vi.fn(async () => undefined), loadThread: vi.fn(async () => undefined), setActiveThread: vi.fn() });
  mockTelemetry();
});
afterEach(() => window.localStorage.clear());

describe("project sidebar resize", () => {
  it("starts at the familiar 320px", () => {
    mount();
    expect(sidebar()).toHaveStyle({ width: "320px" });
    expect(handle()).toHaveAttribute("aria-valuenow", "320");
  });

  it("follows a drag to the right and to the left, and remembers the width", () => {
    mount();
    fireEvent.pointerDown(handle(), { clientX: 320, button: 0 });
    fireEvent.pointerMove(window, { clientX: 420 });
    expect(sidebar()).toHaveStyle({ width: "420px" });
    fireEvent.pointerUp(window, { clientX: 380 });
    expect(sidebar()).toHaveStyle({ width: "380px" });
    expect(window.localStorage.getItem(KEY)).toBe("380");
  });

  it("stops at a minimum and at a maximum that leaves room for the chat", () => {
    mount();
    fireEvent.pointerDown(handle(), { clientX: 320, button: 0 });
    fireEvent.pointerMove(window, { clientX: 0 });
    expect(sidebar()).toHaveStyle({ width: "240px" });
    fireEvent.pointerMove(window, { clientX: 2000 });
    expect(sidebar()).toHaveStyle({ width: "560px" });
    fireEvent.pointerUp(window, { clientX: 2000 });
  });

  it("never takes more than the window leaves after the 480px the chat keeps", () => {
    expect(clampWidth({ min: 240, max: 560, reserve: 480 }, 900, 900)).toBe(420);
    expect(clampWidth({ min: 240, max: 560, reserve: 480 }, 900, 1400)).toBe(560);
    expect(clampWidth({ min: 240, max: 560, reserve: 480 }, 100, 1400)).toBe(240);
    expect(clampWidth({ min: 240, max: 560, reserve: 480 }, 900, 600)).toBe(240); // tiny window: the minimum wins
  });

  it("can be moved with the arrow keys and reset with Home or a double click", () => {
    mount();
    fireEvent.keyDown(handle(), { key: "ArrowRight" });
    expect(sidebar()).toHaveStyle({ width: "336px" });
    fireEvent.keyDown(handle(), { key: "ArrowLeft", shiftKey: true });
    expect(sidebar()).toHaveStyle({ width: "288px" });
    fireEvent.keyDown(handle(), { key: "Home" });
    expect(sidebar()).toHaveStyle({ width: "320px" });
    fireEvent.keyDown(handle(), { key: "ArrowRight", shiftKey: true });
    fireEvent.doubleClick(handle());
    expect(sidebar()).toHaveStyle({ width: "320px" });
  });

  it("opens at the saved width, clamped if the window is smaller now", () => {
    window.localStorage.setItem(KEY, "500");
    const { unmount } = mount();
    expect(sidebar()).toHaveStyle({ width: "500px" });
    unmount();
    (window as unknown as { innerWidth: number }).innerWidth = 800;
    mount();
    expect(sidebar()).toHaveStyle({ width: "320px" }); // 800 - 480
  });

  it("shrinks the sidebar when the window shrinks so the chat keeps its room", () => {
    window.localStorage.setItem(KEY, "540");
    mount();
    expect(sidebar()).toHaveStyle({ width: "540px" });
    (window as unknown as { innerWidth: number }).innerWidth = 900;
    fireEvent(window, new Event("resize"));
    expect(sidebar()).toHaveStyle({ width: "420px" });
  });

  it("still works when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    mount();
    expect(sidebar()).toHaveStyle({ width: "320px" });
    fireEvent.keyDown(handle(), { key: "ArrowRight" });
    expect(sidebar()).toHaveStyle({ width: "336px" });
    vi.restoreAllMocks();
  });
});
