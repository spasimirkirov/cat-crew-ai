import { create } from "zustand";

import { api } from "../lib/api";
import type { ModelHealth, ModelIssue } from "../lib/types";

/**
 * Are the models chosen for each role still available? Asked once when the app opens (the engine
 * checks every host in the background at start-up), again after the user changes a role or key.
 * A failed ask is silent: the notice is a courtesy, the app works without it.
 */
interface State {
  report: ModelHealth | null;
  /** The "Model Configuration Notice" is showing. */
  noticeOpen: boolean;
  started: boolean;
  check: (refresh?: boolean) => Promise<void>;
  /** Once per session: look, and open the notice if something is wrong. */
  startup: () => Promise<void>;
  dismiss: () => void;
}

let seq = 0;

export const useModelHealth = create<State>()((set, get) => ({
  report: null,
  noticeOpen: false,
  started: false,

  check: async (refresh = false) => {
    const mine = ++seq;
    try {
      const report = await api.models.health(refresh);
      if (mine === seq) set({ report });
    } catch {
      /* keep the last report */
    }
  },

  startup: async () => {
    if (get().started) return;
    set({ started: true });
    await get().check();
    const report = get().report;
    if (report && !report.ok && report.issues.length > 0) set({ noticeOpen: true });
  },

  dismiss: () => set({ noticeOpen: false }),
}));

/** The saved model problems, keyed by wire role name. */
export const issuesByRole = (report: ModelHealth | null): Record<string, ModelIssue> => {
  const out: Record<string, ModelIssue> = {};
  for (const i of report?.issues ?? []) out[i.role] = i;
  return out;
};
