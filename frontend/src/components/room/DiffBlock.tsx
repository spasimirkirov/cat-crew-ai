import clsx from "clsx";
import { useState } from "react";

import { faChevronDown, faChevronUp } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";

/** A unified diff, shown as exactly two (single-line, truncated) lines until "See more". */
export function DiffBlock({ text, className }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const lines = text.replace(/\n$/, "").split("\n");
  const long = lines.length > 2;
  const rendered = (list: string[], oneLine = false) =>
    list.map((l, i) => (
      <div
        key={i}
        className={clsx(
          oneLine ? "truncate whitespace-pre" : "whitespace-pre-wrap break-all",
          l.startsWith("+") && !l.startsWith("+++") && "text-emerald",
          l.startsWith("-") && !l.startsWith("---") && "text-danger",
          l.startsWith("@@") && "text-violet",
          !/^[+\-@]/.test(l) && "text-muted",
        )}
      >
        {l || " "}
      </div>
    ));
  return (
    <div className={clsx("rounded-md border border-line bg-[#0b0e15] font-mono text-[11px] leading-relaxed", className)} data-testid="diff-block" data-expanded={open}>
      {open ? (
        <div className="max-h-96 overflow-auto p-2">{rendered(lines)}</div>
      ) : (
        <div className="overflow-hidden px-2 py-1.5" data-testid="diff-preview">{rendered(lines.slice(0, 2), true)}</div>
      )}
      {long && (
        <button type="button" className="btn btn-ghost btn-sm w-full rounded-t-none border-t border-line text-[11px]" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Show less" : `See more (${lines.length} lines)`}
          <FontAwesomeIcon icon={open ? faChevronUp : faChevronDown} className="text-[10px]" />
        </button>
      )}
    </div>
  );
}
