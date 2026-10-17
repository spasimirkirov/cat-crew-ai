import { useEffect, useMemo, useState } from "react";

import { api } from "../../lib/api";
import { isRoomId, type Agent } from "../../lib/types";
import { useStore } from "../../state/store";

/**
 * Core cats first (`is_system`), then the rest; each group keeps the backend's order.
 * Cat Crew (the orchestrator, current or legacy id) never gets a column: skills and tools are
 * assigned to specialist cats only, and the orchestrator uses the engine's built-in routing.
 */
export function orderAgents(agents: Agent[]): Agent[] {
  const cats = agents.filter((a) => !isRoomId(a.id) && a.role !== "room");
  return [...cats.filter((a) => a.is_system), ...cats.filter((a) => !a.is_system)];
}

/**
 * The columns of an assignment matrix. Reads the store's agents; when the store has
 * none yet (the page was opened before `/api/bootstrap` landed) it asks the backend.
 */
export function useMatrixAgents(): Agent[] {
  const stored = useStore((s) => s.agents);
  const storedList = useMemo(() => Object.values(stored), [stored]);
  const [fetched, setFetched] = useState<Agent[]>([]);
  const needFetch = storedList.length === 0;

  useEffect(() => {
    if (!needFetch) return;
    let cancelled = false;
    api.agents
      .list()
      .then((list) => !cancelled && setFetched(list))
      .catch(() => {
        /* the columns stay empty; the matrix explains it */
      });
    return () => {
      cancelled = true;
    };
  }, [needFetch]);

  return useMemo(() => orderAgents(needFetch ? fetched : storedList), [needFetch, fetched, storedList]);
}
