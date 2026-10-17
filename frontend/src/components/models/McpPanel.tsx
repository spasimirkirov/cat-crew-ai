import { faCircleExclamation, faPlug, faRotate } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import type { McpPayload, McpServer } from "../../lib/types";

/** Which emoji stands for a cat next to "Bound to". Unknown cats get the generic one. */
const CAT_EMOJI: Record<string, string> = { pixel: "🎨", pulse: "💻" };
export const catEmoji = (id: string) => CAT_EMOJI[id.toLowerCase()] ?? "🐱";

interface Badge {
  text: string;
  tone: string;
}

/** One word for the row: Connected only when the program behind the server (Blender, Unity) really answers. */
export function badgeOf(s: McpServer): Badge {
  if (!s.enabled || s.status === "disabled") return { text: "Disabled", tone: "border-line text-muted/80" };
  if (s.status === "connected") {
    return s.ready
      ? { text: "Connected", tone: "border-emerald/40 bg-emerald/10 text-emerald" }
      : { text: `Server up · ${s.app_label} not answering`, tone: "border-warn/40 bg-warn/10 text-warn" };
  }
  if (s.status === "connecting") return { text: "Connecting…", tone: "border-warn/40 bg-warn/10 text-warn" };
  if (s.status === "reconnecting") return { text: "Reconnecting…", tone: "border-warn/40 bg-warn/10 text-warn" };
  return { text: "Offline", tone: "border-danger/40 bg-danger/10 text-danger" };
}

/** How soon to look again: quickly while something is changing, slowly when everything is settled. */
export function pollDelay(p: McpPayload | null): number {
  if (!p) return 8000;
  const on = p.servers.filter((s) => s.enabled);
  if (on.some((s) => s.status === "connecting" || s.status === "reconnecting")) return 3000;
  if (on.some((s) => s.status === "disconnected" || (s.status === "connected" && !s.ready))) return 8000;
  return 20000;
}

function detailOf(s: McpServer): string {
  if (s.status === "connected") return s.note || (s.ready ? "" : `The server runs but ${s.app_label} does not answer yet.`);
  if (s.status === "disabled" || !s.enabled) return "";
  const retry = s.next_retry_in ? ` Trying again in ${s.next_retry_in} s.` : "";
  return `${s.error || "Not connected."}${s.status === "disconnected" ? retry : ""}`;
}

function toolsText(s: McpServer): string {
  if (s.status !== "connected") return "";
  const on = s.tools_on_demand > 0 ? ` · ${s.tools_on_demand} on demand` : "";
  return `${s.tools_exposed} ${s.tools_exposed === 1 ? "tool" : "tools"}${on}`;
}

export function McpPanel() {
  const [data, setData] = useState<McpPayload | null>(null);
  const [error, setError] = useState<string | null>(null); // a refresh / reconnect that failed (announced)
  const [unreachable, setUnreachable] = useState<string | null>(null); // the status poll failed (quiet: it retries by itself)
  const [busy, setBusy] = useState<string | null>(null); // "all" or a server id
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const alive = useRef(true);
  const inFlight = useRef(false);
  const latest = useRef<McpPayload | null>(null);

  const apply = useCallback((p: McpPayload) => {
    latest.current = p;
    setData(p);
    setError(null);
    setUnreachable(null);
  }, []);

  // Poll status; the loop stops on unmount and never overlaps a manual refresh.
  useEffect(() => {
    alive.current = true;
    const ac = new AbortController();
    const tick = async () => {
      if (!inFlight.current) {
        try {
          apply(await api.mcp.servers(ac.signal));
        } catch (err) {
          if (ac.signal.aborted) return;
          setUnreachable(err instanceof Error ? err.message : "Could not read the MCP status.");
        }
      }
      if (alive.current && !ac.signal.aborted) timer.current = setTimeout(() => void tick(), pollDelay(latest.current));
    };
    void tick();
    return () => {
      alive.current = false;
      ac.abort();
      clearTimeout(timer.current);
    };
  }, [apply]);

  const run = async (who: string, call: () => Promise<McpPayload>) => {
    inFlight.current = true;
    setBusy(who);
    try {
      apply(await call());
    } catch (err) {
      setError(err instanceof Error ? err.message : "The request failed.");
    } finally {
      inFlight.current = false;
      if (alive.current) setBusy(null);
    }
  };

  const servers = data?.servers ?? [];
  const off = data !== null && !data.enabled;

  return (
    <section aria-labelledby="mcp-h" className="card flex flex-col p-5">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 id="mcp-h" className="text-sm font-semibold">
            MCP Integrations
          </h2>
          <p className="mt-1 flex items-start gap-2 text-xs text-muted">
            <FontAwesomeIcon icon={faPlug} className="mt-0.5 shrink-0" />
            Outside programs the crew can drive. Each one is bound to a single cat; nobody else sees its tools.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-sm shrink-0"
          onClick={() => void run("all", () => api.mcp.refresh())}
          disabled={busy !== null || off}
          title="Read mcp_servers.json again, re-discover every server's tools and reconnect what is down. No restart needed.">
          <FontAwesomeIcon icon={faRotate} spin={busy === "all"} /> Refresh / Reconnect MCP
        </button>
      </div>

      {error && (
        <p role="alert" className="mt-3 flex items-start gap-2 text-xs text-warn">
          <FontAwesomeIcon icon={faCircleExclamation} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}
      {unreachable && !data && <p className="mt-3 text-xs text-muted">{unreachable}</p>}
      {off && <p className="mt-3 text-xs text-muted">MCP is switched off (CATCREW_MCP=0). Unset it and restart the backend to use these integrations.</p>}
      {data && !data.sdk_installed && !off && (
        <p className="mt-3 text-xs text-warn">The “mcp” package (version 2 or newer) is missing. Run: pip install -r requirements.txt</p>
      )}
      {data?.problems.map((p) => (
        <p key={p} className="mt-2 text-xs text-warn">
          {p}
        </p>
      ))}

      {!data && !error && !unreachable && <p className="mt-4 text-xs text-muted">Checking…</p>}

      <ul className="mt-4 divide-y divide-line">
        {servers.map((s) => {
          const badge = badgeOf(s);
          const detail = detailOf(s);
          const tools = toolsText(s);
          const canFix = s.enabled && !off;
          return (
            <li key={s.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-white/[0.03] text-base" aria-hidden="true">
                {s.icon || "🔌"}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-sm font-medium">{s.label}</span>
                  <span data-testid={`mcp-badge-${s.id}`} className={clsx("chip !py-0", badge.tone)}>
                    {badge.text}
                  </span>
                </div>
                <div className="mt-0.5 text-xs text-muted">
                  {s.bound_cats.length > 0 ? (
                    <>Bound to {s.bound_cats.map((c) => `${catEmoji(c.id)} ${c.name}`).join(", ")}</>
                  ) : (
                    "Not bound to a cat"
                  )}
                  {tools && <span> · {tools}</span>}
                  <span className="text-muted/60"> · {s.transport}</span>
                </div>
                {detail && <div className={clsx("mt-1 text-xs", s.status === "connected" ? "text-warn" : "text-muted")}>{detail}</div>}
                {s.status === "disconnected" && s.enabled && s.setup_hint && <div className="mt-1 text-xs text-muted/70">{s.setup_hint}</div>}
              </div>
              <button
                type="button"
                className="btn btn-sm shrink-0"
                disabled={busy !== null || !canFix}
                onClick={() => void run(s.id, async () => (await api.mcp.reconnect(s.id)) as McpPayload)}
                aria-label={`Reconnect ${s.label}`}
                title="Close this connection and open a new one">
                <FontAwesomeIcon icon={faRotate} spin={busy === s.id} />
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
