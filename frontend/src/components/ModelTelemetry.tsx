import { faMicrochip, faRotate } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { api } from "../lib/api";
import {
  MODEL_STATUS,
  cacheLine,
  clampPercent,
  compressAt,
  contextLine,
  contextPercent,
  fmtCompact,
  fmtInt,
  gpuLine,
  jevLine,
  levelFor,
  newerContext,
} from "../lib/telemetry";
import type { BalanceInfo, CacheSavings, JevTelemetry, MeterLevel, Telemetry } from "../lib/types";
import { turnFor } from "../state/reducer";
import { errorText, useStore } from "../state/store";
import { Spinner } from "./Ui";

/** How often the snapshot is re-read while the tab is open. The context gauge itself is pushed live, not polled. */
export const POLL_MS = 5_000;

const FILL: Record<MeterLevel, string> = { ok: "bg-emerald", warn: "bg-warn", danger: "bg-danger" };
const TEXT: Record<MeterLevel, string> = { ok: "text-emerald", warn: "text-warn", danger: "text-danger" };

function Bar({ percent, level, label }: { percent: number; level: MeterLevel; label: string }) {
  const value = Math.round(percent);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
      data-level={level}
      className="h-2 overflow-hidden rounded-full bg-white/[0.07]"
    >
      <div className={clsx("h-full rounded-full transition-[width] duration-500", FILL[level])} style={{ width: `${percent}%` }} />
    </div>
  );
}

function Section({ title, aside, children, testId }: { title: string; aside?: ReactNode; children: ReactNode; testId?: string }) {
  return (
    <section className="card space-y-2 p-3" data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Balance({ info, name }: { info: BalanceInfo; name: string }) {
  if (!info.configured) return null;
  const tone = info.status === "ok" ? "text-ink" : info.status === "empty" ? "text-danger" : "text-warn";
  return (
    <Section title={`${name} balance`} testId={`balance-${info.host}`}>
      {info.status === "error" ? (
        <p className="text-sm text-warn">Balance unavailable{info.message ? ` — ${info.message}` : ""}</p>
      ) : (
        <p className={clsx("text-lg font-semibold tabular-nums", tone)}>{info.display || "—"}</p>
      )}
      {info.status === "empty" && <p className="text-xs text-danger">Nothing left: calls to this host will be refused. Top it up to keep going.</p>}
    </Section>
  );
}

function CacheSavingsCard({ cache }: { cache: CacheSavings }) {
  if (!cache.calls) return null;
  return (
    <Section title="OpenRouter prompt cache" testId="cache-savings">
      <p className="text-sm font-medium tabular-nums" data-testid="cache-line">
        {cacheLine(cache)}
      </p>
      <Bar percent={clampPercent(cache.cached_tokens, cache.prompt_tokens)} level="ok" label="Cache hit rate" />
      <p className="text-[11px] text-muted">
        A rough estimate — OpenRouter does not quote an exact per-token price for this account.
      </p>
    </Section>
  );
}

function JevCard({ jev }: { jev: JevTelemetry }) {
  if (!jev.enabled) return null;
  return (
    <Section title="Jev gatekeeper" testId="jev-metrics">
      <p className="text-sm font-medium tabular-nums" data-testid="jev-line">
        {jevLine(jev)}
      </p>
      <p className="text-[11px] text-muted">
        {jev.model} · confidence threshold {Math.round(jev.confidence_threshold * 100)}%
      </p>
    </Section>
  );
}

export function ModelTelemetry({ projectId }: { projectId: string }) {
  const live = useStore((s) => s.rooms[projectId]?.context ?? null);
  // A turn starting or ending is when balances and speeds move: re-read then, on top of the timer.
  const turnId = useStore((s) => turnFor(s, projectId)?.id ?? "");
  const [data, setData] = useState<Telemetry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const ctl = new AbortController();
    const load = async () => {
      try {
        const next = await api.telemetry.live(projectId, ctl.signal);
        if (!alive) return;
        setData(next);
        setError(null);
      } catch (e) {
        if (alive && !ctl.signal.aborted) setError(errorText(e));
      }
    };
    void load();
    const timer = window.setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState !== "hidden") void load();
    }, POLL_MS);
    return () => {
      alive = false;
      ctl.abort();
      window.clearInterval(timer);
    };
  }, [projectId, turnId, tick]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  if (!data && !error) {
    return (
      <div className="flex justify-center py-10" data-testid="telemetry-loading">
        <Spinner />
      </div>
    );
  }

  const ctx = newerContext(live, data?.context);
  const usedPercent = ctx ? contextPercent(ctx) : 0;
  const level = ctx ? levelFor(usedPercent) : "ok";
  const percent = Math.min(100, usedPercent);
  const active = data?.active;
  const status = active ? MODEL_STATUS[active.status] ?? MODEL_STATUS.unknown : null;
  const gpu = data?.gpu;
  const cloudShown = Boolean(data?.openrouter.configured);

  return (
    <div className="space-y-3 p-3" data-testid="telemetry">
      {error && (
        <div role="alert" className="flex items-center justify-between gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          <span className="min-w-0 truncate" title={error}>
            Telemetry unavailable — {error}
          </span>
          <button type="button" className="btn btn-sm btn-ghost shrink-0" onClick={refresh}>
            <FontAwesomeIcon icon={faRotate} /> Retry
          </button>
        </div>
      )}

      <Section
        title="Context window"
        testId="context-gauge"
        aside={
          ctx ? (
            <span className="text-[11px] text-muted">compresses at {fmtCompact(compressAt(ctx))}</span>
          ) : data?.context_limit ? (
            <span className="text-[11px] text-muted">compresses at {fmtCompact(compressAt({ limit: data.context_limit, threshold: data.context_threshold }))}</span>
          ) : undefined
        }
      >
        {ctx ? (
          <>
            <p className={clsx("text-sm font-medium tabular-nums", TEXT[level])} data-testid="context-line">
              {contextLine(ctx)}
            </p>
            <Bar percent={percent} level={level} label="Context used" />
            <p className="truncate text-[11px] text-muted" title={`${ctx.host} · ${ctx.model}`}>
              {ctx.host ? `${ctx.host} · ` : ""}
              {ctx.model || "model"}
            </p>
            {level === "danger" && !ctx.compressed && (
              <p className="text-xs text-danger" data-testid="context-note">
                Almost full — the older turns are summarized automatically on the next call.
              </p>
            )}
            {ctx.compressed && (
              <p className="text-xs text-cyan" data-testid="context-note">
                Just compressed: older turns were summarized to make room.
              </p>
            )}
          </>
        ) : (
          <>
            <p className="text-sm text-muted" data-testid="context-window">
              {data?.context_limit
                ? `Window: ${fmtInt(data.context_limit)} tokens. Nothing measured yet — the gauge fills when a cat starts working.`
                : "Nothing measured yet — the gauge fills when a cat starts working."}
            </p>
            <Bar percent={0} level="ok" label="Context used" />
          </>
        )}
      </Section>

      {active && status && (
        <Section
          title="Active model"
          testId="active-model"
          aside={
            <span
              data-tone={status.tone}
              className={clsx(
                "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                status.tone === "ok" && "border-emerald/40 text-emerald",
                status.tone === "warn" && "border-warn/40 text-warn",
                status.tone === "danger" && "border-danger/40 text-danger",
                status.tone === "muted" && "border-line text-muted",
              )}
            >
              {status.label}
            </span>
          }
        >
          <p className="truncate text-sm" title={`${active.host_label} · ${active.model}`}>
            <span className="font-medium">{active.host_label}</span>
            <span className="text-muted"> · {active.model || "no model picked"}</span>
          </p>
          <p className="text-[11px] text-muted">
            Coding role{active.source === "project" ? " · this project's own pick" : ""}
          </p>
          {active.message && <p className="text-xs text-warn">{active.message}</p>}
        </Section>
      )}

      {data && <Balance info={data.openrouter} name="OpenRouter" />}
      {data && <CacheSavingsCard cache={data.openrouter_cache} />}
      {data && <JevCard jev={data.jev} />}

      {gpu?.available && (
        <Section
          title="Local GPU"
          testId="gpu-stats"
          aside={<FontAwesomeIcon icon={faMicrochip} className="text-muted" />}
        >
          <p className="text-sm font-medium tabular-nums" data-testid="gpu-line">
            {gpuLine(gpu)}
          </p>
          <Bar
            percent={clampPercent(gpu.vram_used_gb ?? 0, gpu.vram_total_gb ?? 0)}
            level={levelFor(clampPercent(gpu.vram_used_gb ?? 0, gpu.vram_total_gb ?? 0))}
            label="VRAM used"
          />
          <p className="truncate text-[11px] text-muted" title={gpu.name}>
            {gpu.name}
            {gpu.utilization != null ? ` · ${Math.round(gpu.utilization)}% load` : ""}
            {gpu.temperature_c != null ? ` · ${Math.round(gpu.temperature_c)}°C` : ""}
          </p>
        </Section>
      )}
      {gpu && !gpu.available && active?.local && (
        <p className="px-1 text-[11px] text-muted" data-testid="gpu-missing">
          No NVIDIA GPU detected, so there are no local GPU stats.
        </p>
      )}

      {data && !cloudShown && !active?.local && (
        <p className="px-1 text-[11px] text-muted">Add an OpenRouter key in Models to see balances and quotas here.</p>
      )}
    </div>
  );
}
