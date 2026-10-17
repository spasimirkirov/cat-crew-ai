import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { seedStore } from "../../pages/__tests__/helpers";
import { PowerOff } from "../PowerOff";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));

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
afterEach(() => asTauri(false));

describe("PowerOff", () => {
  it("is not rendered in a plain browser (nothing to quit there)", () => {
    asTauri(false);
    const { container } = render(<PowerOff />);
    expect(container).toBeEmptyDOMElement();
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
