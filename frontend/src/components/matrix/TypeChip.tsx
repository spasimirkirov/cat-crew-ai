import clsx from "clsx";

import type { SkillType } from "../../lib/types";

const STYLE: Record<SkillType, { label: string; cls: string }> = {
  bundled: { label: "bundled", cls: "border-cyan/30 bg-cyan/10 text-cyan" },
  custom: { label: "custom", cls: "border-emerald/30 bg-emerald/10 text-emerald" },
  marketplace: { label: "marketplace", cls: "border-violet/30 bg-violet/10 text-violet" },
  auto_generated: { label: "auto", cls: "border-warn/30 bg-warn/10 text-warn" },
};

export function typeLabel(t: SkillType | string): string {
  return STYLE[t as SkillType]?.label ?? String(t);
}

export function TypeChip({ type }: { type: SkillType | string }) {
  const s = STYLE[type as SkillType];
  return <span className={clsx("chip", s?.cls)}>{s?.label ?? type}</span>;
}
