import clsx from "clsx";

import type { Status } from "../lib/types";

const STYLE: Record<Status, { label: string; dot: string; text: string; ring: string; pulse: boolean }> = {
  idle: { label: "Idle", dot: "bg-muted/60", text: "text-muted", ring: "border-line", pulse: false },
  thinking: { label: "Thinking", dot: "bg-violet", text: "text-violet", ring: "border-violet/40", pulse: true },
  coding: { label: "Coding", dot: "bg-cyan", text: "text-cyan", ring: "border-cyan/40", pulse: true },
  testing: { label: "Testing", dot: "bg-emerald", text: "text-emerald", ring: "border-emerald/40", pulse: true },
  chatting: { label: "Chatting", dot: "bg-violet", text: "text-violet", ring: "border-violet/40", pulse: true },
};

/** The status word a cat is showing right now (idle → thinking → coding/testing). */
export function StatusPill({ status, className }: { status: Status | string; className?: string }) {
  const s = STYLE[(status as Status) in STYLE ? (status as Status) : "idle"];
  return (
    <span
      data-status={status}
      className={clsx("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium", s.ring, s.text, className)}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", s.dot, s.pulse && "animate-dot-pulse")} />
      {s.label}
    </span>
  );
}
