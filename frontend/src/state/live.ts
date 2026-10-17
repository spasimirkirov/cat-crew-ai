import { WS_BASE } from "../lib/config";
import { useStore } from "./store";
import { EventSocket } from "./ws";

let socket: EventSocket | null = null;

/** Open the single app-wide event socket (idempotent). */
export function startLive(): void {
  if (socket) return;
  socket = new EventSocket({
    url: `${WS_BASE}/ws/events`,
    onEvent: (ev) => useStore.getState().applyEvent(ev),
    onStatus: (connection) => useStore.setState({ connection }),
    onHello: () => {
      void useStore.getState().resync();
    },
  });
  socket.start();
  // The engine may still be starting: the handshake keeps asking quietly (the splash screen shows it) and
  // only reports a failure once the engine has clearly failed or stayed silent for too long.
  void useStore.getState().connect();
}

export function stopLive(): void {
  socket?.stop();
  socket = null;
}
