import { faPowerOff } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useState } from "react";

import { api } from "../lib/api";
import { isBrowserMode, isTauri } from "../lib/config";
import { quitApp } from "../lib/window";
import { useStore } from "../state/store";
import { Modal } from "./Modal";
import { ErrorNote, Spinner } from "./Ui";

/**
 * "Power Off" for the sidebar footer. The window's X only hides CatCrewAI to the system tray;
 * this is the explicit way to shut down the UI *and* the Python engine behind it.
 * In the desktop shell it quits the app; in browser mode (`python -m backend --ui`) it stops the
 * engine that served the tab. A tab on the Vite dev server has nothing of its own to quit.
 */
export function PowerOff() {
  const [asking, setAsking] = useState(false);
  const [quitting, setQuitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const working = useStore((s) => Object.values(s.activity).some((a) => a.state === "working"));

  const desktop = isTauri();
  if (!desktop && !isBrowserMode()) return null;

  const close = () => {
    if (quitting) return;
    setAsking(false);
    setError(null);
  };

  const confirm = async () => {
    setQuitting(true);
    setError(null);
    try {
      if (desktop) {
        await quitApp(); // the process exits; on success this window simply goes away
      } else {
        await api.shutdown(); // the engine stops; this tab stays, with nothing behind it
        setStopped(true);
      }
    } catch (e) {
      setQuitting(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <button
        type="button"
        title="Power off"
        aria-label="Power off"
        onClick={() => setAsking(true)}
        className="flex h-8 w-8 items-center justify-center rounded-lg text-[13px] text-muted transition hover:bg-danger/10 hover:text-danger focus:outline-none focus-visible:ring-2 focus-visible:ring-danger/50"
      >
        <FontAwesomeIcon icon={faPowerOff} />
      </button>
      {asking && (
        <Modal
          title="Power off CatCrewAI"
          width="max-w-sm"
          onClose={close}
          footer={
            <>
              <button className="btn btn-ghost" onClick={close} disabled={quitting}>
                Cancel
              </button>
              <button className="btn btn-danger" onClick={confirm} disabled={quitting}>
                {quitting ? <Spinner /> : <FontAwesomeIcon icon={faPowerOff} />}
                {quitting ? "Shutting down…" : "Shut down"}
              </button>
            </>
          }
        >
          {stopped ? (
            <p className="text-sm text-ink">CatCrewAI is stopped. You can close this tab; start it again with python -m backend --ui.</p>
          ) : (
            <>
              <p className="text-sm text-ink">Shut down CatCrewAI and stop background engine?</p>
              <p className="mt-2 text-xs text-muted">
                {desktop
                  ? "Closing the window with X only hides the app to the system tray. Powering off quits it completely."
                  : "Closing this tab leaves the engine running. Powering off stops it."}
                {working && " The crew is still working — that run will be stopped."}
              </p>
            </>
          )}
          <div className="mt-2">
            <ErrorNote message={error} />
          </div>
        </Modal>
      )}
    </>
  );
}
