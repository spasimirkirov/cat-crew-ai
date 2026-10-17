import { faKey, faMagnifyingGlass } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useState } from "react";

import type { KeyInfo } from "../../lib/types";
import { KeyBadge } from "./KeyBadge";
import { KeyModal, type KeyTarget } from "./KeyModal";

/** Keys for things that are not chat hosts (web search). All optional. */
export function ServiceKeysPanel({ services, onKeyChanged }: { services: KeyInfo[]; onKeyChanged?: (id: string) => void | Promise<void> }) {
  const [editing, setEditing] = useState<KeyTarget | null>(null);
  if (services.length === 0) return null;

  return (
    <section aria-labelledby="services-h" className="card flex flex-col p-5">
      <h2 id="services-h" className="text-sm font-semibold">
        Other services
      </h2>
      <p className="mt-1 text-xs text-muted">Optional keys for web search. Everything works without them.</p>
      <ul className="mt-4 divide-y divide-line">
        {services.map((s) => (
          <li key={s.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-white/[0.03] text-muted">
              <FontAwesomeIcon icon={faMagnifyingGlass} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-sm font-medium">{s.title}</span>
                <KeyBadge source={s.source} required={false} />
              </div>
              <div className="mt-0.5 text-xs text-muted">{s.subtitle}</div>
            </div>
            <button
              type="button"
              className="btn btn-sm shrink-0"
              onClick={() => setEditing({ id: s.id, label: s.title, hint: s.subtitle, signup: s.signup, source: s.source })}
              aria-label={`${s.source === "none" ? "Set" : "Change"} ${s.title} key`}
            >
              <FontAwesomeIcon icon={faKey} /> {s.source === "none" ? "Set key" : "Change key"}
            </button>
          </li>
        ))}
      </ul>
      {editing && <KeyModal target={editing} onClose={() => setEditing(null)} onChanged={(id) => onKeyChanged?.(id)} />}
    </section>
  );
}
