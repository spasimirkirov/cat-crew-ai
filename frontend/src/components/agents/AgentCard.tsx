import { faPuzzlePiece, faScrewdriverWrench } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";

import type { Agent } from "../../lib/types";
import { useStore } from "../../state/store";
import { Avatar } from "../Avatar";
import { StatusPill } from "../StatusPill";

interface Props {
  agent: Agent;
  selected: boolean;
  onSelect: (id: string) => void;
}

export function AgentCard({ agent, selected, onSelect }: Props) {
  const status = useStore((s) => s.activity[agent.id]?.status ?? "idle");
  const nPlaybooks = agent.playbooks.length;
  const nTools = agent.letta_tools.length;

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`Open ${agent.name}`}
      onClick={() => onSelect(agent.id)}
      className={clsx(
        "card flex w-full flex-col gap-3 p-4 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60",
        selected ? "border-cyan/60 bg-[#151a26] shadow-glow" : "hover:border-[#343a52] hover:bg-[#151926]",
      )}
    >
      <span className="flex items-start gap-3">
        <Avatar agent={agent} size={40} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] font-semibold leading-tight">{agent.name}</span>
          <span className="mt-0.5 block truncate text-xs text-muted">{agent.title || agent.role}</span>
        </span>
        <StatusPill status={status} className="shrink-0" />
      </span>

      {agent.description && <span className="line-clamp-2 text-[13px] leading-5 text-muted">{agent.description}</span>}

      <span className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted">
        {agent.is_system && <span className="chip border-violet/40 bg-violet/10 text-violet">Core cat</span>}
        <span className="inline-flex items-center gap-1.5" title="Assigned playbooks">
          <FontAwesomeIcon icon={faPuzzlePiece} className="text-[11px]" />
          {nPlaybooks} {nPlaybooks === 1 ? "playbook" : "playbooks"}
        </span>
        <span className="inline-flex items-center gap-1.5" title="Assigned Letta tools">
          <FontAwesomeIcon icon={faScrewdriverWrench} className="text-[11px]" />
          {nTools} {nTools === 1 ? "tool" : "tools"}
        </span>
      </span>
    </button>
  );
}
