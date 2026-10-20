import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../lib/api";
import type { Checkpoint } from "../../lib/types";
import { CheckpointsPanel } from "../room/CheckpointsPanel";

const CP: Checkpoint = {
  id: "20261020-101500-abc123",
  label: "Restyle the navigation",
  created_at: Date.now() / 1000 - 120,
  project_id: "p1",
  agent_id: "pixel",
  files: ["resources/views/layout.blade.php", "resources/css/app.css"],
  commands: ["run_command: npm run build"],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CheckpointsPanel", () => {
  it("renders nothing when there is no checkpoint (or the list fails)", async () => {
    const list = vi.spyOn(api.checkpoints, "list").mockRejectedValue(new ApiError(500, "boom"));
    const { container } = render(<CheckpointsPanel projectId="p1" refreshKey="0" />);
    await waitFor(() => expect(list).toHaveBeenCalledWith("p1"));
    expect(container).toBeEmptyDOMElement();
  });

  it("lists turns, shows their files on See more, and restores after a confirmation", async () => {
    vi.spyOn(api.checkpoints, "list").mockResolvedValueOnce([CP]).mockResolvedValue([]);
    const restore = vi.spyOn(api.checkpoints, "restore").mockResolvedValue({
      undone: [CP.id],
      files: ["restored resources/views/layout.blade.php", "restored resources/css/app.css"],
      not_undone: ["run_command: npm run build"],
    });
    render(<CheckpointsPanel projectId="p1" refreshKey="0" />);
    const row = await screen.findByTestId("checkpoint-row");
    expect(within(row).getByText("Restyle the navigation")).toBeInTheDocument();
    expect(within(row).getByText(/2 files, 1 command/)).toBeInTheDocument();

    fireEvent.click(within(row).getByText(/See more/));
    expect(within(row).getByText("resources/css/app.css")).toBeInTheDocument();
    expect(within(row).getByText("$ run_command: npm run build")).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: "Restore to before: Restyle the navigation" }));
    expect(restore).not.toHaveBeenCalled();
    expect(within(row).getByText(/Commands that ran .* are not undone/)).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(restore).toHaveBeenCalledWith("p1", CP.id));
    expect(await screen.findByRole("status")).toHaveTextContent("Restored 2 file change(s) from 1 turn(s). 1 command(s) were not undone.");
  });

  it("shows the backend's refusal inline", async () => {
    vi.spyOn(api.checkpoints, "list").mockResolvedValue([CP]);
    vi.spyOn(api.checkpoints, "restore").mockRejectedValue(new ApiError(409, "The crew is still working on this project: stop it first, then restore."));
    render(<CheckpointsPanel projectId="p1" refreshKey="0" />);
    fireEvent.click(await screen.findByRole("button", { name: /Restore to before/ }));
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("stop it first");
  });
});
