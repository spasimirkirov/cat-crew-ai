import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import type { JevSettings } from "../../lib/types";
import { JevGatekeeperPanel } from "../models/JevGatekeeperPanel";

const settings = (over: Partial<JevSettings> = {}): JevSettings => ({
  enabled: false,
  backend: "openrouter",
  guard_tool_calls: false,
  skip_tools_precheck: false,
  model: "typesafe/jev-latest",
  confidence_threshold: 0.92,
  api_key_configured: false,
  key_source: "none",
  ...over,
});

afterEach(() => vi.restoreAllMocks());

describe("JevGatekeeperPanel", () => {
  it("loads and shows the saved settings, off by default", async () => {
    vi.spyOn(api.jev, "getSettings").mockResolvedValue(settings());
    render(<JevGatekeeperPanel />);
    expect(await screen.findByText("Jev gatekeeper")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enable Jev gatekeeper" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.getByText("No key")).toBeInTheDocument();
    // No key entry, and on OpenRouter the model is Jev's fixed slug, shown but not editable.
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByRole("button", { name: /key/i })).toBeNull();
    expect(screen.getByLabelText("Model")).toHaveValue("typesafe/jev-latest");
    expect(screen.getByLabelText("Model")).toBeDisabled();
  });

  it("warns when no OpenRouter key is configured", async () => {
    vi.spyOn(api.jev, "getSettings").mockResolvedValue(settings());
    render(<JevGatekeeperPanel />);
    expect(await screen.findByText(/No OpenRouter key is configured yet/)).toBeInTheDocument();
  });

  it("shows it is riding on the OpenRouter key, with no warning, once one is configured", async () => {
    vi.spyOn(api.jev, "getSettings").mockResolvedValue(settings({ api_key_configured: true, key_source: "openrouter" }));
    render(<JevGatekeeperPanel />);
    expect(await screen.findByText("Uses OpenRouter key")).toBeInTheDocument();
    expect(screen.queryByText(/No OpenRouter key is configured yet/)).toBeNull();
  });

  it("saves when the toggle is flipped", async () => {
    vi.spyOn(api.jev, "getSettings").mockResolvedValue(settings());
    const setSettings = vi.spyOn(api.jev, "setSettings").mockResolvedValue(settings({ enabled: true }));
    render(<JevGatekeeperPanel />);
    const toggle = await screen.findByRole("switch", { name: "Enable Jev gatekeeper" });
    await act(async () => {
      toggle.click();
    });
    await waitFor(() => expect(setSettings).toHaveBeenCalledWith(expect.objectContaining({ enabled: true })));
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
  });

  it("switches Jev to TypeSafe's official API and asks for the TypeSafe key under Hosts", async () => {
    vi.spyOn(api.jev, "getSettings").mockResolvedValue(settings({ api_key_configured: true, key_source: "openrouter" }));
    const setSettings = vi
      .spyOn(api.jev, "setSettings")
      .mockResolvedValue(settings({ backend: "typesafe", model: "jev-latest", api_key_configured: false, key_source: "none" }));
    render(<JevGatekeeperPanel />);
    const host = await screen.findByLabelText("Host");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["OpenRouter", "TypeSafe (official API)"]);
    await act(async () => {
      fireEvent.change(host, { target: { value: "typesafe" } });
    });
    await waitFor(() => expect(setSettings).toHaveBeenCalledWith(expect.objectContaining({ backend: "typesafe" })));
    expect(setSettings.mock.calls[0][0]).not.toHaveProperty("api_key"); // keys never travel through Jev's settings
    expect(await screen.findByText(/No TypeSafe key is saved yet/)).toBeInTheDocument();
    expect(screen.getByLabelText("Model")).toHaveValue("jev-latest");
  });

  it("keeps the guard and the precheck off until turned on", async () => {
    vi.spyOn(api.jev, "getSettings").mockResolvedValue(settings({ enabled: true }));
    render(<JevGatekeeperPanel />);
    expect(await screen.findByRole("switch", { name: "Block tool calls Jev is sure are unsafe" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("switch", { name: "Drop tool schemas from turns that need no tools" })).toHaveAttribute("aria-checked", "false");
  });

  it("shows a load error", async () => {
    vi.spyOn(api.jev, "getSettings").mockRejectedValue(new Error("backend is down"));
    render(<JevGatekeeperPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("backend is down");
  });
});
