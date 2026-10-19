import { useCallback, useEffect, useRef, useState } from "react";

import { errorText } from "../../state/store";

export interface Assignable {
  id: string;
  name: string;
  is_global: boolean;
  agent_ids: string[];
}

/** The one call the read-only skills/tools matrix needs. */
export interface MatrixApi<T> {
  list: () => Promise<T[]>;
}

/** Rows for a skills/tools matrix. Only the latest load wins, so a slow earlier reload cannot overwrite it. */
export function useAssignmentMatrix<T extends Assignable>(matrixApi: MatrixApi<T>) {
  const apiRef = useRef(matrixApi);
  apiRef.current = matrixApi;

  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
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

  return { items, loading, loadError, reload };
}
