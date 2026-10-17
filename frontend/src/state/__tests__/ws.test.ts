import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventSocket, type ConnectionStatus } from "../ws";

class FakeWS {
  static instances: FakeWS[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send(s: string) {
    this.sent.push(s);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  push(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

const make = () => {
  const events: unknown[] = [];
  const statuses: ConnectionStatus[] = [];
  const hellos: [number, boolean][] = [];
  const sock = new EventSocket({
    url: "ws://x/ws/events",
    onEvent: (e) => events.push(e),
    onStatus: (s) => statuses.push(s),
    onHello: (n, re) => hellos.push([n, re]),
    WebSocketImpl: FakeWS as unknown as typeof WebSocket,
    backoff: [100, 200],
    pingEveryMs: 1000,
    staleAfterMs: 5000,
  });
  return { sock, events, statuses, hellos };
};
const hello = (n = 1) => ({ v: 1, seq: 0, ts: "", type: "hello", project_id: null, data: { server_seq: n } });

beforeEach(() => {
  vi.useFakeTimers();
  FakeWS.instances = [];
});
afterEach(() => vi.useRealTimers());

describe("EventSocket", () => {
  it("is open only after the server's hello, and forwards events but not pongs", () => {
    const { sock, events, statuses, hellos } = make();
    sock.start();
    const ws = FakeWS.instances[0];
    ws.open();
    expect(statuses).toEqual(["connecting"]);
    ws.push(hello(7));
    ws.push({ type: "pong" });
    ws.push({ v: 1, seq: 8, ts: "", type: "bot.released", project_id: "p", data: { bot_id: "x" } });
    expect(statuses).toEqual(["connecting", "open"]);
    expect(hellos).toEqual([[7, false]]);
    expect(events).toHaveLength(1);
    sock.stop();
  });

  it("reconnects with back-off after a drop and flags the next hello as a reconnect", () => {
    const { sock, statuses, hellos } = make();
    sock.start();
    FakeWS.instances[0].open();
    FakeWS.instances[0].push(hello());
    FakeWS.instances[0].close(); // server went away
    expect(statuses[statuses.length - 1]).toBe("down");
    expect(FakeWS.instances).toHaveLength(1);

    vi.advanceTimersByTime(100);
    expect(FakeWS.instances).toHaveLength(2);
    FakeWS.instances[1].close(); // still down: next delay is 200 ms
    vi.advanceTimersByTime(150);
    expect(FakeWS.instances).toHaveLength(2);
    vi.advanceTimersByTime(60);
    expect(FakeWS.instances).toHaveLength(3);

    FakeWS.instances[2].open();
    FakeWS.instances[2].push(hello(9));
    expect(hellos).toEqual([[1, false], [9, true]]);
    expect(statuses[statuses.length - 1]).toBe("open");
    sock.stop();
  });

  it("stop() never reconnects", () => {
    const { sock } = make();
    sock.start();
    FakeWS.instances[0].open();
    sock.stop();
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.instances).toHaveLength(1);
  });

  it("pings while healthy and closes a socket that went silent", () => {
    const { sock } = make();
    sock.start();
    const ws = FakeWS.instances[0];
    ws.open();
    ws.push(hello());
    vi.advanceTimersByTime(1000);
    expect(ws.sent.map((s) => JSON.parse(s).type)).toContain("ping");
    vi.advanceTimersByTime(6000); // > staleAfterMs with no traffic
    expect(ws.readyState).toBe(3);
    sock.stop();
  });

  it("sends subscribe frames only while open", () => {
    const { sock } = make();
    sock.start();
    const ws = FakeWS.instances[0];
    sock.subscribe("p1");
    expect(ws.sent).toEqual([]);
    ws.open();
    sock.subscribe("p1");
    expect(JSON.parse(ws.sent[0])).toEqual({ type: "subscribe", project_id: "p1" });
    sock.stop();
  });
});
