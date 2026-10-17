import { faChevronDown, faChevronUp } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useId, useState } from "react";

import { RichText } from "./RichText";

/** Rough line count of chat text: each source line wraps after about `cols` characters. */
export function estimateLines(text: string, cols = 90): number {
  if (!text) return 0;
  return text.split("\n").reduce((n, line) => n + Math.max(1, Math.ceil(line.length / cols)), 0);
}

/** A hidden remainder of one or two lines is not worth a button: collapse only past this slack. */
const SLACK = 2;
/** One `text-sm leading-relaxed` line, plus a little for paragraph gaps. */
const LINE_REM = 1.45;

interface Props {
  text: string;
  /** Lines shown while collapsed. */
  maxLines: number;
  className?: string;
}

/**
 * Chat text that stays out of the way: past `maxLines` (plus a little slack) it is clipped with a soft
 * fade and a "See more" toggle. The full text is always in the DOM (the clip is CSS only), so search,
 * copy and screen readers still get all of it.
 */
export function CollapsibleText({ text, maxLines, className }: Props) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const lines = estimateLines(text);
  const collapsible = lines > maxLines + SLACK;
  const clipped = collapsible && !open;
  const clip = { maxHeight: `${maxLines * LINE_REM}rem`, WebkitMaskImage: FADE, maskImage: FADE };

  return (
    <div className={className}>
      <div id={bodyId} data-testid="message-text" data-collapsed={clipped} className={clipped ? "overflow-hidden" : undefined} style={clipped ? clip : undefined}>
        <RichText text={text} />
      </div>
      {collapsible && (
        <button type="button" className="btn btn-ghost btn-sm -ml-2 mt-1 text-[11px]" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((v) => !v)}>
          {open ? "Show less" : `See more (${lines - maxLines} more lines)`}
          <FontAwesomeIcon icon={open ? faChevronUp : faChevronDown} className="text-[10px]" />
        </button>
      )}
    </div>
  );
}

const FADE = "linear-gradient(to bottom, #000 calc(100% - 2rem), transparent)";
