import { faCat, faRotateRight, faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useState, type ReactNode } from "react";

import { useStore } from "../state/store";
import { Spinner } from "./Ui";

/** After this long a quiet "still starting" note appears, so a slow first launch does not look frozen. */
export const SLOW_START_MS = 6000;

/**
 * What the window shows until the backend has answered once. The engine takes a few seconds to start with
 * the app, so waiting is the normal state here: a spinner, never an error. Only a start that has clearly
 * failed (the shell says so, or the engine stays silent for too long) turns into a message with Try again.
 */
export function StartupScreen() {
  const boot = useStore((s) => s.boot);
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    setSlow(false);
    if (boot.phase !== "starting") return;
    const t = setTimeout(() => setSlow(true), SLOW_START_MS);
    return () => clearTimeout(t);
  }, [boot.phase, boot.since]);

  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-6 bg-bg px-6 text-ink" data-testid="startup" data-phase={boot.phase}>
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan/30 to-violet/30 text-3xl">
        <FontAwesomeIcon icon={faCat} className="text-cyan" aria-hidden />
      </div>
      <h1 className="text-lg font-semibold tracking-tight">CatCrewAI</h1>

      {boot.phase === "starting" ? (
        <div role="status" aria-live="polite" className="flex flex-col items-center gap-2 text-sm text-muted">
          <span className="flex items-center gap-2">
            <Spinner /> Starting the engine…
          </span>
          {slow && <span className="max-w-xs text-center text-xs text-muted/80">Still starting. The first launch can take a little longer.</span>}
        </div>
      ) : (
        <div className="flex w-full max-w-md flex-col items-center gap-3">
          <div role="alert" className="flex w-full items-start gap-2 rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
            <FontAwesomeIcon icon={faTriangleExclamation} className="mt-0.5 shrink-0" aria-hidden />
            <span>{boot.error ?? "The CatCrewAI engine is not answering."}</span>
          </div>
          {boot.detail && (
            <details className="w-full text-xs text-muted">
              <summary className="cursor-pointer select-none">Details</summary>
              <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-[#0b0e15] p-2 font-mono text-[11px] leading-relaxed">{boot.detail}</pre>
            </details>
          )}
          <p className="text-center text-xs text-muted">
            The desktop app starts the engine for you. If you run it yourself, start it with <code className="rounded bg-white/[0.07] px-1 py-0.5 font-mono">python -m backend</code> and try again.
          </p>
          <button type="button" className="btn btn-primary" onClick={() => void useStore.getState().connect()}>
            <FontAwesomeIcon icon={faRotateRight} /> Try again
          </button>
        </div>
      )}
    </div>
  );
}

/** Renders the app once the backend has answered; the start-up screen until then. */
export function BootGate({ children }: { children: ReactNode }) {
  const ready = useStore((s) => s.ready);
  return ready ? <>{children}</> : <StartupScreen />;
}
