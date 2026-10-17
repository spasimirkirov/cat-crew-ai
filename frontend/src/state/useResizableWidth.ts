import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";

interface Options {
  /** localStorage key; the width is a per-viewer convenience, so a failure to read or write is ignored. */
  storageKey: string;
  initial: number;
  min: number;
  /** The widest the panel may get in absolute terms. */
  max: number;
  /** Width that must stay free for what sits beside it (the chat). May change between renders. */
  reserve: number;
  /** Which side of the chat the panel is on. A right-hand panel grows when its edge is dragged to the left. */
  edge?: "left" | "right";
}

const readStored = (key: string): number | null => {
  try {
    const n = Number(window.localStorage.getItem(key));
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
};

/** The widest allowed right now: `max`, but never so wide that less than `reserve` is left for the chat. */
export const limitFor = (o: Pick<Options, "min" | "max" | "reserve">, viewport: number) => Math.max(o.min, Math.min(o.max, viewport - o.reserve));

export const clampWidth = (o: Pick<Options, "min" | "max" | "reserve">, width: number, viewport: number) => Math.round(Math.min(limitFor(o, viewport), Math.max(o.min, width)));

/** A panel width the user can drag (pointer), nudge (arrow keys) or reset (double click / Home). */
export function useResizableWidth(o: Options) {
  const { storageKey, initial, min, max, reserve } = o;
  const sign = o.edge === "right" ? -1 : 1;
  const bounds = useRef({ min, max, reserve });
  bounds.current = { min, max, reserve };
  // What the user last asked for. What is shown is that, held inside today's limits, so a panel squeezed by its
  // neighbour or a small window springs back to its wanted width when the room comes back.
  const [wanted, setWanted] = useState(() => readStored(storageKey) ?? initial);
  const [viewport, setViewport] = useState(() => window.innerWidth);
  const [dragging, setDragging] = useState(false);
  const width = clampWidth(bounds.current, wanted, viewport);

  const commit = useCallback(
    (next: number) => {
      const w = clampWidth(bounds.current, next, window.innerWidth);
      setWanted(w);
      try {
        window.localStorage.setItem(storageKey, String(w));
      } catch {
        /* private window or blocked storage */
      }
    },
    [storageKey],
  );

  // A smaller window must never leave the chat squeezed: track the viewport so the width is re-clamped.
  useEffect(() => {
    const onResize = () => setViewport(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const onPointerDown = useCallback(
    (e: ReactPointerEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const startX = e.clientX;
      const startW = clampWidth(bounds.current, wanted, window.innerWidth);
      setDragging(true);
      const target = (ev: PointerEvent) => startW + sign * (ev.clientX - startX);
      const move = (ev: PointerEvent) => setWanted(clampWidth(bounds.current, target(ev), window.innerWidth));
      const up = (ev: PointerEvent) => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        setDragging(false);
        commit(target(ev));
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    },
    [wanted, commit, sign],
  );

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const step = e.shiftKey ? 48 : 16;
      const grow = sign === 1 ? "ArrowRight" : "ArrowLeft";
      const shrink = sign === 1 ? "ArrowLeft" : "ArrowRight";
      if (e.key === grow) commit(width + step);
      else if (e.key === shrink) commit(width - step);
      else if (e.key === "Home") commit(initial);
      else return;
      e.preventDefault();
    },
    [width, commit, initial, sign],
  );

  return {
    width,
    dragging,
    max: limitFor(bounds.current, viewport),
    reset: () => commit(initial),
    handleProps: { onPointerDown, onKeyDown, onDoubleClick: () => commit(initial) },
  };
}
