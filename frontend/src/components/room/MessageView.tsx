import clsx from "clsx";

import { humanAgo } from "../../lib/time";
import type { Agent, FileChange, Message } from "../../lib/types";
import { Avatar } from "../Avatar";
import { ExecutionCard, FileLine } from "../ExecutionCard";
import { CollapsibleText } from "../CollapsibleText";

const PHASE_LABEL: Record<string, string> = {
  blueprint: "Blueprint",
  architecture: "Architecture",
  distribution: "Distribution",
  devops: "DevOps",
  build: "Build",
  design: "Design",
  qa: "QA",
  "qa-fix": "QA fix",
  mention: "Direct",
  spontaneous: "Advisory",
};

/** Phases whose message is a cat's work report: shown as a short card until "See more". */
const WORK_PHASES = new Set(["blueprint", "architecture", "distribution", "devops", "build", "design", "qa", "qa-fix"]);
/** Lines of a message shown before "See more": a work report is a summary card, a conversation reply reads freely. */
const WORK_LINES = 5;
const CHAT_LINES = 12;

interface Props {
  message: Message;
  /** The speaker's card. Cat Crew (not an agent) passes just name/avatar/colour. */
  agent?: Pick<Agent, "name" | "avatar" | "color">;
  now: number;
  /** File changes this cat made while producing the message (shown in its card). */
  changes?: FileChange[];
}

export function MessageView({ message: m, agent, now, changes = [] }: Props) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end animate-fade-up" data-testid="message" data-role="user">
        <div className="max-w-[78%] rounded-2xl rounded-br-md border border-cyan/25 bg-cyan/10 px-4 py-2.5">
          <CollapsibleText text={m.text} maxLines={CHAT_LINES} />
          <div className="mt-1 text-right text-[10px] text-muted">{humanAgo(m.created_at, now)}</div>
        </div>
      </div>
    );
  }

  if (m.role === "error") {
    return (
      <div className="animate-fade-up rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger" role="alert" data-testid="message" data-role="error">
        {m.text}
      </div>
    );
  }

  const steps = m.activity?.steps ?? (m.tool_events ?? []).map((line) => ({ line }));
  const speaker = m.speaker || agent?.name || "CatCrewAI";
  const phase = m.phase ? PHASE_LABEL[m.phase] ?? m.phase : "";
  const color = agent?.color;

  return (
    <div className="flex gap-3 animate-fade-up" data-testid="message" data-role="assistant" data-bot={m.bot_id ?? ""}>
      {agent ? (
        <Avatar agent={agent} size={32} className="mt-0.5" />
      ) : (
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-white/5 text-xs text-muted">{speaker.slice(0, 1)}</span>
      )}
      <div className="min-w-0 max-w-[86%] flex-1 space-y-2">
        <div className="flex items-center gap-2 text-xs">
          <span className={clsx("font-semibold")} style={color ? { color } : undefined}>
            {speaker}
          </span>
          {phase && <span className="chip">{phase}</span>}
          <span className="text-[10px] text-muted">{humanAgo(m.created_at, now)}</span>
        </div>
        {m.text && (
          <div className="rounded-2xl rounded-tl-md border border-line bg-card px-4 py-2.5">
            <CollapsibleText text={m.text} maxLines={m.phase && WORK_PHASES.has(m.phase) ? WORK_LINES : CHAT_LINES} />
          </div>
        )}
        {(steps.length > 0 || changes.length > 0) && (
          <ExecutionCard steps={steps} elapsed={m.activity?.elapsed_s}>
            {changes.length > 0 && (
              <div className="space-y-1 border-t border-line pt-2">
                <div className="text-[11px] uppercase tracking-wide text-muted">Files written</div>
                {changes.map((c) => (
                  <FileLine key={c.diff_uuid} path={c.file_path} />
                ))}
              </div>
            )}
          </ExecutionCard>
        )}
      </div>
    </div>
  );
}
