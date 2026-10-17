import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../lib/api";
import type { ContextUsage, Telemetry } from "../../lib/types";
import { emptyRoom } from "../../state/reducer";
import { useStore } from "../../state/store";
import { makeTelemetry, mockTelemetry, seedStore } from "../../pages/__tests__/helpers";
import { ModelTelemetry, POLL_MS } from "../ModelTelemetry";

const reading = (percent: number, over: Partial<ContextUsage> = {}): ContextUsage => ({
  host: "lmstudio", model: "qwen-coder", tokens: Math.round((8192 * percent) / 100), system_tokens: 900, limit: 8192, percent,
  level: "ok", compressed: false, updated_at: "2026-01-01T00:00:00+00:00", ...over,
});

async function show(snapshot: Telemetry = makeTelemetry()) {
  mockTelemetry(snapshot);
  render(<ModelTelemetry projectId="p1" />);
  return screen.findByTestId("telemetry");
}

beforeEach(() => seedStore({ rooms: { p1: { ...emptyRoom(), loaded: true } } }));
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("context gauge", () => {
  it("says so while nothing has been measured", async () => {
    await show();
    expect(screen.getByText(/Nothing measured yet/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Context used" })).toHaveAttribute("aria-valuenow", "0");
  });

  it("prints the line and fills green below 60%", async () => {
    await show(makeTelemetry({ context: reading(50) }));
    expect(screen.getByTestId("context-line")).toHaveTextContent("Context: 4,096 / 8,192 tokens (50% used)");
    const bar = screen.getByRole("progressbar", { name: "Context used" });
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(bar).toHaveAttribute("data-level", "ok");
    expect(screen.queryByTestId("context-note")).not.toBeInTheDocument();
  });

  it("turns yellow from 60% and red above 85%, and warns that compression is near", async () => {
    mockTelemetry(makeTelemetry({ context: reading(72) }));
    const first = render(<ModelTelemetry projectId="p1" />);
    await screen.findByTestId("telemetry");
    expect(screen.getByRole("progressbar", { name: "Context used" })).toHaveAttribute("data-level", "warn");
    first.unmount();

    mockTelemetry(makeTelemetry({ context: reading(91) }));
    render(<ModelTelemetry projectId="p1" />);
    await screen.findByTestId("telemetry");
    expect(screen.getByRole("progressbar", { name: "Context used" })).toHaveAttribute("data-level", "danger");
    expect(screen.getByTestId("context-note")).toHaveTextContent(/summarized automatically/);
  });

  it("says when the reading was taken right after a compression", async () => {
    await show(makeTelemetry({ context: reading(30, { compressed: true }) }));
    expect(screen.getByTestId("context-note")).toHaveTextContent(/Just compressed/);
  });

  it("follows the live context.usage reading over an older polled one, and ignores a stale live one", async () => {
    useStore.setState({ rooms: { p1: { ...emptyRoom(), loaded: true, context: reading(88, { tokens: 7200, updated_at: "2026-01-01T00:00:09+00:00" }) } } });
    await show(makeTelemetry({ context: reading(20, { tokens: 1638, updated_at: "2026-01-01T00:00:01+00:00" }) }));
    expect(screen.getByTestId("context-line")).toHaveTextContent("Context: 7,200 / 8,192 tokens (88% used)");
    expect(screen.getByRole("progressbar", { name: "Context used" })).toHaveAttribute("data-level", "danger");

    // the store gets a fresher reading without another request
    act(() => {
      useStore.setState({ rooms: { p1: { ...emptyRoom(), loaded: true, context: reading(40, { tokens: 3277, updated_at: "2026-01-01T00:00:20+00:00" }) } } });
    });
    expect(screen.getByTestId("context-line")).toHaveTextContent("3,277 / 8,192 tokens (40% used)");
    expect(screen.getByRole("progressbar", { name: "Context used" })).toHaveAttribute("data-level", "ok");
  });
});

describe("context gauge on each provider's real scale", () => {
  const at = (tokens: number, limit: number, over: Partial<ContextUsage> = {}): ContextUsage => ({
    host: "deepseek", model: "deepseek-v4-pro", tokens, system_tokens: 900, limit, threshold: over.threshold ?? ({ 64_000: 54_000, 1_000_000: 850_000, 16_384: 14_000 } as Record<number, number>)[limit] ?? Math.floor(limit * 0.85),
    percent: 0, level: "ok", compressed: false, updated_at: "2026-01-01T00:00:00+00:00", ...over,
  });
  const bar = () => screen.getByRole("progressbar", { name: "Context used" });

  it("DeepSeek 64k: 10,270 tokens is 16% and green", async () => {
    await show(makeTelemetry({ context: at(10_270, 64_000) }));
    expect(screen.getByTestId("context-line")).toHaveTextContent("Context: 10,270 / 64,000 tokens (16% used)");
    expect(bar()).toHaveAttribute("data-level", "ok");
    expect(screen.getByTestId("context-gauge")).toHaveTextContent("compresses at 54k");
  });

  it("Gemini 1M: 10,270 tokens is 1% and green", async () => {
    await show(makeTelemetry({ context: at(10_270, 1_000_000, { host: "gemini", model: "gemini-2.5-pro" }) }));
    expect(screen.getByTestId("context-line")).toHaveTextContent("Context: 10,270 / 1,000,000 tokens (1% used)");
    expect(bar()).toHaveAttribute("data-level", "ok");
    expect(screen.getByTestId("context-gauge")).toHaveTextContent("compresses at 850k");
  });

  it("a 16,384 local window: 10,160 tokens is 62% and yellow", async () => {
    await show(makeTelemetry({ context: at(10_160, 16_384, { host: "lmstudio", model: "qwen-coder" }) }));
    expect(screen.getByTestId("context-line")).toHaveTextContent("Context: 10,160 / 16,384 tokens (62% used)");
    expect(bar()).toHaveAttribute("data-level", "warn");
    expect(screen.queryByTestId("context-note")).not.toBeInTheDocument();
  });

  it("warns only above 85% of that model's window", async () => {
    mockTelemetry(makeTelemetry({ context: at(54_000, 64_000) })); // 84%: yellow, no warning
    const first = render(<ModelTelemetry projectId="p1" />);
    await screen.findByTestId("telemetry");
    expect(bar()).toHaveAttribute("data-level", "warn");
    expect(screen.queryByTestId("context-note")).not.toBeInTheDocument();
    first.unmount();

    mockTelemetry(makeTelemetry({ context: at(56_000, 64_000) })); // 87.5%
    render(<ModelTelemetry projectId="p1" />);
    await screen.findByTestId("telemetry");
    expect(bar()).toHaveAttribute("data-level", "danger");
    expect(screen.getByTestId("context-note")).toHaveTextContent(/Almost full/);
  });

  it("uses the model's own window before anything is measured", async () => {
    await show(makeTelemetry({ context_limit: 128_000, context_threshold: 110_000 }));
    expect(screen.getByTestId("context-window")).toHaveTextContent("Window: 128,000 tokens");
    expect(screen.getByTestId("context-gauge")).toHaveTextContent("compresses at 110k");
  });

  it("derives the percentage from the numbers, not from a stale field", async () => {
    await show(makeTelemetry({ context: at(32_000, 64_000, { percent: 3, level: "ok" }) }));
    expect(screen.getByTestId("context-line")).toHaveTextContent("(50% used)");
    expect(bar()).toHaveAttribute("aria-valuenow", "50");
  });
});

describe("model and balances", () => {
  it("shows the active model and whether its host answers", async () => {
    await show(
      makeTelemetry({ active: { role: "coding", host: "openrouter", host_label: "OpenRouter", model: "deepseek/deepseek-chat", source: "project", local: false, status: "no_key", message: "It needs an OpenRouter API key." } }),
    );
    const card = screen.getByTestId("active-model");
    expect(card).toHaveTextContent("OpenRouter");
    expect(card).toHaveTextContent("deepseek/deepseek-chat");
    expect(card).toHaveTextContent("No API key");
    expect(card).toHaveTextContent("this project's own pick");
    expect(card).toHaveTextContent("It needs an OpenRouter API key.");
  });

  it("shows the OpenRouter balance, an error, and an empty wallet", async () => {
    await show(makeTelemetry({ openrouter: { host: "openrouter", configured: true, status: "ok", balance: 12.45, currency: "USD", display: "$12.45 remaining" } }));
    expect(screen.getByTestId("balance-openrouter")).toHaveTextContent("$12.45 remaining");
  });

  it("explains a balance that cannot be read and one that ran out", async () => {
    mockTelemetry(makeTelemetry({ openrouter: { host: "openrouter", configured: true, status: "error", message: "The key was rejected." } }));
    const first = render(<ModelTelemetry projectId="p1" />);
    await first.findByTestId("telemetry");
    expect(screen.getByTestId("balance-openrouter")).toHaveTextContent("Balance unavailable — The key was rejected.");
    first.unmount();

    await show(makeTelemetry({ openrouter: { host: "openrouter", configured: true, status: "empty", balance: 0, currency: "USD", display: "$0.00 remaining" } }));
    expect(screen.getByTestId("balance-openrouter")).toHaveTextContent("$0.00 remaining");
    expect(screen.getByTestId("balance-openrouter")).toHaveTextContent(/Nothing left/);
  });
});

describe("prompt cache and Jev gatekeeper", () => {
  it("prints the prompt-cache savings once there have been calls", async () => {
    await show(
      makeTelemetry({
        openrouter_cache: { host: "openrouter", calls: 4, prompt_tokens: 1_500, cached_tokens: 800, cache_hit_pct: 53, estimated_pct_spend_saved: 48, estimated_usd_saved: null },
      }),
    );
    expect(screen.getByTestId("cache-line")).toHaveTextContent("Prompt cache: 53% hit rate (800 / 1,500 tokens) · ~48% spend saved");
    expect(screen.getByRole("progressbar", { name: "Cache hit rate" })).toHaveAttribute("aria-valuenow", "53");
  });

  it("adds the dollar figure once the catalog can price the model", async () => {
    await show(
      makeTelemetry({
        openrouter_cache: { host: "openrouter", calls: 4, prompt_tokens: 1_500, cached_tokens: 800, cache_hit_pct: 53, estimated_pct_spend_saved: 48, estimated_usd_saved: 0.65 },
      }),
    );
    expect(screen.getByTestId("cache-line")).toHaveTextContent("~48% spend saved (~$0.65)");
  });

  it("says nothing about the cache before any call was made", async () => {
    await show();
    expect(screen.queryByTestId("cache-savings")).not.toBeInTheDocument();
  });

  it("shows Jev's counters when it is enabled, and nothing when it is off", async () => {
    await show(
      makeTelemetry({
        jev: {
          enabled: true,
          model: "jev-latest",
          confidence_threshold: 0.92,
          precheck_calls: 20,
          bypasses: 8,
          guard_calls: 4,
          blocked_tool_calls: 4,
          interceptions: 12,
          tokens_saved_estimate: 4_200,
          avg_decision_latency_ms: 180,
          estimated_usd_saved: null,
        },
      }),
    );
    expect(screen.getByTestId("jev-line")).toHaveTextContent("12 interceptions · ~4.2k tokens saved · 180ms avg decision");
    expect(screen.getByTestId("jev-metrics")).toHaveTextContent("jev-latest · confidence threshold 92%");
  });

  it("stays quiet when Jev is disabled", async () => {
    await show();
    expect(screen.queryByTestId("jev-metrics")).not.toBeInTheDocument();
  });
});

describe("gpu", () => {
  it("shows VRAM and generation speed of the local GPU", async () => {
    await show(
      makeTelemetry({ gpu: { available: true, name: "RTX 4080", vram_used_gb: 8.9, vram_total_gb: 16, utilization: 71, temperature_c: 63, gen_speed_tps: 72 } }),
    );
    expect(screen.getByTestId("gpu-line")).toHaveTextContent("VRAM: 8.9 / 16 GB | Gen Speed: 72 t/s");
    expect(screen.getByTestId("gpu-stats")).toHaveTextContent("RTX 4080 · 71% load · 63°C");
    expect(screen.getByRole("progressbar", { name: "VRAM used" })).toHaveAttribute("aria-valuenow", "56");
  });

  it("says so for a local model without an NVIDIA GPU, and stays quiet for a cloud one", async () => {
    await show();
    expect(screen.getByTestId("gpu-missing")).toBeInTheDocument();
  });

  it("stays quiet about the GPU when the model runs in the cloud", async () => {
    await show(
      makeTelemetry({ active: { role: "coding", host: "openrouter", host_label: "OpenRouter", model: "m", source: "global", local: false, status: "ready", message: "" } }),
    );
    expect(screen.queryByTestId("gpu-missing")).not.toBeInTheDocument();
    expect(screen.getByText(/Add an OpenRouter key/)).toBeInTheDocument();
  });
});

describe("polling", () => {
  it("reports a failure without losing the page, and Retry asks again", async () => {
    const live = vi.spyOn(api.telemetry, "live").mockRejectedValueOnce(new Error("backend is down")).mockResolvedValue(makeTelemetry({ context: reading(50) }));
    render(<ModelTelemetry projectId="p1" />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Telemetry unavailable — backend is down");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    });
    expect(await screen.findByTestId("context-line")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(live).toHaveBeenCalledTimes(2);
    expect(live.mock.calls[0][0]).toBe("p1");
  });

  it("re-reads on a timer while open, and stops when closed", async () => {
    vi.useFakeTimers();
    const live = mockTelemetry(makeTelemetry());
    const { unmount } = render(<ModelTelemetry projectId="p1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(live).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(live).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 2);
    });
    expect(live).toHaveBeenCalledTimes(4);
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 5);
    });
    expect(live).toHaveBeenCalledTimes(4);
  });

  it("re-reads when a turn of this project starts or ends (that is when balances and speeds move)", async () => {
    const live = mockTelemetry(makeTelemetry());
    render(<ModelTelemetry projectId="p1" />);
    await screen.findByTestId("telemetry");
    expect(live).toHaveBeenCalledTimes(1);
    await act(async () => {
      useStore.setState({ currentTurn: { id: "t1", project_id: "p1", bot_id: "crew", started_at: "", phase: "", kind: "room" } });
    });
    expect(live).toHaveBeenCalledTimes(2);
    await act(async () => {
      useStore.setState({ currentTurn: { id: "t9", project_id: "other", bot_id: "crew", started_at: "", phase: "", kind: "room" } });
    });
    expect(live).toHaveBeenCalledTimes(3); // the turn left this project: back to "no turn here"
    await act(async () => {
      useStore.setState({ currentTurn: null });
    });
    expect(live).toHaveBeenCalledTimes(3); // still "no turn here": nothing changed for this project
  });
});
