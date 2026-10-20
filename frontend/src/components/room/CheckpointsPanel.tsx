import { faClockRotateLeft, faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useCallback, useEffect, useState } from "react";

import { api } from "../../lib/api";
import { humanAgo } from "../../lib/time";
import type { Checkpoint } from "../../lib/types";
import { useNow } from "../../lib/useNow";
import { errorText, useStore } from "../../state/store";
import { Avatar } from "../Avatar";

function CheckpointRow({ cp, onRestore, busy }: { cp: Checkpoint; onRestore: (cp: Checkpoint) => void; busy: boolean }) {
  const agent = useStore((s) => s.agents[cp.agent_id]);
  const now = useNow();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const when = humanAgo(new Date(cp.created_at * 1000).toISOString(), now);
  return (
    <li className="card space-y-2 p-3" data-testid="checkpoint-row">
      <div className="flex items-center gap-2">
        {agent && <Avatar agent={agent} size={20} />}
        <span className={open ? "min-w-0 flex-1 text-xs text-ink" : "line-clamp-2 min-w-0 flex-1 text-xs text-ink"} title={cp.label}>
          {cp.label || "Agent turn"}
        </span>
        <span className="shrink-0 text-[11px] text-muted">{when}</span>
      </div>
      <button type="button" className="text-[11px] text-cyan hover:underline" onClick={() => setOpen((v) => !v)}>
        {cp.files.length} file{cp.files.length === 1 ? "" : "s"}
        {cp.commands.length ? `, ${cp.commands.length} command${cp.commands.length === 1 ? "" : "s"}` : ""} · {open ? "Hide" : "See more"}
      </button>
      {open && (
        <ul className="space-y-0.5 font-mono text-[11px] text-muted">
          {cp.files.map((f) => (
            <li key={f} className="truncate" title={f}>
              {f}
            </li>
          ))}
          {cp.commands.map((c, i) => (
            <li key={`c${i}`} className="truncate text-[#febc2e]" title={c}>
              $ {c}
            </li>
          ))}
        </ul>
      )}
      {confirming ? (
        <div className="space-y-2 rounded-md border border-line bg-white/[0.02] p-2 text-[11px] text-muted">
          <p>
            Put the files back the way they were before this turn? This also undoes every later turn, and overwrites changes you made to those files
            since.
            {cp.commands.length > 0 && " Commands that ran (installs, git, deploys) are not undone."}
          </p>
          <div className="flex gap-2">
            <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => onRestore(cp)}>
              Restore
            </button>
            <button type="button" className="btn btn-sm" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="btn btn-sm" aria-label={`Restore to before: ${cp.label}`} onClick={() => setConfirming(true)}>
          <FontAwesomeIcon icon={faClockRotateLeft} />
          Restore to before this
        </button>
      )}
    </li>
  );
}

/** Undo points of the project folder, newest first: one per agent turn that changed files. */
export function CheckpointsPanel({ projectId, refreshKey }: { projectId: string; refreshKey: string }) {
  const [items, setItems] = useState<Checkpoint[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.checkpoints
      .list(projectId)
      .then(setItems)
      .catch(() => setItems([])); // optional: the change list below still works
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const restore = async (cp: Checkpoint) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.checkpoints.restore(projectId, cp.id);
      const left = res.not_undone.length ? ` ${res.not_undone.length} command(s) were not undone.` : "";
      setNotice(`Restored ${res.files.length} file change(s) from ${res.undone.length} turn(s).${left}`);
      load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  if (items.length === 0 && !notice && !error) return null;
  return (
    <section aria-label="Checkpoints" className="space-y-2 px-3 pt-3">
      <h3 className="text-[11px] font-medium uppercase tracking-wide text-muted">Checkpoints</h3>
      {notice && (
        <div role="status" className="rounded-md border border-emerald/30 bg-emerald/10 px-2 py-1.5 text-[11px] text-emerald">
          {notice}
        </div>
      )}
      {error && (
        <div role="alert" className="flex items-center gap-1.5 rounded-md border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-300">
          <FontAwesomeIcon icon={faTriangleExclamation} />
          {error}
        </div>
      )}
      <ul className="space-y-2">
        {items.map((cp) => (
          <CheckpointRow key={cp.id} cp={cp} onRestore={restore} busy={busy} />
        ))}
      </ul>
    </section>
  );
}
