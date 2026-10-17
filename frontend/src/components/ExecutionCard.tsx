import { faCheck, faChevronDown, faChevronUp, faCircleNotch, faFileCode, faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useId, useState, type ReactNode } from "react";

import { humanDuration } from "../lib/time";
import type { ActivityStep } from "../lib/types";

export interface ExecutionCardProps {
  steps: ActivityStep[];
  /** A live card (the turn is still running): spinner + accent border. */
  running?: boolean;
  /** Whole-turn duration in seconds, when known. */
  elapsed?: number;
  /** Optional label above the summary ("Pulse", "Phase 4: Build"). */
  title?: string;
  /** Live cards: what is happening right now ("Waiting for your answer", "Thinking — 2m 10s"). */
  now?: string;
  failed?: boolean;
  /** Extra expandable content (file changes / diffs). */
  children?: ReactNode;
  defaultExpanded?: boolean;
  className?: string;
}

/**
 * The compact activity callout used in chat: whatever the cat is doing or did,
 * clamped to TWO lines (`line-clamp-2`), with a "See more" toggle that opens the
 * full ordered log (every step with its preview) and any attached diffs.
 */
/** A question the cat asked reads as a conversation. Older turns stored it as the raw tool call
 * (line "ask_user", preview "args: {…} result: User answered: …"): show those the same way. */
export function humanStep(step: ActivityStep): ActivityStep {
  if (step.line !== "ask_user" || !step.preview) return step;
  const question = /"(?:prompt|question)"\s*:\s*"((?:[^"\\]|\\.)*)/.exec(step.preview)?.[1];
  const answer = /result:\s*(?:User answered:\s*)?([\s\S]*)$/.exec(step.preview)?.[1]?.trim();
  return {
    ...step,
    line: `Asked you: ${question ? question.replace(/\\"/g, '"') : "a question"}`,
    preview: answer ? `You answered: ${answer}` : "No answer",
  };
}

export function ExecutionCard({ steps: rawSteps, running, elapsed, title, now, failed, children, defaultExpanded = false, className }: ExecutionCardProps) {
  const steps = rawSteps.map(humanStep);
  const [open, setOpen] = useState(defaultExpanded);
  const bodyId = useId();
  const latest = steps.length ? steps[steps.length - 1] : undefined;
  // The two-line callout is the human line only ("Asked you: …", "Editing src/App.jsx"); the raw
  // arguments and results stay in the expanded log for whoever wants them.
  const summary = latest ? latest.line : running ? "Working…" : "No activity recorded";
  const hasMore = steps.length > 1 || Boolean(latest?.preview) || Boolean(children);

  return (
    <div
      data-testid="execution-card"
      data-expanded={open}
      data-running={Boolean(running)}
      className={clsx(
        "rounded-lg border bg-[#10131b] text-sm transition-colors",
        running ? "border-cyan/35" : failed ? "border-danger/40" : "border-line",
        className,
      )}
    >
      <div className="flex items-start gap-2.5 px-3 py-2">
        <span className={clsx("mt-[3px] w-4 shrink-0 text-center text-xs", failed ? "text-danger" : running ? "text-cyan" : "text-emerald")}>
          <FontAwesomeIcon icon={failed ? faTriangleExclamation : running ? faCircleNotch : faCheck} spin={running} />
        </span>
        <div className="min-w-0 flex-1">
          {(title || elapsed !== undefined || steps.length > 0) && (
            <div className="mb-0.5 flex items-center gap-2 text-[11px] uppercase tracking-wide text-muted">
              {title && <span className="truncate font-medium">{title}</span>}
              {steps.length > 0 && (
                <span>
                  {steps.length} {steps.length === 1 ? "step" : "steps"}
                </span>
              )}
              {elapsed !== undefined && elapsed > 0 && <span>{humanDuration(elapsed)}</span>}
            </div>
          )}
          {/* the two-line callout */}
          <p data-testid="execution-summary" className="line-clamp-2 break-words text-[13px] leading-snug text-ink/90">
            {summary}
          </p>
          {running && now && (
            <p data-testid="execution-now" className="mt-0.5 truncate text-[11px] text-cyan/90">
              Now: {now}
            </p>
          )}
        </div>
        {hasMore && (
          <button
            type="button"
            className="btn btn-ghost btn-sm -my-0.5 shrink-0 text-[11px]"
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Show less" : "See more"}
            <FontAwesomeIcon icon={open ? faChevronUp : faChevronDown} className="text-[10px]" />
          </button>
        )}
      </div>
      {open && (
        <div id={bodyId} className="max-h-96 space-y-2 overflow-y-auto border-t border-line px-3 py-2.5">
          {steps.length > 0 && (
            <ol className="space-y-1.5" data-testid="execution-log">
              {steps.map((s, i) => (
                <li key={i} className="flex gap-2 text-xs">
                  <span className="w-5 shrink-0 text-right font-mono text-muted/70">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <div className="break-words text-ink/90">{s.line}</div>
                    {s.preview && (
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-[#0b0e15] p-2 font-mono text-[11px] leading-relaxed text-muted">
                        {s.preview}
                      </pre>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
          {children}
        </div>
      )}
    </div>
  );
}

/** A small file-change row used inside an expanded card. */
export function FileLine({ path }: { path: string }) {
  return (
    <div className="flex items-center gap-2 font-mono text-[11px] text-muted">
      <FontAwesomeIcon icon={faFileCode} className="text-violet" />
      <span className="truncate">{path}</span>
    </div>
  );
}
