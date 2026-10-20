import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, UNAUTHORIZED_EVENT } from "../../lib/api";
import type { AuthUser } from "../../lib/types";
import { useAuth } from "../../state/auth";
import { AuthGate } from "../AuthGate";

const ADA: AuthUser = { id: "u_ada", username: "ada", role: "admin", created_at: 0 };

beforeEach(() => {
  useAuth.setState({ phase: "checking", mode: "local", needsSetup: false, user: null, error: null });
  window.location.hash = "";
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const app = () => render(<AuthGate><div>the app</div></AuthGate>);

describe("AuthGate", () => {
  it("lets the desktop (local mode) straight through", async () => {
    vi.spyOn(api.auth, "status").mockResolvedValue({ mode: "local", needs_setup: false, user: { ...ADA, id: "local", username: "local" } });
    app();
    expect(await screen.findByText("the app")).toBeInTheDocument();
  });

  it("asks a fresh server for the first admin with the console setup code", async () => {
    vi.spyOn(api.auth, "status").mockResolvedValue({ mode: "remote", needs_setup: true, user: null });
    const register = vi.spyOn(api.auth, "register").mockResolvedValue({ user: ADA });
    app();
    expect(await screen.findByRole("form", { name: "Create the admin account" })).toBeInTheDocument();
    expect(screen.queryByText("the app")).toBeNull();
    fireEvent.change(screen.getByLabelText("Setup code"), { target: { value: "ab12cd34" } });
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "ada" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-long-password" } });
    fireEvent.change(screen.getByLabelText("Repeat the password"), { target: { value: "a-long-passwor" } });
    expect(screen.getByRole("button", { name: "Create account" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Repeat the password"), { target: { value: "a-long-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(register).toHaveBeenCalledWith("ada", "a-long-password", "ab12cd34"));
    expect(await screen.findByText("the app")).toBeInTheDocument();
  });

  it("shows the login on a server with accounts, and the backend's refusal inline", async () => {
    vi.spyOn(api.auth, "status").mockResolvedValue({ mode: "remote", needs_setup: false, user: null });
    const login = vi.spyOn(api.auth, "login").mockRejectedValueOnce(new ApiError(401, "Wrong username or password.")).mockResolvedValue({ user: ADA });
    app();
    fireEvent.change(await screen.findByLabelText("Username"), { target: { value: "ada" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "a-long-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("the app")).toBeInTheDocument();
    expect(login).toHaveBeenCalledTimes(2);
  });

  it("opens an invite link as a sign-up", async () => {
    window.location.hash = "#/invite/tok_123";
    vi.spyOn(api.auth, "status").mockResolvedValue({ mode: "remote", needs_setup: false, user: null });
    vi.spyOn(api.auth, "invite").mockResolvedValue({ role: "user", expires_at: 0 });
    const accept = vi.spyOn(api.auth, "acceptInvite").mockResolvedValue({ user: { ...ADA, role: "user", username: "bob" } });
    app();
    expect(await screen.findByRole("form", { name: "Join CatCrewAI" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "bobs-long-password" } });
    fireEvent.change(screen.getByLabelText("Repeat the password"), { target: { value: "bobs-long-password" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Create account" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(accept).toHaveBeenCalledWith("tok_123", "bob", "bobs-long-password"));
    expect(await screen.findByText("the app")).toBeInTheDocument();
  });

  it("goes back to the login when the session ends (a 401 anywhere)", async () => {
    vi.spyOn(api.auth, "status").mockResolvedValue({ mode: "remote", needs_setup: false, user: ADA });
    app();
    expect(await screen.findByText("the app")).toBeInTheDocument();
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeInTheDocument();
  });
});
