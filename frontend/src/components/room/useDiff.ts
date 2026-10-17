import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import type { DiffBody } from "../../lib/types";

const cache = new Map<string, DiffBody>();
const inflight = new Map<string, Promise<DiffBody>>();

function load(uuid: string): Promise<DiffBody> {
  const hit = cache.get(uuid);
  if (hit) return Promise.resolve(hit);
  let p = inflight.get(uuid);
  if (!p) {
    p = api.tasks
      .diff(uuid)
      .then((d) => {
        cache.set(uuid, d);
        return d;
      })
      .finally(() => inflight.delete(uuid));
    inflight.set(uuid, p);
  }
  return p;
}

/** Lazily fetch one diff body (cached for the session; a diff never changes once written). */
export function useDiff(uuid: string, enabled = true): { diff: DiffBody | null; error: string | null } {
  const [diff, setDiff] = useState<DiffBody | null>(() => cache.get(uuid) ?? null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled || diff) return;
    let live = true;
    load(uuid)
      .then((d) => live && setDiff(d))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [uuid, enabled, diff]);
  return { diff, error };
}

export function clearDiffCache(): void {
  cache.clear();
  inflight.clear();
}
