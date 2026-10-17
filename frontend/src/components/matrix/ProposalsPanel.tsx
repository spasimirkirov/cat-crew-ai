import { faCheck, faWandMagicSparkles, faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useId, useState } from "react";

import type { Agent, Skill } from "../../lib/types";
import { errorText } from "../../state/store";
import { ErrorNote, Spinner } from "../Ui";

interface Props {
  proposals: Skill[];
  agents: Agent[];
  onApprove: (skillId: string, agentId: string) => Promise<void>;
  onDismiss: (skillId: string) => Promise<void>;
}

/** Skills the Hermes auto-builder noticed a cat repeating, waiting for a human decision. */
export function ProposalsPanel({ proposals, agents, onApprove, onDismiss }: Props) {
  if (proposals.length === 0) return null;
  return (
    <section aria-label="Proposed skills" className="rounded-xl border border-violet/30 bg-violet/[0.06]">
      <div className="flex items-center gap-2 px-4 pt-3 text-sm font-medium text-violet">
        <FontAwesomeIcon icon={faWandMagicSparkles} />
        Hermes noticed {proposals.length === 1 ? "a repeatable procedure" : `${proposals.length} repeatable procedures`}
        <span className="font-normal text-muted">Approve to turn them into playbooks.</span>
      </div>
      <ul className="grid max-h-[232px] gap-2 overflow-y-auto p-3 md:grid-cols-2 2xl:grid-cols-3">
        {proposals.map((p) => (
          <ProposalCard key={p.id} skill={p} agents={agents} onApprove={onApprove} onDismiss={onDismiss} />
        ))}
      </ul>
    </section>
  );
}

function ProposalCard({ skill, agents, onApprove, onDismiss }: { skill: Skill } & Omit<Props, "proposals">) {
  const selectId = useId();
  const [choosing, setChoosing] = useState(false);
  const [agentId, setAgentId] = useState("");
  const [busy, setBusy] = useState<"approve" | "dismiss" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const startChoosing = () => {
    const preferred = skill.agent_ids.find((id) => agents.some((a) => a.id === id));
    setAgentId(preferred ?? agents[0]?.id ?? "");
    setChoosing(true);
  };

  const run = async (kind: "approve" | "dismiss", fn: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(errorText(err));
      setBusy(null);
    }
  };

  return (
    <li className="flex flex-col gap-2 rounded-lg border border-line bg-card/80 p-3">
      <div className="min-w-0">
        <div className="truncate text-sm font-medium">{skill.name}</div>
        <p className="line-clamp-2 text-xs text-muted">{skill.description || "No description"}</p>
      </div>
      {choosing ? (
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={selectId} className="sr-only">
            Assign {skill.name} to
          </label>
          <select id={selectId} className="input h-8 w-auto min-w-[120px] flex-1 py-0 text-xs" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-sm btn-emerald"
            disabled={!agentId || busy !== null}
            onClick={() => run("approve", () => onApprove(skill.id, agentId))}
          >
            {busy === "approve" ? <Spinner className="text-[#04261a]" /> : <FontAwesomeIcon icon={faCheck} />}
            Assign and approve
          </button>
          <button type="button" className="btn btn-sm btn-ghost" disabled={busy !== null} onClick={() => setChoosing(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <button type="button" className="btn btn-sm" disabled={busy !== null || agents.length === 0} onClick={startChoosing}>
            <FontAwesomeIcon icon={faCheck} className="text-emerald" />
            Approve
          </button>
          <button type="button" className="btn btn-sm btn-ghost" disabled={busy !== null} onClick={() => run("dismiss", () => onDismiss(skill.id))}>
            {busy === "dismiss" ? <Spinner /> : <FontAwesomeIcon icon={faXmark} />}
            Dismiss
          </button>
        </div>
      )}
      <ErrorNote message={error} />
    </li>
  );
}
