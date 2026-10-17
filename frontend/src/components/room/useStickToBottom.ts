import { useCallback, useLayoutEffect, useRef } from "react";

/**
 * Keep a scroller pinned to its bottom while new content streams in, unless the
 * user scrolled up to read (then leave them alone). `signal` changes whenever
 * the content grows.
 */
export function useStickToBottom<T extends HTMLElement>(signal: unknown) {
  const ref = useRef<T | null>(null);
  const pinned = useRef(true);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [signal]);

  const jump = useCallback(() => {
    const el = ref.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
      pinned.current = true;
    }
  }, []);

  return { ref, onScroll, jump };
}
