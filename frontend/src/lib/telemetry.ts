// Pure formatting for the Telemetry tab. No React, no fetch: everything the tab prints is decided here so it can be tested.

import type { ContextUsage, MeterLevel } from "./types";

export const WARN_PERCENT = 60;
export const DANGER_PERCENT = 85;

/** Green below 60 %, yellow from 60 to 85 %, red above (the older turns are about to be summarized). */
export function levelFor(percent: number): MeterLevel {
  if (percent > DANGER_PERCENT) return "danger";
  if (percent >= WARN_PERCENT) return "warn";
  return "ok";
}

/** Fill of a bar, 0-100, whatever the input. */
export function clampPercent(used: number, limit: number): number {
  if (!Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) return 0;
  return Math.max(0, Math.min(100, (used / limit) * 100));
}

/** Share of the model's window in use, from the numbers themselves (the server's rounded `percent` is not trusted over them). */
export function contextPercent(c: Pick<ContextUsage, "tokens" | "limit">): number {
  if (!Number.isFinite(c.tokens) || !Number.isFinite(c.limit) || c.limit <= 0) return 0;
  return Math.max(0, (c.tokens / c.limit) * 100);
}

/** Where the older turns are summarized: the server's figure, else 85 % of the window. */
export function compressAt(c: Pick<ContextUsage, "limit" | "threshold">): number {
  return c.threshold && c.threshold > 0 ? c.threshold : Math.floor(c.limit * 0.85);
}

/** 4120 -> "4,120" */
export function fmtInt(n: number): string {
  return Math.round(Number.isFinite(n) ? n : 0).toLocaleString("en-US");
}

/** 42000 -> "42k", 1000000 -> "1M", 950 -> "950" (for tokens per minute, where the line is short). */
export function fmtCompact(n: number): string {
  const v = Number.isFinite(n) ? Math.max(0, n) : 0;
  const trim = (x: number) => String(Math.round(x * 10) / 10);
  if (v >= 1_000_000) return `${trim(v / 1_000_000)}M`;
  if (v >= 1_000) return `${trim(v / 1_000)}k`;
  return String(Math.round(v));
}

/** "Context: 4,120 / 8,192 tokens (50% used)" */
export function contextLine(c: Pick<ContextUsage, "tokens" | "limit">): string {
  return `Context: ${fmtInt(c.tokens)} / ${fmtInt(c.limit)} tokens (${Math.round(contextPercent(c))}% used)`;
}

/** "$0.0012" for a fraction of a cent, "$1.23" otherwise. */
export function fmtUsd(n: number): string {
  const v = Number.isFinite(n) ? Math.max(0, n) : 0;
  return v > 0 && v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
}

/** "Prompt cache: 53% hit rate (800 / 1,500 tokens) · ~48% spend saved (~$0.65)". The dollar
 * figure is omitted (never guessed as $0) when the catalog doesn't know a cached call's price. */
export function cacheLine(c: { cache_hit_pct: number; estimated_pct_spend_saved: number; cached_tokens: number; prompt_tokens: number; estimated_usd_saved?: number | null }): string {
  const usd = c.estimated_usd_saved != null ? ` (~${fmtUsd(c.estimated_usd_saved)})` : "";
  return `Prompt cache: ${c.cache_hit_pct}% hit rate (${fmtInt(c.cached_tokens)} / ${fmtInt(c.prompt_tokens)} tokens) · ~${c.estimated_pct_spend_saved}% spend saved${usd}`;
}

/** "12 interceptions · ~4.2k tokens saved (~$0.02) · 180ms avg decision" */
export function jevLine(j: { interceptions: number; tokens_saved_estimate: number; avg_decision_latency_ms: number; estimated_usd_saved?: number | null }): string {
  const usd = j.estimated_usd_saved != null ? ` (~${fmtUsd(j.estimated_usd_saved)})` : "";
  return `${j.interceptions} interception${j.interceptions === 1 ? "" : "s"} · ~${fmtCompact(j.tokens_saved_estimate)} tokens saved${usd} · ${Math.round(j.avg_decision_latency_ms)}ms avg decision`;
}

/** "VRAM: 8.9 / 16 GB | Gen Speed: 72 t/s" */
export function gpuLine(g: { vram_used_gb?: number; vram_total_gb?: number; gen_speed_tps?: number | null }): string {
  const gb = (n: number | undefined) => String(Math.round((n ?? 0) * 10) / 10);
  const speed = g.gen_speed_tps == null ? "—" : `${Math.round(g.gen_speed_tps)} t/s`;
  return `VRAM: ${gb(g.vram_used_gb)} / ${gb(g.vram_total_gb)} GB | Gen Speed: ${speed}`;
}

/** The newer of two context readings (the live event, the polled snapshot); either may be missing. */
export function newerContext(a: ContextUsage | null | undefined, b: ContextUsage | null | undefined): ContextUsage | null {
  if (!a) return b ?? null;
  if (!b) return a;
  const at = (c: ContextUsage) => (c.updated_at ? Date.parse(c.updated_at) || 0 : 0);
  return at(a) >= at(b) ? a : b;
}

export const MODEL_STATUS: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "muted" }> = {
  ready: { label: "Ready", tone: "ok" },
  offline: { label: "Offline", tone: "danger" },
  missing: { label: "Model missing", tone: "danger" },
  no_key: { label: "No API key", tone: "warn" },
  disabled: { label: "Host off", tone: "warn" },
  unknown: { label: "Not checked", tone: "muted" },
};
