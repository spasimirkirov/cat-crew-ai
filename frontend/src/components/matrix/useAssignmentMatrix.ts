import { useCallback, useEffect, useRef, useState } from "react";

import { errorText } from "../../state/store";

export interface Assignable {
  id: string;
  name: string;
  is_global: boolean;
  agent_ids: string[];
}

/** The three calls every assignment matrix needs (skills and tools share the shape). */
export interface MatrixApi<T> {
  list: () => Promise<T[]>;
  assign: (id: string, agentIds: string[]) => Promise<T>;
  setGlobal: (id: string, on: boolean) => Promise<T>;
}

/**
 * Rows + optimistic assignment state for a skills/tools matrix.
 * A toggle updates the row immediately, sends the FULL agent-id list, and rolls the row
 * back (with an inline error) if the backend refuses. A row accepts one change at a time.
 */
export function useAssignmentMatrix<T extends Assignable>(matrixApi: MatrixApi<T>) {
  const apiRef = useRef(matrixApi);
  apiRef.current = matrixApi;

  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const pendingRef = useRef(new Set<string>());
  const itemsRef = useRef<T[]>([]);
  itemsRef.current = items;
  const seq = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const list = await apiRef.current.list();
      if (mine !== seq.current) return;
      setItems(list);
      setLoadError(null);
    } catch (err) {
      if (mine !== seq.current) return;
      setLoadError(errorText(err));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const patchRow = useCallback((id: string, fn: (row: T) => T) => {
    setItems((list) => list.map((r) => (r.id === id ? fn(r) : r)));
  }, []);

  const mutate = useCallback(
    async (row: T, optimistic: Pick<Assignable, "is_global" | "agent_ids">, call: () => Promise<T>) => {
      if (pendingRef.current.has(row.id)) return;
      pendingRef.current.add(row.id);
      setPending(new Set(pendingRef.current));
      setActionError(null);
      const before = { is_global: row.is_global, agent_ids: row.agent_ids };
      patchRow(row.id, (r) => ({ ...r, ...optimistic }));
      try {
        const res = await call();
        if (res && typeof res === "object") {
          patchRow(row.id, (r) => ({
            ...r,
            is_global: typeof res.is_global === "boolean" ? res.is_global : r.is_global,
            agent_ids: Array.isArray(res.agent_ids) ? res.agent_ids : r.agent_ids,
          }));
        }
      } catch (err) {
        patchRow(row.id, (r) => ({ ...r, ...before }));
        setActionError(`Could not update ${row.name}: ${errorText(err)}`);
      } finally {
        pendingRef.current.delete(row.id);
        setPending(new Set(pendingRef.current));
      }
    },
    [patchRow],
  );

  const toggleAgent = useCallback(
    (id: string, agentId: string) => {
      const row = itemsRef.current.find((r) => r.id === id);
      if (!row || row.is_global) return;
      const next = row.agent_ids.includes(agentId) ? row.agent_ids.filter((a) => a !== agentId) : [...row.agent_ids, agentId];
      return mutate(row, { is_global: row.is_global, agent_ids: next }, () => apiRef.current.assign(id, next));
    },
    [mutate],
  );

  const toggleGlobal = useCallback(
    (id: string, on: boolean) => {
      const row = itemsRef.current.find((r) => r.id === id);
      if (!row) return;
      return mutate(row, { is_global: on, agent_ids: row.agent_ids }, () => apiRef.current.setGlobal(id, on));
    },
    [mutate],
  );

  const dismissError = useCallback(() => setActionError(null), []);

  return { items, loading, loadError, actionError, pending, reload, toggleAgent, toggleGlobal, dismissError };
}
