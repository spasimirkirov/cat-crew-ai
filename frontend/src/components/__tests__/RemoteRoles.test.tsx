import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../lib/api";
import type { AuthUser, UsersPayload } from "../../lib/types";
import { UsersPage } from "../../pages/Users";
import { seedStore } from "../../pages/__tests__/helpers";
import { useAuth } from "../../state/auth";
import { useModelHealth } from "../../state/modelHealth";
import { Shell } from "../Shell";

const ADA: AuthUser = { id: "u_ada", username: "ada", role: "admin", created_at: 0 };
const BOB: AuthUser = { id: "u_bob", username: "bob", role: "user", created_at: 1 };

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<div>home</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  seedStore();
  useModelHealth.setState({ report: null, noticeOpen: false, started: true });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useAuth.setState({ mode: "local", user: null, phase: "checking" });
});

describe("roles on a server", () => {
  it("a regular user sees no global settings, no Users and no Power off", () => {
    useAuth.setState({ mode: "remote", user: BOB, phase: "signed-in" });
    renderShell();
    expect(screen.queryByRole("link", { name: "Models" })).toBeNull();
    expect(screen.queryByRole("link", { name: "MCP Tools & Integrations" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Power off" })).toBeNull();
    expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Account" })).toBeInTheDocument();
  });

  it("an admin sees Models and Users; the desktop shows no Users or account", () => {
    useAuth.setState({ mode: "remote", user: ADA, phase: "signed-in" });
    const first = renderShell();
    expect(screen.getByRole("link", { name: "Models" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
    first.unmount();
    useAuth.setState({ mode: "local", user: null, phase: "signed-in" });
    renderShell();
    expect(screen.getByRole("link", { name: "Models" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Account" })).toBeNull();
  });
});

describe("UsersPage", () => {
  const payload = (ready: boolean): UsersPayload => ({
    users: [ADA, BOB],
    invites: [],
    sandbox: { ready, detail: "the Docker sandbox for user workspaces is not set up yet." },
  });

  it("lists accounts, invites an admin, and holds user invites until the sandbox is ready", async () => {
    useAuth.setState({ mode: "remote", user: ADA, phase: "signed-in" });
    vi.spyOn(api.users, "list").mockResolvedValue(payload(false));
    const invite = vi.spyOn(api.users, "invite").mockResolvedValue({ token: "t", url: "https://cats.example/#/invite/t", role: "admin" });
    render(<UsersPage />);
    expect(await screen.findAllByTestId("user-row")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Invite a user" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent("not set up yet");
    fireEvent.click(screen.getByRole("button", { name: "Invite an admin" }));
    await waitFor(() => expect(invite).toHaveBeenCalledWith("admin"));
    expect(await screen.findByText("https://cats.example/#/invite/t")).toBeInTheDocument();
  });

  it("changes a role and shows a refusal inline", async () => {
    useAuth.setState({ mode: "remote", user: ADA, phase: "signed-in" });
    vi.spyOn(api.users, "list").mockResolvedValue(payload(true));
    const setRole = vi.spyOn(api.users, "setRole").mockRejectedValue(new ApiError(422, "There must always be at least one admin."));
    render(<UsersPage />);
    fireEvent.change(await screen.findByLabelText("Role of bob"), { target: { value: "admin" } });
    await waitFor(() => expect(setRole).toHaveBeenCalledWith("u_bob", "admin"));
    expect(await screen.findByRole("alert")).toHaveTextContent("at least one admin");
  });
});
