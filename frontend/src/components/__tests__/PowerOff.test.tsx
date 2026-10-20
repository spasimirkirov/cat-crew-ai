import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import { seedStore } from "../../pages/__tests__/helpers";
import { PowerOff } from "../PowerOff";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));
let browserMode = false;
vi.mock("../../lib/config", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/config")>()),
  isBrowserMode: () => browserMode,
}));

const asTauri = (on: boolean) => {
  const w = window as unknown as Record<string, unknown>;
  if (on) w.__TAURI_INTERNALS__ = {};
  else delete w.__TAURI_INTERNALS__;
};

beforeEach(() => {
  invoke.mockReset();
  invoke.mockResolvedValue(undefined);
  seedStore();
  asTauri(true);
});
afterEach(() => {
  asTauri(false);
  browserMode = false;
  vi.restoreAllMocks();
});

describe("PowerOff", () => {
  it("is not rendered in a tab the backend did not serve (Vite dev: nothing to quit there)", () => {
    asTauri(false);
    const { container } = render(<PowerOff />);
    expect(container).toBeEmptyDOMElement();
  });

  it("in browser mode stops the engine that served the tab", async () => {
    asTauri(false);
    browserMode = true;
    const shutdown = vi.spyOn(api, "shutdown").mockResolvedValue({ status: "stopping" });
    render(<PowerOff />);
    fireEvent.click(screen.getByRole("button", { name: "Power off" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Closing this tab leaves the engine running");
    fireEvent.click(screen.getByRole("button", { name: /shut down/i }));
    await waitFor(() => expect(shutdown).toHaveBeenCalled());
    expect(await screen.findByText(/CatCrewAI is stopped/)).toBeInTheDocument();
    expect(invoke).not.toHaveBeenCalled();
  });

  it("asks before doing anything, and Cancel leaves the app running", () => {
    render(<PowerOff />);
    fireEvent.click(screen.getByRole("button", { name: "Power off" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Shut down CatCrewAI and stop background engine?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(invoke).not.toHaveBeenCalled();
  });

  it("invokes quit_app on confirmation", async () => {
    render(<PowerOff />);
    fireEvent.click(screen.getByRole("button", { name: "Power off" }));
    fireEvent.click(screen.getByRole("button", { name: /shut down/i }));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("quit_app"));
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it("shows the error and lets the user retry when quit_app fails", async () => {
    invoke.mockRejectedValueOnce(new Error("boom"));
    render(<PowerOff />);
    fireEvent.click(screen.getByRole("button", { name: "Power off" }));
    fireEvent.click(screen.getByRole("button", { name: /shut down/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByRole("button", { name: /shut down/i })).toBeEnabled();
  });

  it("warns when the crew is still working", () => {
    seedStore({ activity: { pulse: { bot_id: "pulse", status: "coding", state: "working", text: "", updated_at: null } } });
    render(<PowerOff />);
    fireEvent.click(screen.getByRole("button", { name: "Power off" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(/still working/i);
  });
});
