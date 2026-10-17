import { faPaw } from "@fortawesome/free-solid-svg-icons";
import { useMemo, useState } from "react";

import { AgentCard } from "../components/agents/AgentCard";
import { AgentDetail } from "../components/agents/AgentDetail";
import { EmptyState, PageHeader, Spinner } from "../components/Ui";
import { useStore } from "../state/store";

/** The crew, read-only: who each cat is and what it does. The cats themselves are defined by CatCrewAI. */
export function AgentsPage() {
  const ready = useStore((s) => s.ready);
  const agentsMap = useStore((s) => s.agents);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Core cats first, then the rest; each group alphabetical.
  const agents = useMemo(
    () => Object.values(agentsMap).sort((a, b) => Number(b.is_system) - Number(a.is_system) || a.name.localeCompare(b.name)),
    [agentsMap],
  );
  const selected = selectedId && agentsMap[selectedId] ? selectedId : null;

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Agents" subtitle="The cats on your crew: who they are, what they do and what they carry." />

      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto px-8 py-6">
          {!ready ? (
            <div role="status" className="flex items-center justify-center gap-2 py-24 text-sm text-muted">
              <Spinner /> Loading agents
            </div>
          ) : agents.length === 0 ? (
            <EmptyState icon={faPaw} title="No agents yet" hint="The crew appears here once the backend has started." />
          ) : (
            <ul aria-label="Agents" className={selected ? "grid grid-cols-[repeat(auto-fill,minmax(270px,1fr))] gap-3" : "grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4"}>
              {agents.map((a) => (
                <li key={a.id} className="flex">
                  <AgentCard agent={a} selected={a.id === selected} onSelect={setSelectedId} />
                </li>
              ))}
            </ul>
          )}
        </div>

        {selected && (
          <div className="w-[440px] max-w-[46%] shrink-0 border-l border-line bg-[#0e1119]">
            <AgentDetail key={selected} agentId={selected} onClose={() => setSelectedId(null)} />
          </div>
        )}
      </div>
    </div>
  );
}
