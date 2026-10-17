import { describe, expect, it } from "vitest";

import { cacheLine, clampPercent, compressAt, contextLine, contextPercent, fmtCompact, fmtInt, fmtUsd, gpuLine, jevLine, levelFor, newerContext } from "./telemetry";
import type { ContextUsage } from "./types";

const ctx = (over: Partial<ContextUsage>): ContextUsage => ({
  host: "lmstudio", model: "m", tokens: 100, system_tokens: 0, limit: 200, percent: 50, level: "ok", compressed: false, ...over,
});

describe("context gauge colours", () => {
  it("is green below 60%, yellow from 60 to 85%, red above", () => {
    expect(levelFor(0)).toBe("ok");
    expect(levelFor(59.9)).toBe("ok");
    expect(levelFor(60)).toBe("warn");
    expect(levelFor(85)).toBe("warn");
    expect(levelFor(85.1)).toBe("danger");
    expect(levelFor(130)).toBe("danger");
  });

  it("never fills outside 0-100 and survives nonsense", () => {
    expect(clampPercent(50, 100)).toBe(50);
    expect(clampPercent(500, 100)).toBe(100);
    expect(clampPercent(-5, 100)).toBe(0);
    expect(clampPercent(5, 0)).toBe(0);
    expect(clampPercent(Number.NaN, 100)).toBe(0);
  });
});

describe("telemetry text", () => {
  it("writes the context line the way the spec words it", () => {
    expect(contextLine({ tokens: 4120, limit: 8192 })).toBe("Context: 4,120 / 8,192 tokens (50% used)");
    expect(contextLine({ tokens: 10_270, limit: 64_000 })).toBe("Context: 10,270 / 64,000 tokens (16% used)");
    expect(contextLine({ tokens: 10_270, limit: 1_000_000 })).toBe("Context: 10,270 / 1,000,000 tokens (1% used)");
    expect(contextLine({ tokens: 10_160, limit: 16_384 })).toBe("Context: 10,160 / 16,384 tokens (62% used)");
  });

  it("measures against the model's window and finds where it is summarized", () => {
    expect(contextPercent({ tokens: 32_000, limit: 64_000 })).toBe(50);
    expect(contextPercent({ tokens: 5, limit: 0 })).toBe(0);
    expect(compressAt({ limit: 64_000, threshold: 54_000 })).toBe(54_000);
    expect(compressAt({ limit: 200_000 })).toBe(170_000); // an older reading without a threshold: 85 %
    expect(levelFor(contextPercent({ tokens: 10_270, limit: 64_000 }))).toBe("ok");
    expect(levelFor(contextPercent({ tokens: 10_160, limit: 16_384 }))).toBe("warn");
  });

  it("shortens token counts", () => {
    expect(fmtCompact(42_000)).toBe("42k");
    expect(fmtCompact(1_000_000)).toBe("1M");
    expect(fmtCompact(1_500)).toBe("1.5k");
    expect(fmtCompact(950)).toBe("950");
    expect(fmtCompact(-3)).toBe("0");
    expect(fmtInt(12345.6)).toBe("12,346");
  });

  it("prints OpenRouter's prompt-cache savings", () => {
    expect(cacheLine({ cache_hit_pct: 53, estimated_pct_spend_saved: 48, cached_tokens: 800, prompt_tokens: 1_500 })).toBe(
      "Prompt cache: 53% hit rate (800 / 1,500 tokens) · ~48% spend saved",
    );
  });

  it("prints the Jev gatekeeper's counters", () => {
    expect(jevLine({ interceptions: 12, tokens_saved_estimate: 4_200, avg_decision_latency_ms: 180.4 })).toBe(
      "12 interceptions · ~4.2k tokens saved · 180ms avg decision",
    );
    expect(jevLine({ interceptions: 1, tokens_saved_estimate: 0, avg_decision_latency_ms: 12 })).toBe("1 interception · ~0 tokens saved · 12ms avg decision");
  });

  it("adds the dollar figure only when the catalog priced it", () => {
    expect(cacheLine({ cache_hit_pct: 53, estimated_pct_spend_saved: 48, cached_tokens: 800, prompt_tokens: 1_500, estimated_usd_saved: 0.65 })).toBe(
      "Prompt cache: 53% hit rate (800 / 1,500 tokens) · ~48% spend saved (~$0.65)",
    );
    expect(cacheLine({ cache_hit_pct: 53, estimated_pct_spend_saved: 48, cached_tokens: 800, prompt_tokens: 1_500, estimated_usd_saved: null })).toBe(
      "Prompt cache: 53% hit rate (800 / 1,500 tokens) · ~48% spend saved",
    );
    expect(jevLine({ interceptions: 12, tokens_saved_estimate: 4_200, avg_decision_latency_ms: 180.4, estimated_usd_saved: 0.0007 })).toBe(
      "12 interceptions · ~4.2k tokens saved (~$0.0007) · 180ms avg decision",
    );
  });

  it("formats small amounts to 4 decimals, larger ones to 2", () => {
    expect(fmtUsd(0.0007)).toBe("$0.0007");
    expect(fmtUsd(1.5)).toBe("$1.50");
    expect(fmtUsd(0)).toBe("$0.00");
  });

  it("prints VRAM and generation speed, with a dash while no speed is known", () => {
    expect(gpuLine({ vram_used_gb: 8.9, vram_total_gb: 16, gen_speed_tps: 72.4 })).toBe("VRAM: 8.9 / 16 GB | Gen Speed: 72 t/s");
    expect(gpuLine({ vram_used_gb: 8.9, vram_total_gb: 16, gen_speed_tps: null })).toBe("VRAM: 8.9 / 16 GB | Gen Speed: —");
  });
});

describe("newerContext", () => {
  it("takes the reading with the later timestamp, and whichever exists", () => {
    const old = ctx({ tokens: 1, updated_at: "2026-01-01T00:00:00+00:00" });
    const fresh = ctx({ tokens: 2, updated_at: "2026-01-01T00:00:05+00:00" });
    expect(newerContext(old, fresh)?.tokens).toBe(2);
    expect(newerContext(fresh, old)?.tokens).toBe(2);
    expect(newerContext(null, old)?.tokens).toBe(1);
    expect(newerContext(fresh, undefined)?.tokens).toBe(2);
    expect(newerContext(null, null)).toBeNull();
  });
});
