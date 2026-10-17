import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TerminalPanel } from "../TerminalPanel";
import { historyFor } from "../../state/terminal";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  closed = false;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.closed = true;
    this.readyState = 3;
  }
  // test helpers
  frame(f: object) {
    act(() => this.onmessage?.({ data: JSON.stringify(f) } as MessageEvent));
  }
  ready(cwd = "/work/site") {
    this.readyState = 1;
    this.frame({ type: "ready", cwd, root: cwd, shell: "bash", platform: "posix" });
  }
}

const terminal = { WebSocketImpl: FakeSocket as unknown as typeof WebSocket };
const last = () => FakeSocket.instances[FakeSocket.instances.length - 1];

function mount(props: Partial<Parameters<typeof TerminalPanel>[0]> = {}) {
  return render(<TerminalPanel projectId="p1" open onClose={() => undefined} terminal={terminal} {...props} />);
}
const box = () => screen.getByLabelText(/terminal command|send input/i);
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const key = (k: string, extra: object = {}) => fireEvent.keyDown(box(), { key: k, ...extra });

beforeEach(() => {
  FakeSocket.instances = [];
  // rAF-batched repaints: run them straight away
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => {
    cb();
    return 1;
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TerminalPanel", () => {
  it("connects to the project's terminal socket and shows the shell and folder", () => {
    mount();
    expect(last().url).toMatch(/\/ws\/projects\/p1\/terminal$/);
    expect(screen.getByRole("status")).toHaveTextContent("Connecting");
    last().ready("/work/site");
    expect(screen.getByRole("status")).toHaveTextContent("Ready");
    expect(screen.getByText("bash")).toBeInTheDocument();
    expect(screen.getAllByText("/work/site").length).toBeGreaterThan(0);
  });

  it("runs a command, echoes it and streams coloured output", () => {
    mount();
    last().ready();
    type("npm test");
    key("Enter");
    expect(last().sent).toEqual([{ type: "run", command: "npm test" }]);
    expect(screen.getByRole("log")).toHaveTextContent("site $ npm test");
    last().frame({ type: "started" });
    expect(screen.getByRole("status")).toHaveTextContent("Running");
    last().frame({ type: "output", stream: "stdout", data: "\x1b[32mPASS\x1b[0m all good\n" });
    const pass = screen.getByText("PASS");
    expect(pass).toHaveStyle({ color: "#4ade80" });
    last().frame({ type: "exit", code: 1, cwd: "/work/site" });
    expect(screen.getByRole("log")).toHaveTextContent("exit code 1");
    expect(screen.getByRole("status")).toHaveTextContent("Ready");
    expect(box()).toHaveValue("");
  });

  it("sends the prompt line as stdin while a command runs, and Ctrl+C interrupts it", () => {
    mount();
    last().ready();
    type("python ask.py");
    key("Enter");
    last().frame({ type: "started" });
    type("Ada");
    key("Enter");
    expect(last().sent[1]).toEqual({ type: "input", data: "Ada\n" });
    key("c", { ctrlKey: true });
    expect(last().sent[2]).toEqual({ type: "interrupt" });
    last().frame({ type: "exit", code: null, cwd: "/work/site", interrupted: true });
    expect(screen.getByRole("log")).toHaveTextContent("^C");
  });

  it("walks the command history with Up and Down and restores the unfinished line", () => {
    mount();
    last().ready();
    for (const c of ["ls", "git status"]) {
      type(c);
      key("Enter");
      last().frame({ type: "exit", code: 0, cwd: "/work/site" });
    }
    expect(historyFor("p1").slice(-2)).toEqual(["ls", "git status"]);
    type("gi");
    key("ArrowUp");
    expect(box()).toHaveValue("git status");
    key("ArrowUp");
    expect(box()).toHaveValue("ls");
    key("ArrowDown");
    expect(box()).toHaveValue("git status");
    key("ArrowDown");
    expect(box()).toHaveValue("gi");
  });

  it("clears the output and copies it as plain text", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mount();
    last().ready();
    last().frame({ type: "output", stream: "stdout", data: "\x1b[31mred text\x1b[0m\n" });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy output" }));
    });
    expect(writeText).toHaveBeenCalledWith("red text");
    fireEvent.click(screen.getByRole("button", { name: "Clear terminal" }));
    expect(screen.getByRole("log")).not.toHaveTextContent("red text");
  });

  it("stays alive but hidden when collapsed, and closes its socket when it goes away", () => {
    const { rerender, unmount } = mount({ open: false });
    expect(screen.getByTestId("terminal-panel")).not.toBeVisible();
    // a class like Tailwind's `flex` beats the [hidden] attribute in a real browser: hide it inline too
    expect(screen.getByTestId("terminal-panel").style.display).toBe("none");
    expect(last().closed).toBe(false);
    rerender(<TerminalPanel projectId="p1" open onClose={() => undefined} terminal={terminal} />);
    expect(screen.getByTestId("terminal-panel")).toBeVisible();
    expect(FakeSocket.instances).toHaveLength(1);
    unmount();
    expect(last().closed).toBe(true); // the backend kills the shell when the socket closes
  });

  it("offers to reconnect when the socket drops and starts a fresh one", () => {
    mount();
    last().ready();
    act(() => last().onclose?.());
    expect(screen.getByRole("status")).toHaveTextContent("Disconnected");
    expect(box()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /reconnect/i }));
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    mount({ onClose });
    last().ready();
    key("Escape");
    expect(onClose).toHaveBeenCalled();
  });
});
