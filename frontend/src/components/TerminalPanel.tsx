import { faBroom, faCopy, faStop, faTerminal, faXmark, faPlug } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { memo, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";

import type { AnsiLine, AnsiSeg } from "../lib/ansi";
import { cwdName, historyFor, useTerminal, type UseTerminalOptions } from "../state/terminal";

interface Props {
  projectId: string;
  /** Whether the panel is showing. It stays mounted (and its shell alive) while collapsed. */
  open: boolean;
  onClose: () => void;
  /** Test seam. */
  terminal?: UseTerminalOptions;
}

const MIN_HEIGHT = 140;
const DEFAULT_HEIGHT = 288;

function segStyle(seg: AnsiSeg): CSSProperties {
  const { style } = seg;
  let fg = style.fg;
  let bg = style.bg;
  if (style.inverse) [fg, bg] = [bg ?? "#0b0e14", fg ?? "#e5e7eb"];
  if (!fg && seg.stream === "stderr") fg = "#fca5a5";
  return {
    color: fg,
    backgroundColor: bg,
    fontWeight: style.bold ? 600 : undefined,
    opacity: style.dim ? 0.65 : undefined,
    fontStyle: style.italic ? "italic" : undefined,
    textDecoration: style.underline ? "underline" : undefined,
  };
}

const Line = memo(function Line({ line }: { line: AnsiLine }) {
  if (line.length === 0) return <div className="min-h-[1.25em]">{"​"}</div>;
  return (
    <div className="min-h-[1.25em] whitespace-pre-wrap break-words">
      {line.map((seg, i) => (
        <span key={i} style={segStyle(seg)}>
          {seg.text}
        </span>
      ))}
    </div>
  );
});

/**
 * The collapsible terminal under a project's chat. Runs commands in the project folder through the
 * backend's `/ws/projects/{id}/terminal` socket. Ctrl/Cmd+` toggles it from the room header.
 */
export function TerminalPanel({ projectId, open, onClose, terminal }: Props) {
  const t = useTerminal(projectId, true, terminal);
  const [text, setText] = useState("");
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [copied, setCopied] = useState(false);
  const cursor = useRef(-1); // position in the history while browsing it; -1 = the line being typed
  const draft = useRef("");
  const input = useRef<HTMLInputElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // Focus the prompt whenever the panel is shown.
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  // Auto-scroll while the reader is at the bottom; scrolling up pauses it until they come back.
  useEffect(() => {
    const el = log.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [t.version, open]);

  const onScroll = () => {
    const el = log.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  const submit = () => {
    const value = text;
    setText("");
    cursor.current = -1;
    stick.current = true;
    if (t.running) t.sendInput(value);
    else t.run(value);
  };

  const browse = (dir: -1 | 1) => {
    const list = historyFor(projectId);
    if (list.length === 0) return;
    if (cursor.current === -1) {
      if (dir === 1) return;
      draft.current = text;
      cursor.current = list.length - 1;
    } else {
      const next = cursor.current + (dir === -1 ? -1 : 1);
      if (next >= list.length) {
        cursor.current = -1;
        setText(draft.current);
        return;
      }
      cursor.current = Math.max(0, next);
    }
    setText(list[cursor.current]);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      browse(-1);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      browse(1);
    } else if (ctrl && e.key.toLowerCase() === "c" && t.running && !window.getSelection()?.toString()) {
      e.preventDefault();
      t.interrupt();
    } else if (ctrl && e.key.toLowerCase() === "d" && t.running) {
      e.preventDefault();
      t.eof();
    } else if (ctrl && e.key.toLowerCase() === "l") {
      e.preventDefault();
      t.clear();
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  const copy = async () => {
    if (await t.copy()) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const startResize = (e: ReactPointerEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = height;
    const move = (ev: PointerEvent) => setHeight(Math.max(MIN_HEIGHT, Math.min(window.innerHeight * 0.8, startH + (startY - ev.clientY))));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const closed = t.status === "closed";

  return (
    <section
      aria-label="Terminal"
      hidden={!open}
      // Inline, not the `hidden` attribute alone: Tailwind's `flex` class outranks the browser's
      // [hidden] rule, so the panel would stay on screen after Close / the toggle.
      style={{ height, display: open ? undefined : "none" }}
      className="relative flex shrink-0 flex-col border-t border-line bg-[#080a10]"
      data-testid="terminal-panel"
    >
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize terminal"
        onPointerDown={startResize}
        className="absolute inset-x-0 -top-1 z-10 h-2 cursor-row-resize"
      />
      <header className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-xs">
        <FontAwesomeIcon icon={faTerminal} className="text-cyan" aria-hidden />
        <span className="font-semibold">Terminal</span>
        {t.shell && <span className="chip">{t.shell}</span>}
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted" title={t.cwd}>
          {t.cwd}
        </span>
        <span role="status" className={clsx("chip", t.status === "open" ? "border-emerald/40 text-emerald" : closed ? "border-danger/40 text-danger" : "text-muted")}>
          {t.status === "open" ? (t.running ? "Running" : "Ready") : closed ? "Disconnected" : "Connecting…"}
        </span>
        {closed && (
          <button type="button" className="btn btn-sm btn-ghost" onClick={t.reconnect} title="Reconnect">
            <FontAwesomeIcon icon={faPlug} /> Reconnect
          </button>
        )}
        {t.running && (
          <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={t.interrupt} title="Stop the running command (Ctrl+C)">
            <FontAwesomeIcon icon={faStop} /> Stop
          </button>
        )}
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => void copy()} aria-label="Copy output" title="Copy output">
          <FontAwesomeIcon icon={faCopy} /> {copied ? "Copied" : ""}
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={t.clear} aria-label="Clear terminal" title="Clear (Ctrl+L)">
          <FontAwesomeIcon icon={faBroom} />
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClose} aria-label="Close terminal" title="Hide terminal (Ctrl+`)">
          <FontAwesomeIcon icon={faXmark} />
        </button>
      </header>

      <div
        ref={log}
        onScroll={onScroll}
        role="log"
        aria-live="off"
        aria-label="Terminal output"
        tabIndex={-1}
        onClick={() => {
          if (!window.getSelection()?.toString()) input.current?.focus();
        }}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-2 font-mono text-[12.5px] leading-5 text-[#d1d5db]"
      >
        {t.buffer.droppedLines > 0 && <div className="text-muted">… {t.buffer.droppedLines} earlier lines dropped</div>}
        {t.buffer.lines.map((line, i) => (
          <Line key={i + t.buffer.droppedLines} line={line} />
        ))}
      </div>

      <div className="flex items-center gap-2 border-t border-line px-3 py-1.5 font-mono text-[12.5px]">
        <span className="shrink-0 text-cyan" aria-hidden>
          {t.running ? "›" : `${cwdName(t.cwd)} $`}
        </span>
        <input
          ref={input}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            cursor.current = -1;
          }}
          onKeyDown={onKeyDown}
          disabled={closed}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          aria-label={t.running ? "Send input to the running command" : "Terminal command"}
          placeholder={closed ? "Disconnected" : t.running ? "Type to send input to the running command" : "Type a command and press Enter"}
          className="min-w-0 flex-1 bg-transparent text-[#e5e7eb] outline-none placeholder:text-muted/60"
        />
      </div>
    </section>
  );
}
