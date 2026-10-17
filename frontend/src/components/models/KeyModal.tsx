import { faEye, faEyeSlash, faKey, faTrashCan } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useState, type FormEvent } from "react";

import { api } from "../../lib/api";
import type { KeySource } from "../../lib/types";
import { errorText } from "../../state/store";
import { Modal } from "../Modal";
import { ErrorNote, Spinner } from "../Ui";

export interface KeyTarget {
  id: string;
  label: string;
  hint?: string;
  /** Where to get a key, shown as text to copy (the desktop shell does not open links). */
  signup?: string;
  source: KeySource;
}

/**
 * Enter, replace or remove one API key. The key is write-only: it lives in this component's state
 * until it is sent, and nothing that comes back from the backend contains it. Closing the dialog
 * drops it.
 */
export function KeyModal({ target, onClose, onChanged }: { target: KeyTarget; onClose: () => void; onChanged: (id: string) => void | Promise<void> }) {
  const [key, setKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState<"save" | "remove" | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const has = target.source !== "none";
  const canSave = key.trim().length > 0 && busy === null;

  const run = async (kind: "save" | "remove") => {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "save") await api.keys.set(target.id, key.trim());
      else await api.keys.clear(target.id);
    } catch (err) {
      setError(errorText(err));
      setBusy(null);
      setConfirmRemove(false);
      return;
    }
    // saved (or removed): whatever the refresh does, the key is no longer this dialog's business
    setKey("");
    void Promise.resolve(onChanged(target.id)).catch(() => undefined);
    onClose();
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (canSave) void run("save");
  };

  return (
    <Modal
      title={`${target.label} API key`}
      width="max-w-md"
      onClose={() => busy === null && onClose()}
      footer={
        <>
          {target.source === "saved" && (
            <button
              type="button"
              className="btn btn-danger mr-auto"
              disabled={busy !== null}
              onClick={() => (confirmRemove ? void run("remove") : setConfirmRemove(true))}
            >
              {busy === "remove" ? <Spinner /> : <FontAwesomeIcon icon={faTrashCan} />}
              {confirmRemove ? "Click again to remove" : "Remove saved key"}
            </button>
          )}
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy !== null}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void run("save")} disabled={!canSave}>
            {busy === "save" ? <Spinner className="text-[#06202e]" /> : <FontAwesomeIcon icon={faKey} />}
            Save key
          </button>
        </>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        {target.hint && <p className="text-xs text-muted">{target.hint}</p>}
        <div>
          <label htmlFor="api-key-input" className="label">
            {has ? "New key" : "API key"}
          </label>
          <div className="flex gap-2">
            <input
              id="api-key-input"
              className="input font-mono"
              type={reveal ? "text" : "password"}
              value={key}
              onChange={(e) => {
                setKey(e.target.value);
                setError(null);
              }}
              placeholder={has ? "Paste a new key to replace the current one" : "Paste your key"}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              autoFocus
              disabled={busy !== null}
            />
            <button
              type="button"
              className="btn btn-ghost shrink-0"
              onClick={() => setReveal((v) => !v)}
              aria-label={reveal ? "Hide key" : "Show key"}
              aria-pressed={reveal}
            >
              <FontAwesomeIcon icon={reveal ? faEyeSlash : faEye} />
            </button>
          </div>
        </div>
        <ErrorNote message={error} />
        <p className="text-xs text-muted">
          {target.source === "environment"
            ? "A key from an environment variable is in use now. A key saved here takes priority over it."
            : target.source === "saved"
              ? "A key is saved on this computer. It is never shown again; paste a new one to replace it."
              : "The key is stored on this computer only and is never shown again."}
        </p>
        {target.signup && (
          <p className="text-xs text-muted">
            Get a key at <span className="select-all font-mono text-ink/80">{target.signup}</span>
          </p>
        )}
      </form>
    </Modal>
  );
}
