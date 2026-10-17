import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { AnsiBuffer } from "../lib/ansi";
import { WS_BASE } from "../lib/config";

export type TerminalStatus = "idle" | "connecting" | "open" | "closed";

/** Frames the backend sends on `/ws/projects/{id}/terminal` (see `backend/api/terminal_ws.py`). */
export type TerminalFrame =
  | { type: "ready"; cwd: string; root: string; shell: string; platform: string }
  | { type: "started" }
  | { type: "output"; stream: "stdout" | "stderr"; data: string }
  | { type: "exit"; code: number | null; cwd: string; interrupted?: boolean }
  | { type: "error"; message: string }
  | { type: "pong" };

export const HISTORY_MAX = 200;

// Command history outlives the panel (closing and reopening it, switching projects and coming back).
const histories = new Map<string, string[]>();

export function pushHistory(projectId: string, command: string): string[] {
  const list = histories.get(projectId) ?? [];
  const text = command.trim();
  if (text && list[list.length - 1] !== text) list.push(text);
  if (list.length > HISTORY_MAX) list.splice(0, list.length - HISTORY_MAX);
  histories.set(projectId, list);
  return list;
}

export const historyFor = (projectId: string): string[] => histories.get(projectId) ?? [];

export interface UseTerminalOptions {
  /** Test seam. */
  WebSocketImpl?: typeof WebSocket;
}

export interface TerminalApi {
  status: TerminalStatus;
  cwd: string;
  shell: string;
  running: boolean;
  buffer: AnsiBuffer;
  /** Bumps whenever the buffer changed, so a component re-renders. */
  version: number;
  run: (command: string) => void;
  /** stdin for the running command (echoed locally: pipes do not echo). */
  sendInput: (text: string) => void;
  eof: () => void;
  interrupt: () => void;
  clear: () => void;
  copy: () => Promise<boolean>;
  reconnect: () => void;
}

/**
 * One project's terminal over its WebSocket. Connects while `enabled`; closing the socket (unmount, project
 * switch) is what makes the backend kill the shell, so nothing is left running behind a page you left.
 */
export function useTerminal(projectId: string, enabled: boolean, opts: UseTerminalOptions = {}): TerminalApi {
  const [status, setStatus] = useState<TerminalStatus>("idle");
  const [cwd, setCwd] = useState("");
  const [shell, setShell] = useState("");
  const [running, setRunning] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [version, bump] = useReducer((n: number) => n + 1, 0);
  const buffer = useRef(new AnsiBuffer()).current;
  const socket = useRef<WebSocket | null>(null);
  const frame = useRef(false); // a repaint is already scheduled
  const Impl = opts.WebSocketImpl;

  // Repaint at most once per animation frame, however many chunks arrive.
  const repaint = useCallback(() => {
    if (frame.current) return;
    frame.current = true;
    const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (cb: () => void) => setTimeout(cb, 16);
    raf(() => {
      frame.current = false;
      bump();
    });
  }, []);

  useEffect(() => {
    if (!enabled || !projectId) return;
    setStatus("connecting");
    const Socket = Impl ?? WebSocket;
    const ws = new Socket(`${WS_BASE}/ws/projects/${encodeURIComponent(projectId)}/terminal`);
    socket.current = ws;
    let live = true;

    ws.onmessage = (ev: MessageEvent) => {
      if (!live) return;
      let f: TerminalFrame;
      try {
        f = JSON.parse(String(ev.data)) as TerminalFrame;
      } catch {
        return;
      }
      if (f.type === "ready") {
        setCwd(f.cwd);
        setShell(f.shell);
        setStatus("open");
      } else if (f.type === "output") {
        buffer.write(f.data, f.stream);
        repaint();
      } else if (f.type === "started") {
        setRunning(true);
      } else if (f.type === "exit") {
        setRunning(false);
        setCwd(f.cwd);
        if (f.interrupted) buffer.write("\x1b[2m^C\x1b[0m\n");
        else if (f.code) buffer.write(`\x1b[2m[exit code ${f.code}]\x1b[0m\n`);
        repaint();
      } else if (f.type === "error") {
        buffer.write(`\x1b[31m${f.message}\x1b[0m\n`);
        repaint();
      }
    };
    ws.onclose = () => {
      if (!live) return;
      setStatus("closed");
      setRunning(false);
      buffer.write("\x1b[2m[terminal disconnected]\x1b[0m\n");
      repaint();
    };
    ws.onerror = () => undefined; // onclose follows

    return () => {
      live = false;
      if (socket.current === ws) socket.current = null;
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    };
  }, [enabled, projectId, attempt, Impl, buffer, repaint]);

  const send = useCallback((payload: object): boolean => {
    const ws = socket.current;
    if (!ws || ws.readyState !== 1) return false;
    ws.send(JSON.stringify(payload));
    return true;
  }, []);

  const run = useCallback(
    (command: string) => {
      const text = command.replace(/\r?\n$/, "");
      if (!text.trim()) return;
      pushHistory(projectId, text);
      buffer.write(`\x1b[36m${cwdName(cwd)}\x1b[0m \x1b[2m$\x1b[0m ${text}\n`);
      repaint();
      if (!send({ type: "run", command: text })) {
        buffer.write("\x1b[31mNot connected.\x1b[0m\n");
        repaint();
      }
    },
    [projectId, buffer, cwd, send, repaint],
  );

  const sendInput = useCallback(
    (text: string) => {
      buffer.write(`${text}\n`);
      repaint();
      send({ type: "input", data: `${text}\n` });
    },
    [buffer, send, repaint],
  );

  return {
    status,
    cwd,
    shell,
    running,
    buffer,
    version,
    run,
    sendInput,
    eof: () => void send({ type: "eof" }),
    interrupt: () => void send({ type: "interrupt" }),
    clear: () => {
      buffer.clear();
      bump();
    },
    copy: async () => {
      try {
        await navigator.clipboard.writeText(buffer.text());
        return true;
      } catch {
        return false;
      }
    },
    reconnect: () => {
      buffer.write("\x1b[2m[reconnecting]\x1b[0m\n");
      setAttempt((n) => n + 1);
    },
  };
}

/** Last path segment, for the prompt (`C:\dev\site` -> `site`). */
export function cwdName(cwd: string): string {
  const parts = cwd.split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] ?? (cwd || "~");
}
