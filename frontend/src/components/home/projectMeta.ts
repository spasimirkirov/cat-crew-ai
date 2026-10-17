// Small pure helpers for the Home launcher (no React, easy to unit test).

import type { Project } from "../../lib/types";

const PHASE_LABELS: Record<string, string> = {
  blueprint: "Blueprint",
  architecture: "Architecture",
  distribution: "Distribution",
  devops: "DevOps",
  build: "Build",
  design: "Design",
  qa: "QA",
  "qa-fix": "QA fix",
  mention: "Direct mention",
  spontaneous: "Spontaneous",
  done: "Finished",
};

/** `project.phase` -> a friendly chip label. Empty means the project has not started. */
export function phaseLabel(phase: string | null | undefined): string {
  const key = (phase ?? "").trim().toLowerCase();
  if (!key) return "Not started";
  if (PHASE_LABELS[key]) return PHASE_LABELS[key];
  const spaced = key.replace(/[-_]+/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export type PhaseTone = "idle" | "active" | "done";

export function phaseTone(phase: string | null | undefined): PhaseTone {
  const key = (phase ?? "").trim().toLowerCase();
  if (!key) return "idle";
  return key === "done" ? "done" : "active";
}

/** Newest first by `created_at`; projects without a date (or ties) fall back to name. */
export function sortProjects(projects: Project[]): Project[] {
  const ts = (p: Project) => {
    const n = p.created_at ? new Date(p.created_at).getTime() : NaN;
    return Number.isNaN(n) ? null : n;
  };
  return projects.slice().sort((a, b) => {
    const ta = ts(a);
    const tb = ts(b);
    if (ta !== null && tb !== null && ta !== tb) return tb - ta;
    if (ta !== null && tb === null) return -1;
    if (ta === null && tb !== null) return 1;
    return a.name.localeCompare(b.name);
  });
}

/** Last path segment, for both `/` and `\` separators ("Workspace" when there is none). */
export function folderBasename(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  const last = parts[parts.length - 1] ?? "";
  // "C:" is a drive, not a folder name
  return last && !/^[A-Za-z]:$/.test(last) ? last : "Workspace";
}
