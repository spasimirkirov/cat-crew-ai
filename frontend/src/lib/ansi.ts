// A small ANSI/VT text model for the integrated terminal.
//
// Commands run over pipes (no PTY), so what arrives is plain text with colour codes. This turns a
// stream of chunks into styled lines: SGR colours and attributes (16, 256 and true colour), `\r`
// rewriting the current line (progress bars) and `\r\n` line ends. Every other escape sequence
// (cursor moves, OSC titles) is swallowed rather than shown as garbage. Escapes that are split across
// two chunks are held until the rest arrives.

export interface AnsiStyle {
  fg?: string;
  bg?: string;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
  inverse?: boolean;
}

export interface AnsiSeg {
  text: string;
  style: AnsiStyle;
  /** Which pipe the text came from (stderr is tinted when it has no colour of its own). */
  stream: "stdout" | "stderr";
}

export type AnsiLine = AnsiSeg[];

const BASE = ["#1f2937", "#f87171", "#4ade80", "#facc15", "#60a5fa", "#c084fc", "#22d3ee", "#d1d5db"];
const BRIGHT = ["#6b7280", "#fca5a5", "#86efac", "#fde047", "#93c5fd", "#d8b4fe", "#67e8f9", "#f9fafb"];

const hex = (n: number) => n.toString(16).padStart(2, "0");
const rgb = (r: number, g: number, b: number) => `#${hex(r)}${hex(g)}${hex(b)}`;

/** xterm 256-colour palette entry. */
export function color256(n: number): string {
  if (n < 8) return BASE[n];
  if (n < 16) return BRIGHT[n - 8];
  if (n >= 232) {
    const v = 8 + (n - 232) * 10;
    return rgb(v, v, v);
  }
  const i = n - 16;
  const level = (x: number) => (x === 0 ? 0 : 55 + x * 40);
  return rgb(level(Math.floor(i / 36)), level(Math.floor((i % 36) / 6)), level(i % 6));
}

const clamp = (n: number) => Math.max(0, Math.min(255, n | 0));

/** Apply one `ESC[...m` parameter list to a style (returns a new object). */
export function applySgr(style: AnsiStyle, params: number[]): AnsiStyle {
  let s: AnsiStyle = { ...style };
  const list = params.length ? params : [0];
  for (let i = 0; i < list.length; i++) {
    const p = list[i];
    if (p === 0) s = {};
    else if (p === 1) s.bold = true;
    else if (p === 2) s.dim = true;
    else if (p === 3) s.italic = true;
    else if (p === 4) s.underline = true;
    else if (p === 7) s.inverse = true;
    else if (p === 22) {
      s.bold = false;
      s.dim = false;
    } else if (p === 23) s.italic = false;
    else if (p === 24) s.underline = false;
    else if (p === 27) s.inverse = false;
    else if (p >= 30 && p <= 37) s.fg = BASE[p - 30];
    else if (p >= 90 && p <= 97) s.fg = BRIGHT[p - 90];
    else if (p >= 40 && p <= 47) s.bg = BASE[p - 40];
    else if (p >= 100 && p <= 107) s.bg = BRIGHT[p - 100];
    else if (p === 39) s.fg = undefined;
    else if (p === 49) s.bg = undefined;
    else if (p === 38 || p === 48) {
      const key = p === 38 ? "fg" : "bg";
      if (list[i + 1] === 5 && i + 2 < list.length) {
        s[key] = color256(clamp(list[i + 2]));
        i += 2;
      } else if (list[i + 1] === 2 && i + 4 < list.length) {
        s[key] = rgb(clamp(list[i + 2]), clamp(list[i + 3]), clamp(list[i + 4]));
        i += 4;
      }
    }
  }
  return s;
}

const sameStyle = (a: AnsiStyle, b: AnsiStyle) =>
  a.fg === b.fg && a.bg === b.bg && !a.bold === !b.bold && !a.dim === !b.dim && !a.italic === !b.italic && !a.underline === !b.underline && !a.inverse === !b.inverse;

export const MAX_LINES = 5000;

export class AnsiBuffer {
  /** The last line is the one still being written. Lines are replaced, never edited, so React can memo them. */
  lines: AnsiLine[] = [[]];
  private style: AnsiStyle = {};
  private held = ""; // an escape sequence cut off by the end of a chunk
  private cr = false; // a "\r" was the last thing seen
  private dropped = 0;

  /** How many old lines were discarded to stay under the cap. */
  get droppedLines(): number {
    return this.dropped;
  }

  clear(): void {
    this.lines = [[]];
    this.style = {};
    this.held = "";
    this.cr = false;
    this.dropped = 0;
  }

  private newline(): void {
    this.lines.push([]);
    if (this.lines.length > MAX_LINES) {
      const extra = this.lines.length - MAX_LINES;
      this.lines.splice(0, extra);
      this.dropped += extra;
    }
  }

  private put(text: string, stream: "stdout" | "stderr"): void {
    if (!text) return;
    if (this.cr) {
      // "\r" then text: the line is being rewritten from the start.
      this.lines[this.lines.length - 1] = [];
      this.cr = false;
    }
    const at = this.lines.length - 1;
    const line = this.lines[at];
    const last = line[line.length - 1];
    if (last && last.stream === stream && sameStyle(last.style, this.style)) {
      this.lines[at] = [...line.slice(0, -1), { ...last, text: last.text + text }];
    } else {
      this.lines[at] = [...line, { text, style: this.style, stream }];
    }
  }

  /** Append a chunk of output. */
  write(data: string, stream: "stdout" | "stderr" = "stdout"): void {
    const text = this.held + data;
    this.held = "";
    let run = "";
    const flush = () => {
      this.put(run, stream);
      run = "";
    };
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (ch === "\x1b") {
        const next = text[i + 1];
        if (next === undefined) {
          flush();
          this.held = text.slice(i);
          return;
        }
        if (next === "[") {
          let j = i + 2;
          while (j < text.length && !(text.charCodeAt(j) >= 0x40 && text.charCodeAt(j) <= 0x7e)) j++;
          if (j >= text.length) {
            flush();
            this.held = text.slice(i);
            return;
          }
          flush();
          if (text[j] === "m") {
            const raw = text.slice(i + 2, j);
            const params = raw === "" ? [] : raw.split(/[;:]/).map((x) => (x === "" ? 0 : Number(x) || 0));
            this.style = applySgr(this.style, params);
          } else if (text[j] === "K" || text[j] === "J") {
            // erase in line/display: after "\r" the line was already restarted; nothing else to do
            if (this.cr) {
              this.lines[this.lines.length - 1] = [];
              this.cr = false;
            }
          }
          i = j;
          continue;
        }
        if (next === "]") {
          // OSC ... BEL | ESC \
          let j = i + 2;
          while (j < text.length && text[j] !== "\x07" && !(text[j] === "\x1b" && text[j + 1] === "\\")) j++;
          if (j >= text.length) {
            flush();
            this.held = text.slice(i);
            return;
          }
          flush();
          i = text[j] === "\x07" ? j : j + 1;
          continue;
        }
        flush();
        if ("()*+-./".includes(next)) {
          // charset selection (ESC ( B) has one more byte
          if (i + 2 >= text.length) {
            this.held = text.slice(i);
            return;
          }
          i += 2;
        } else {
          i += 1; // a two-character escape: drop it
        }
        continue;
      }
      if (ch === "\r") {
        flush();
        if (text[i + 1] === "\n") {
          i += 1;
          this.cr = false;
          this.newline();
        } else if (i + 1 >= text.length) {
          this.cr = true;
        } else {
          this.lines[this.lines.length - 1] = [];
          this.cr = false;
        }
        continue;
      }
      if (ch === "\n") {
        flush();
        if (this.cr) this.cr = false; // a "\r" then "\n" split over two chunks
        this.newline();
        continue;
      }
      if (ch === "\b" || ch === "\x07" || (ch < " " && ch !== "\t")) continue; // other control bytes
      run += ch;
    }
    flush();
  }

  /** Plain text of everything (colours stripped), for Copy. */
  text(): string {
    return this.lines.map((l) => l.map((s) => s.text).join("")).join("\n").replace(/\n+$/, "");
  }
}
