import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../lib/api";
import type { Bootstrap } from "../../lib/types";
import { seedStore } from "../../pages/__tests__/helpers";
import { bootStarting } from "../../state/reducer";
import { useStore } from "../../state/store";
import { BootGate, SLOW_START_MS, StartupScreen } from "../StartupScreen";

const payload: Bootstrap = { agents: [], projects: [], activity: [], current_turn: null };
const down = () => new ApiError(0, "Cannot reach the CatCrewAI backend. Is it running?");

beforeEach(() => {
  vi.useFakeTimers();
  seedStore({ ready: false, boot: bootStarting(Date.now()) });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("start-up screen", () => {
  it("shows a spinner, and no error, while the engine is starting", () => {
    render(<StartupScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("Starting the engine");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/still starting/i)).toBeNull();
  });

  it("adds a calm note when a start is slow, but is still not an error", () => {
    render(<StartupScreen />);
    act(() => {
      vi.advanceTimersByTime(SLOW_START_MS + 50);
    });
    expect(screen.getByText(/first launch can take a little longer/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the failure, its details and Try again once the start has failed", async () => {
    const connect = vi.spyOn(useStore.getState(), "connect").mockResolvedValue();
    useStore.setState({ boot: { ...bootStarting(), phase: "failed", error: "The CatCrewAI engine could not start.", detail: "python: command not found" } });
    render(<StartupScreen />);
    expect(screen.getByRole("alert")).toHaveTextContent("The CatCrewAI engine could not start.");
    expect(screen.getByText("python: command not found")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(connect).toHaveBeenCalledTimes(1);
  });
});

describe("boot gate", () => {
  it("shows the splash until the backend has answered, then the app", () => {
    render(
      <BootGate>
        <div>the app</div>
      </BootGate>,
    );
    expect(screen.queryByText("the app")).toBeNull();
    expect(screen.getByTestId("startup")).toBeInTheDocument();
    act(() => useStore.setState({ ready: true }));
    expect(screen.getByText("the app")).toBeInTheDocument();
    expect(screen.queryByTestId("startup")).toBeNull();
  });

  it("never flashes 'Cannot reach the backend' while the engine takes a few seconds to start (the reported bug)", async () => {
    // the engine refuses connections for ~3 s, then answers
    let calls = 0;
    vi.spyOn(api, "bootstrap").mockImplementation(async () => {
      calls += 1;
      if (calls <= 5) throw down();
      return payload;
    });
    const seen: string[] = [];
    const { container } = render(
      <BootGate>
        <div>the app</div>
      </BootGate>,
    );
    const snap = () => seen.push(container.textContent ?? "");

    let started!: Promise<void>;
    act(() => {
      started = useStore.getState().connect();
    });
    for (let i = 0; i < 40 && !useStore.getState().ready; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      snap();
    }
    await started;

    expect(calls).toBe(6);
    expect(seen.length).toBeGreaterThan(2);
    for (const text of seen.slice(0, -1)) {
      expect(text).toContain("Starting the engine");
      expect(text).not.toMatch(/cannot reach|is it running|try again/i);
    }
    expect(useStore.getState().lastError).toBeNull();
    expect(screen.getByText("the app")).toBeInTheDocument();
  });

  it("gives up with a clear message when the engine never answers, and recovers if it shows up later", async () => {
    vi.spyOn(api, "bootstrap").mockRejectedValue(down());
    render(
      <BootGate>
        <div>the app</div>
      </BootGate>,
    );
    let started!: Promise<void>;
    act(() => {
      started = useStore.getState().connect();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000);
    });
    await started;
    expect(screen.getByRole("alert")).toHaveTextContent("did not answer within 30 seconds");
    expect(screen.queryByText("the app")).toBeNull();

    // the engine finally starts: the socket's resync loads the data and the gate opens by itself
    vi.mocked(api.bootstrap).mockResolvedValue(payload);
    await act(async () => {
      await useStore.getState().bootstrap();
    });
    expect(screen.getByText("the app")).toBeInTheDocument();
  });
});
