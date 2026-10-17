import { faArrowUp, faFolder, faFolderOpen, faFolderPlus } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { api } from "../../lib/api";
import type { BrowseResult } from "../../lib/types";
import { errorText } from "../../state/store";
import { Modal } from "../Modal";
import { ErrorNote, Spinner } from "../Ui";

interface PanelProps {
  startPath?: string;
  pickLabel?: string;
  /** Called with the folder the user chose. May be async; a thrown error is shown inline. */
  onPick: (path: string) => void | Promise<void>;
  onCancel: () => void;
}

/** Folder chooser built on `GET /api/workspaces/browse`. Only folders are listed. */
export function FolderBrowserPanel({ startPath = "", pickLabel = "Use this folder", onPick, onCancel }: PanelProps) {
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  // "New folder": an inline name field under the path bar; the folder is made inside the current one.
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [making, setMaking] = useState(false);

  const load = useCallback(async (path: string, fallbackHome = false) => {
    const mine = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.projects.browse(path);
      if (mine === seq.current) setResult(res);
    } catch (err) {
      if (mine !== seq.current) return;
      if (fallbackHome && path) {
        // The typed path does not exist (yet): start from the default location instead.
        void load("");
        return;
      }
      setError(errorText(err));
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(startPath.trim(), true);
    return () => {
      seq.current += 1; // drop any in-flight response after unmount
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const folders = (result?.entries ?? []).filter((e) => e.is_dir);

  const stopCreating = () => {
    setCreating(false);
    setNewName("");
    setCreateError(null);
  };

  const makeFolder = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!result || making) return;
    const name = newName.trim();
    if (!name) {
      setCreateError("Type a name for the new folder.");
      return;
    }
    setMaking(true);
    setCreateError(null);
    try {
      const made = await api.projects.createFolder(result.path, name);
      seq.current += 1; // a listing still in flight is for the old folder
      setResult(made); // step into the new folder, ready to be chosen
      setLoading(false);
      stopCreating();
    } catch (err) {
      setCreateError(errorText(err));
    } finally {
      setMaking(false);
    }
  };

  const pick = async () => {
    if (!result) return;
    setBusy(true);
    setError(null);
    try {
      await onPick(result.path);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn btn-sm shrink-0"
          onClick={() => result?.parent && void load(result.parent)}
          disabled={!result?.parent || loading || busy}
        >
          <FontAwesomeIcon icon={faArrowUp} /> Up
        </button>
        <button
          type="button"
          className="btn btn-sm shrink-0"
          onClick={() => (creating ? stopCreating() : setCreating(true))}
          disabled={!result?.path || loading || busy}
          aria-expanded={creating}
          title={result?.path ? "Create a new folder here" : "Open a drive first, then create the folder inside it"}
        >
          <FontAwesomeIcon icon={faFolderPlus} /> New folder
        </button>
        <div
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-line bg-[#0f121a] px-3 py-1.5 font-mono text-xs"
          title={result?.path}
          data-testid="browse-path"
        >
          <FontAwesomeIcon icon={faFolderOpen} className="shrink-0 text-cyan" />
          <span className="truncate">{result?.path ?? "..."}</span>
        </div>
      </div>

      {creating && (
        <form onSubmit={(e) => void makeFolder(e)} className="flex flex-col gap-1.5" aria-label="New folder">
          <div className="flex items-center gap-2">
            <FontAwesomeIcon icon={faFolderPlus} className="shrink-0 text-violet" aria-hidden />
            <input
              className="input !py-1.5 text-sm"
              value={newName}
              onChange={(e) => {
                setNewName(e.target.value);
                if (createError) setCreateError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.stopPropagation(); // close this field, not the whole dialog
                  stopCreating();
                }
              }}
              placeholder="New folder name"
              aria-label="New folder name"
              aria-invalid={createError ? true : undefined}
              maxLength={120}
              spellCheck={false}
              autoComplete="off"
              autoFocus
            />
            <button type="submit" className="btn btn-sm btn-primary shrink-0" disabled={making}>
              {making && <Spinner className="text-[#06202e]" />}
              Create
            </button>
            <button type="button" className="btn btn-sm shrink-0" onClick={stopCreating} disabled={making}>
              Cancel
            </button>
          </div>
          {createError && (
            <p role="alert" className="text-xs text-danger">
              {createError}
            </p>
          )}
        </form>
      )}

      <div className="min-h-[220px] rounded-lg border border-line bg-[#0f121a]">
        {loading && !result ? (
          <div className="flex h-[220px] items-center justify-center gap-2 text-sm text-muted">
            <Spinner /> Loading folders
          </div>
        ) : (
          <ul aria-label="Sub-folders" aria-busy={loading} className="max-h-[300px] overflow-y-auto py-1">
            {folders.length === 0 && !loading && <li className="px-4 py-8 text-center text-sm text-muted">No sub-folders here.</li>}
            {folders.map((f) => (
              <li key={f.path}>
                <button
                  type="button"
                  onClick={() => void load(f.path)}
                  disabled={busy}
                  className="flex w-full items-center gap-3 px-3 py-1.5 text-left text-sm transition hover:bg-white/5 focus:outline-none focus-visible:bg-white/5 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan/60"
                >
                  <FontAwesomeIcon icon={faFolder} className="shrink-0 text-violet" />
                  <span className="truncate">{f.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ErrorNote message={error} />

      <div className="flex justify-end gap-2">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="btn btn-primary" onClick={() => void pick()} disabled={!result || loading || busy}>
          {busy && <Spinner className="text-[#06202e]" />}
          {pickLabel}
        </button>
      </div>
    </div>
  );
}

interface ModalProps extends Omit<PanelProps, "onCancel"> {
  title?: string;
  onClose: () => void;
}

export function FolderBrowserModal({ title = "Choose a folder", onClose, ...panel }: ModalProps) {
  return (
    <Modal title={title} onClose={onClose} width="max-w-xl">
      <FolderBrowserPanel {...panel} onCancel={onClose} />
    </Modal>
  );
}
