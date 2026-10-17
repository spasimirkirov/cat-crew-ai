import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import type { ModelHealth, ModelIssue } from "../../lib/types";
import { seedStore, user } from "../../pages/__tests__/helpers";
import { useModelHealth } from "../../state/modelHealth";
import { Shell } from "../Shell";

const issue = (role: string, roleLabel: string, model: string): ModelIssue => ({
  role,
  role_label: roleLabel,
  host: "gemini",
  host_label: "Google Gemini",
  model,
  reason: "missing",
  message: `The model '${model}' assigned to '${roleLabel}' is no longer available on Google Gemini.`,
});

const report = (issues: ModelIssue[]): ModelHealth => ({ checked_at: 1, ok: issues.length === 0, hosts: {}, issues });

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname + loc.search}</div>;
}

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Where />} />
          <Route path="models" element={<Where />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  seedStore();
  useModelHealth.setState({ report: null, noticeOpen: false, started: false });
});
afterEach(() => vi.restoreAllMocks());

describe("Model Configuration Notice", () => {
  it("opens on launch and names every role and model that is gone", async () => {
    vi.spyOn(api.models, "health").mockResolvedValue(report([issue("memory", "Memory", "gemini-1.0-pro"), issue("coding", "Coding", "old-coder")]));
    renderShell();
    const dialog = await screen.findByRole("dialog", { name: "Model Configuration Notice" });
    expect(within(dialog).getByText("The model 'gemini-1.0-pro' assigned to 'Memory' is no longer available on Google Gemini.")).toBeInTheDocument();
    expect(within(dialog).getByText(/'old-coder' assigned to 'Coding'/)).toBeInTheDocument();
    expect(screen.getByTestId("models-issue-badge")).toHaveTextContent("2");
  });

  it("Re-assign Models opens the Models page with the broken roles named", async () => {
    vi.spyOn(api.models, "health").mockResolvedValue(report([issue("memory", "Memory", "gemini-1.0-pro"), issue("coding", "Coding", "old-coder")]));
    renderShell();
    await user.click(await screen.findByRole("button", { name: "Re-assign Models" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/models?fix=memory%2Ccoding"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Dismiss closes it and it does not come back within the session", async () => {
    const health = vi.spyOn(api.models, "health").mockResolvedValue(report([issue("vision", "Vision", "qwen-vl-gone")]));
    const { unmount } = renderShell();
    await user.click(await screen.findByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("models-issue-badge")).toBeInTheDocument(); // the nav still flags it
    unmount();
    renderShell(); // e.g. the shell remounts: no second notice
    await waitFor(() => expect(screen.getByTestId("where")).toBeInTheDocument());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(health).toHaveBeenCalledTimes(1);
  });

  it("stays quiet when every chosen model is available", async () => {
    const health = vi.spyOn(api.models, "health").mockResolvedValue(report([]));
    renderShell();
    await waitFor(() => expect(health).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByTestId("models-issue-badge")).toBeNull();
  });

  it("stays quiet when the check itself cannot be made", async () => {
    vi.spyOn(api.models, "health").mockRejectedValue(new Error("offline"));
    renderShell();
    await waitFor(() => expect(useModelHealth.getState().started).toBe(true));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
