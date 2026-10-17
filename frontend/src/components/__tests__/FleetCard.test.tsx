import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RosterEntry } from "../../lib/types";
import { seedStore, makeAgent } from "../../pages/__tests__/helpers";
import { useStore } from "../../state/store";
import { FleetCard } from "../room/FleetCard";

const entry: RosterEntry = {
  bot_id: "pulse",
  agent: makeAgent({ id: "pulse", title: "Developer" }),
  is_room: false,
  joined_at: null,
  activity: { bot_id: "pulse", status: "idle", state: "idle", text: "", updated_at: null },
};
const view = () => (
  <FleetCard entry={entry} projectId="p1" selected={false} unread={0} onSelect={() => {}} />
);

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("FleetCard", () => {
  it("pops in when freshly staffed, then settles", () => {
    seedStore({ fresh: { "p1:pulse": Date.now() } });
    render(view());
    const card = screen.getByTestId("fleet-card");
    expect(card).toHaveClass("animate-pop-in");
    expect(card).toHaveAttribute("data-fresh", "true");
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(useStore.getState().fresh["p1:pulse"]).toBeUndefined();
    expect(screen.getByTestId("fleet-card")).not.toHaveClass("animate-pop-in");
  });

  it("does not animate an already-known cat and follows live status changes", () => {
    seedStore();
    render(view());
    expect(screen.getByTestId("fleet-card")).not.toHaveClass("animate-pop-in");
    expect(screen.getByText("Developer")).toBeInTheDocument();
    act(() => {
      useStore.setState({ activity: { pulse: { bot_id: "pulse", status: "coding", state: "working", text: "Writing index.html", updated_at: null } } });
    });
    expect(screen.getByTestId("fleet-card")).toHaveAttribute("data-status", "coding");
    expect(screen.getByText("Writing index.html")).toBeInTheDocument();
  });

  it("Cat Crew wears the bundled default.png and cannot be removed", () => {
    seedStore();
    const crew: RosterEntry = {
      bot_id: "crew",
      agent: makeAgent({ id: "crew", name: "Cat Crew", role: "room", title: "Orchestrator" }),
      is_room: true,
      joined_at: null,
      activity: { bot_id: "crew", status: "idle", state: "idle", text: "", updated_at: null },
    };
    const { container } = render(<FleetCard entry={crew} projectId="p1" selected={false} unread={0} onSelect={() => {}} />);
    expect(screen.getByText("Cat Crew")).toBeInTheDocument();
    expect(screen.getByText("Orchestrator")).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("src", "/avatars/default.png");
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
  });
});
