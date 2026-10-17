import { describe, expect, it } from "vitest";

import { AnsiBuffer, applySgr, color256, MAX_LINES } from "./ansi";

const plain = (b: AnsiBuffer) => b.lines.map((l) => l.map((s) => s.text).join(""));

describe("AnsiBuffer", () => {
  it("splits lines on \\n and \\r\\n", () => {
    const b = new AnsiBuffer();
    b.write("one\r\ntwo\nthree");
    expect(plain(b)).toEqual(["one", "two", "three"]);
  });

  it("keeps a chunk boundary in the middle of a line invisible", () => {
    const b = new AnsiBuffer();
    b.write("hel");
    b.write("lo\r");
    b.write("\nworld");
    expect(plain(b)).toEqual(["hello", "world"]);
  });

  it("colours text with SGR codes and resets", () => {
    const b = new AnsiBuffer();
    b.write("\x1b[31mred\x1b[0m plain \x1b[1;32mbold green\x1b[0m");
    const [line] = b.lines;
    expect(line.map((s) => s.text)).toEqual(["red", " plain ", "bold green"]);
    expect(line[0].style.fg).toBe(color256(1));
    expect(line[1].style).toEqual({});
    expect(line[2].style).toMatchObject({ bold: true, fg: color256(2) });
    expect(b.text()).toBe("red plain bold green");
  });

  it("understands 256-colour, true colour and bright colours", () => {
    expect(applySgr({}, [38, 5, 196]).fg).toBe(color256(196));
    expect(applySgr({}, [48, 2, 10, 20, 30]).bg).toBe("#0a141e");
    expect(applySgr({}, [92]).fg).toBe(color256(10));
    expect(applySgr({ fg: "#fff" }, [39]).fg).toBeUndefined();
    expect(applySgr({ bold: true, dim: true }, [22])).toMatchObject({ bold: false, dim: false });
  });

  it("holds an escape sequence that is cut in two chunks", () => {
    const b = new AnsiBuffer();
    b.write("a\x1b[3");
    b.write("2mgreen");
    expect(b.lines[0].map((s) => s.text)).toEqual(["a", "green"]);
    expect(b.lines[0][1].style.fg).toBe(color256(2));
  });

  it("rewrites the line on a bare \\r (progress bars)", () => {
    const b = new AnsiBuffer();
    b.write("10%\r50%\r100%\ndone");
    expect(plain(b)).toEqual(["100%", "done"]);
    const c = new AnsiBuffer();
    c.write("first\r");
    c.write("second");
    expect(plain(c)).toEqual(["second"]);
  });

  it("swallows cursor moves and OSC titles instead of printing them", () => {
    const b = new AnsiBuffer();
    b.write("\x1b]0;my title\x07ok\x1b[2K\x1b[1Ashown\x1b(B");
    expect(b.text()).toBe("okshown");
  });

  it("remembers which pipe text came from and merges runs of the same style", () => {
    const b = new AnsiBuffer();
    b.write("out ");
    b.write("more", "stdout");
    b.write("err", "stderr");
    expect(b.lines[0].map((s) => [s.text, s.stream])).toEqual([
      ["out more", "stdout"],
      ["err", "stderr"],
    ]);
  });

  it("replaces lines instead of mutating them (so React can memo)", () => {
    const b = new AnsiBuffer();
    b.write("a\nb");
    const before = b.lines[0];
    b.write("c");
    expect(b.lines[0]).toBe(before);
    expect(plain(b)).toEqual(["a", "bc"]);
  });

  it("caps the scrollback and says how much was dropped", () => {
    const b = new AnsiBuffer();
    b.write(Array.from({ length: MAX_LINES + 50 }, (_, i) => `l${i}`).join("\n"));
    expect(b.lines.length).toBe(MAX_LINES);
    expect(b.droppedLines).toBe(50);
    expect(b.lines[b.lines.length - 1][0].text).toBe(`l${MAX_LINES + 49}`);
  });

  it("clears everything, including the colour in effect", () => {
    const b = new AnsiBuffer();
    b.write("\x1b[31mx");
    b.clear();
    b.write("y");
    expect(b.lines).toHaveLength(1);
    expect(b.lines[0][0].style).toEqual({});
  });
});
