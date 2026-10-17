import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import type { OpenRouterSettings } from "../../lib/types";
import { OpenRouterSavingsPanel } from "../models/OpenRouterSavingsPanel";

const settings = (over: Partial<OpenRouterSettings> = {}): OpenRouterSettings => ({
  provider_sort: "",
  allow_fallbacks: true,
  compression_enabled: false,
  free_tier_enabled: false,
  free_tier_model: "meta-llama/llama-3.3-70b-instruct:free",
  ...over,
});

afterEach(() => vi.restoreAllMocks());

describe("OpenRouterSavingsPanel", () => {
  it("loads and shows the saved settings", async () => {
    vi.spyOn(api.openrouter, "getSettings").mockResolvedValue(settings());
    render(<OpenRouterSavingsPanel />);
    expect(await screen.findByText("OpenRouter savings")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Provider routing" })).toHaveValue("");
    expect(screen.getByRole("switch", { name: "Allow fallback providers" })).toHaveAttribute("aria-checked", "true");
    // free-tier model field only shows once free-tier routing is on
    expect(screen.queryByPlaceholderText("meta-llama/llama-3.3-70b-instruct:free")).not.toBeInTheDocument();
  });

  it("saves when the provider sort changes", async () => {
    vi.spyOn(api.openrouter, "getSettings").mockResolvedValue(settings());
    const setSettings = vi.spyOn(api.openrouter, "setSettings").mockResolvedValue(settings({ provider_sort: "throughput" }));
    render(<OpenRouterSavingsPanel />);
    const select = await screen.findByRole("combobox", { name: "Provider routing" });
    await act(async () => {
      (select as HTMLSelectElement).value = "throughput";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await waitFor(() => expect(setSettings).toHaveBeenCalledWith(expect.objectContaining({ provider_sort: "throughput" })));
  });

  it("reveals the free-tier model field once enabled", async () => {
    vi.spyOn(api.openrouter, "getSettings").mockResolvedValue(settings());
    vi.spyOn(api.openrouter, "setSettings").mockResolvedValue(settings({ free_tier_enabled: true }));
    render(<OpenRouterSavingsPanel />);
    const toggle = await screen.findByRole("switch", { name: "Free-tier for background calls" });
    await act(async () => {
      toggle.click();
    });
    await waitFor(() => expect(screen.getByPlaceholderText("meta-llama/llama-3.3-70b-instruct:free")).toBeInTheDocument());
  });
});
