// Reconnecting client for `/ws/events` (docs/API.md §3).
//
// The server has no replay: every (re)connect starts with a `hello`, and the
// owner of this socket refetches state over REST when it sees one. The socket
// itself only guarantees "connect, keep alive, back off, report status".

import type { ServerEvent } from "../lib/types";

export type ConnectionStatus = "connecting" | "open" | "down";

export interface EventSocketOptions {
  url: string;
  onEvent: (ev: ServerEvent) => void;
  onStatus?: (s: ConnectionStatus) => void;
  /** Called for each `hello` (first connect and every reconnect). */
  onHello?: (serverSeq: number, isReconnect: boolean) => void;
  WebSocketImpl?: typeof WebSocket;
  /** Reconnect delays in ms; the last one repeats. */
  backoff?: number[];
  pingEveryMs?: number;
  /** Close a socket that has been silent this long (pongs count as traffic). */
  staleAfterMs?: number;
}

export class EventSocket {
  private ws: WebSocket | null = null;
  private stopped = true;
  private attempt = 0;
  private everOpened = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private lastTraffic = 0;
  private readonly opts: Required<Omit<EventSocketOptions, "onStatus" | "onHello" | "WebSocketImpl">> &
    Pick<EventSocketOptions, "onStatus" | "onHello" | "WebSocketImpl">;

  constructor(opts: EventSocketOptions) {
    this.opts = {
      backoff: [400, 800, 1600, 3200, 6400, 10_000],
      pingEveryMs: 20_000,
      staleAfterMs: 65_000,
      ...opts,
    };
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.open();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    }
    this.opts.onStatus?.("down");
  }

  /** Narrow (or widen, with null) the server-side project filter. */
  subscribe(projectId: string | null): void {
    this.send({ type: "subscribe", project_id: projectId });
  }

  private send(obj: unknown): void {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(obj));
  }

  private clearTimers(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.reconnectTimer = this.pingTimer = null;
  }

  private open(): void {
    if (this.stopped) return;
    this.opts.onStatus?.("connecting");
    const Impl = this.opts.WebSocketImpl ?? WebSocket;
    let ws: WebSocket;
    try {
      ws = new Impl(this.opts.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.lastTraffic = Date.now();
    };
    ws.onmessage = (msg: MessageEvent) => {
      this.lastTraffic = Date.now();
      let data: unknown;
      try {
        data = JSON.parse(String(msg.data));
      } catch {
        return;
      }
      if (!data || typeof data !== "object") return;
      const frame = data as { type?: string };
      if (frame.type === "pong") return;
      if (frame.type === "hello") {
        const wasOpen = this.everOpened;
        this.everOpened = true;
        this.attempt = 0;
        this.opts.onStatus?.("open");
        this.startPing();
        this.opts.onHello?.((data as ServerEvent & { data: { server_seq: number } }).data.server_seq, wasOpen);
        return;
      }
      this.opts.onEvent(data as ServerEvent);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.pingTimer = null;
      this.opts.onStatus?.("down");
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      /* onclose follows; reconnect is scheduled there */
    };
  }

  private startPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = setInterval(() => {
      if (Date.now() - this.lastTraffic > this.opts.staleAfterMs) {
        try {
          this.ws?.close();
        } catch {
          /* ignore */
        }
        return;
      }
      this.send({ type: "ping" });
    }, this.opts.pingEveryMs);
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) return;
    const list = this.opts.backoff;
    const delay = list[Math.min(this.attempt, list.length - 1)];
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }
}
