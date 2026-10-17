import { faCloud, faKey, faRotate, faServer } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";

import { useState } from "react";

import type { KeyInfo, KeySource, ModelHost } from "../../lib/types";
import { KeyBadge } from "./KeyBadge";
import { KeyModal, type KeyTarget } from "./KeyModal";
import type { Probe } from "./modelsUtil";

function probeText(p?: Probe): { text: string; tone: string } {
  if (!p) return { text: "Not checked", tone: "text-muted/70" };
  if (p.status === "loading") return { text: "Checking...", tone: "text-muted" };
  if (p.status === "error") return { text: "Could not check", tone: "text-warn" };
  if (!p.data?.online) return { text: "Offline", tone: "text-warn" };
  const n = p.data.models.length;
  return { text: `Online, ${n} ${n === 1 ? "model" : "models"}`, tone: "text-emerald" };
}

const sourceOf = (h: ModelHost): KeySource => h.key_source ?? (h.configured ? "saved" : "none");

interface Props {
  hosts: ModelHost[];
  probes: Record<string, Probe>;
  /** Extra detail per key id (hint, where to get one); optional, the panel works without it. */
  keys?: Record<string, KeyInfo>;
  onRefresh: (id: string) => void;
  /** A key was saved or removed: reload what depends on it. */
  onKeyChanged?: (id: string) => void | Promise<void>;
}

export function HostsPanel({ hosts, probes, keys = {}, onRefresh, onKeyChanged }: Props) {
  const [editing, setEditing] = useState<KeyTarget | null>(null);
  const edit = (h: ModelHost) => setEditing({ id: h.id, label: h.label, hint: keys[h.id]?.subtitle, signup: keys[h.id]?.signup, source: sourceOf(h) });

  return (
    <section aria-labelledby="hosts-h" className="card flex flex-col p-5">
      <h2 id="hosts-h" className="text-sm font-semibold">
        Hosts
      </h2>
      <p className="mt-1 flex items-start gap-2 text-xs text-muted">
        <FontAwesomeIcon icon={faKey} className="mt-0.5 shrink-0" />
        Keys are stored on this computer and never shown again. Use Set key to add or replace one.
      </p>

      <ul className="mt-4 divide-y divide-line">
        {hosts.map((h) => {
          const source = sourceOf(h);
          // a cloud host without a key is never probed: say so instead of "Offline"
          const pr = probeText(h.cloud && source === "none" ? undefined : probes[h.id]);
          const checking = probes[h.id]?.status === "loading";
          return (
            <li key={h.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-white/[0.03] text-muted">
                <FontAwesomeIcon icon={h.cloud ? faCloud : faServer} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-sm font-medium">{h.label}</span>
                  <span className={clsx("chip !py-0", h.enabled ? "border-emerald/40 text-emerald" : "text-muted/80")}>{h.enabled ? "Enabled" : "Disabled"}</span>
                  <KeyBadge source={source} required={h.cloud} />
                </div>
                <div className={clsx("mt-0.5 text-xs", pr.tone)}>{pr.text}</div>
              </div>
              <button
                type="button"
                className={clsx("btn btn-sm shrink-0", h.cloud && source === "none" && "btn-primary")}
                onClick={() => edit(h)}
                aria-label={`${source === "none" ? "Set" : "Change"} ${h.label} key`}
              >
                <FontAwesomeIcon icon={faKey} /> {source === "none" ? "Set key" : "Change key"}
              </button>
              <button type="button" className="btn btn-sm shrink-0" onClick={() => onRefresh(h.id)} disabled={checking} aria-label={`Refresh ${h.label}`} title="Refresh">
                <FontAwesomeIcon icon={faRotate} spin={checking} />
              </button>
            </li>
          );
        })}
      </ul>
      {editing && <KeyModal target={editing} onClose={() => setEditing(null)} onChanged={(id) => onKeyChanged?.(id)} />}
    </section>
  );
}
