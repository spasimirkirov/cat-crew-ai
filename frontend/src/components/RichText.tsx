import clsx from "clsx";
import { Fragment, type ReactNode } from "react";

// Chat text: paragraphs, fenced code blocks and `inline code`. Deliberately not a
// full markdown renderer (no HTML injection surface): everything is plain React text.

function inline(text: string): ReactNode[] {
  return text.split(/(`[^`\n]+`)/g).map((part, i) =>
    part.startsWith("`") && part.endsWith("`") && part.length > 2 ? (
      <code key={i} className="rounded bg-white/[0.07] px-1 py-0.5 font-mono text-[0.85em] text-cyan">
        {part.slice(1, -1)}
      </code>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

export function RichText({ text, className }: { text: string; className?: string }) {
  const blocks = text.split(/```/g);
  return (
    <div className={clsx("space-y-2 whitespace-pre-wrap break-words text-sm leading-relaxed", className)}>
      {blocks.map((block, i) => {
        if (i % 2 === 1) {
          // first line of a fence may be a language tag
          const nl = block.indexOf("\n");
          const code = nl !== -1 && /^[\w+-]*$/.test(block.slice(0, nl).trim()) ? block.slice(nl + 1) : block;
          return (
            <pre key={i} className="overflow-x-auto rounded-lg border border-line bg-[#0b0e15] p-3 font-mono text-xs leading-relaxed text-ink/90">
              {code.replace(/\n$/, "")}
            </pre>
          );
        }
        const trimmed = block.replace(/^\n+|\n+$/g, "");
        return trimmed ? <p key={i}>{inline(trimmed)}</p> : null;
      })}
    </div>
  );
}
