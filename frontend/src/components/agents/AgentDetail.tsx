import { faChevronDown, faLock, faPuzzlePiece, faScrewdriverWrench, faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useEffect, useId, useState } from "react";

import { api } from "../../lib/api";
import type { Agent } from "../../lib/types";
import { errorText, useStore } from "../../state/store";
import { Avatar } from "../Avatar";
import { StatusPill } from "../StatusPill";
import { ErrorNote, Spinner } from "../Ui";

interface Props {
  agentId: string;
  onClose: () => void;
}

/**
 * One cat, read-only. The crew is defined by CatCrewAI itself (``platform/catcrew.py``) and synced
 * on every start: this panel says who the cat is, what it does and what it carries -- it is not an
 * editor. Mounted with `key={agentId}` so switching cats starts clean.
 */
export function AgentDetail({ agentId, onClose }: Props) {
  const uid = useId();
  const summary = useStore((s) => s.agents[agentId]);
  const activity = useStore((s) => s.activity[agentId]);
  const [full, setFull] = useState<Agent | null>(null); // the full record (with system_prompt)
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showPrompt, setShowPrompt] = useState(false);

  useEffect(() => {
    let alive = true;
    setFull(null);
    setLoadError(null);
    api.agents
      .get(agentId)
      .then((a) => alive && setFull(a))
      .catch((err) => alive && setLoadError(errorText(err)));
    return () => {
      alive = false;
    };
  }, [agentId]);

  const agent = full ?? summary;
  if (!agent) return null;
  const working = activity?.state === "working" && activity.text;

  return (
    <aside aria-label={`${agent.name} details`} className="flex h-full min-h-0 flex-col">
      <div className="flex items-start gap-3 border-b border-line px-5 py-4">
        <Avatar agent={agent} size={44} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-base font-semibold">{agent.name}</h2>
            {agent.is_system && <span className="chip border-violet/40 bg-violet/10 text-violet">Core cat</span>}
          </div>
          {agent.title && <p className="text-xs text-muted">{agent.title}</p>}
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <StatusPill status={activity?.status ?? "idle"} />
            {working && <span className="truncate">{activity?.text}</span>}
          </div>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close details">
          <FontAwesomeIcon icon={faXmark} />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {loadError ? (
          <ErrorNote message={loadError} />
        ) : !full ? (
          <div role="status" className="flex items-center gap-2 py-10 text-sm text-muted">
            <Spinner /> Loading {agent.name}
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            {full.description && (
              <section>
                <div className="label">What {full.name} does</div>
                <p className="text-sm leading-relaxed text-ink/90">{full.description}</p>
              </section>
            )}
            {(full.skills?.length ?? 0) > 0 && (
              <section>
                <div className="label">Strengths</div>
                <ul className="flex flex-wrap gap-1.5" aria-label="Strengths">
                  {full.skills.map((s) => (
                    <li key={s} className="chip">
                      {s}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {(full.essential_tools?.length ?? 0) > 0 && (
              <ChipGroup label="Always on" icon={faLock} items={full.essential_tools ?? []} hint="Built in for this role." />
            )}
            <ChipGroup label="Playbooks" icon={faPuzzlePiece} items={full.playbooks} hint="Assign optional playbooks on the Skills page." />
            <ChipGroup label="Letta tools" icon={faScrewdriverWrench} items={full.letta_tools} hint="Assign optional tools on the Tools page." />
            {full.system_prompt && (
              <section>
                <button
                  type="button"
                  className="label !mb-1 flex items-center gap-1.5 hover:text-ink"
                  aria-expanded={showPrompt}
                  aria-controls={`${uid}-prompt`}
                  onClick={() => setShowPrompt((v) => !v)}
                >
                  How {full.name} is instructed
                  <FontAwesomeIcon icon={faChevronDown} className={clsx("text-[10px] transition", showPrompt && "rotate-180")} />
                </button>
                {showPrompt && (
                  <pre id={`${uid}-prompt`} className="whitespace-pre-wrap rounded-lg border border-line bg-[#0b0e15] p-3 font-mono text-[11px] leading-5 text-muted">
                    {full.system_prompt}
                  </pre>
                )}
              </section>
            )}
            <p className="text-xs text-muted/70">The crew is defined by CatCrewAI; cats cannot be added, edited or removed here.</p>
          </div>
        )}
      </div>
    </aside>
  );
}

function ChipGroup({ label, icon, items, hint }: { label: string; icon: typeof faPuzzlePiece; items: string[]; hint: string }) {
  return (
    <section>
      <div className="label">{label}</div>
      {items.length === 0 ? (
        <p className="text-xs text-muted/70">None assigned. {hint}</p>
      ) : (
        <>
          <ul className="flex flex-wrap gap-1.5" aria-label={label}>
            {items.map((n) => (
              <li key={n} className="chip">
                <FontAwesomeIcon icon={icon} className="text-[10px]" />
                {n}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-xs text-muted/70">{hint}</p>
        </>
      )}
    </section>
  );
}
