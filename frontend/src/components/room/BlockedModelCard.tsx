import { faRotateRight, faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useId, useMemo, useState } from "react";

import { api } from "../../lib/api";
import type { BlockedModel, ModelHost } from "../../lib/types";
import { errorText } from "../../state/store";
import { ErrorNote, Spinner } from "../Ui";
import { ModelPicker } from "../models/ModelPicker";
import { probeKey, useHostProbes } from "../models/modelsUtil";

/**
 * Shown when a turn stopped because the host turned its model away (`turn.failed` with
 * `model_issue`): rate-limited -- a `:free` OpenRouter model is the usual case -- or out of credit.
 * The crew stops at the first refusal instead of walking every phase into it; this card lets the
 * person pick another model, switch every role that uses the blocked one (globally and in every
 * project, `POST /api/models/replace`), and run the same message again (`POST .../retry`).
 */
export function BlockedModelCard({ projectId, issue }: { projectId: string; issue: BlockedModel }) {
  const uid = useId();
  const [hosts, setHosts] = useState<ModelHost[] | null>(null);
  const [host, setHost] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const { probes, probe } = useHostProbes();

  useEffect(() => {
    let alive = true;
    api.models
      .get()
      .then((state) => {
        if (!alive) return;
        const usable = state.hosts.filter((h) => h.configured && h.enabled !== false);
        setHosts(usable);
        setHost(usable.some((h) => h.id === issue.host) ? issue.host : usable[0]?.id ?? "");
      })
      .catch((err) => alive && setError(errorText(err)));
    return () => {
      alive = false;
    };
  }, [issue.host]);

  useEffect(() => {
    if (host) probe(host);
  }, [host, probe]);

  const probeNow = host ? probes[probeKey(host)] : undefined;
  // Offer anything but the blocked model itself.
  const models = useMemo(
    () => (probeNow?.data?.models ?? []).filter((m) => !(host === issue.host && m.name === issue.model)),
    [probeNow, host, issue.host, issue.model],
  );

  if (dismissed) return null;

  const switchAndRetry = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.models.replace({ from_host: issue.host, from_model: issue.model, to_host: host, to_model: model });
      await api.chat.retry(projectId);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  const what = issue.kind === "no_credit" ? "is out of credit" : "is rate-limited";
  const why = issue.free
    ? "It is a free model: the host allows only a few requests a minute and a day, and a crew run needs many. The crew stopped instead of retrying."
    : issue.kind === "no_credit"
      ? "The account has no credit left for it. The crew stopped instead of retrying."
      : "The host is refusing more requests for now. The crew stopped instead of retrying.";
  const usedBy = issue.roles.map((r) => (r.source === "project" ? `${r.label} (this project)` : r.label)).join(", ");

  return (
    <section role="alert" aria-labelledby={`${uid}-h`} className="rounded-lg border border-warn/40 bg-warn/[0.07] p-3" data-testid="blocked-model">
      <div className="flex items-start gap-2">
        <FontAwesomeIcon icon={faTriangleExclamation} className="mt-0.5 text-warn" />
        <div className="min-w-0 flex-1">
          <h3 id={`${uid}-h`} className="text-sm font-medium">
            <code className="rounded bg-white/10 px-1 font-mono text-xs">{issue.model}</code> {what}
          </h3>
          <p className="mt-1 text-xs text-muted">{why}</p>
          {usedBy && <p className="mt-1 text-xs text-muted">Used by: {usedBy}.</p>}
        </div>
      </div>

      {hosts === null ? (
        <div className="mt-3 flex items-center gap-2 text-xs text-muted">
          <Spinner /> Loading hosts
        </div>
      ) : hosts.length === 0 ? (
        <p className="mt-3 text-xs text-warn">No host has a key yet. Add one under Models → Hosts, then switch.</p>
      ) : (
        <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)]">
          <select
            className="input"
            aria-label="Switch to host"
            value={host}
            disabled={busy}
            onChange={(e) => {
              setHost(e.target.value);
              setModel("");
            }}
          >
            {hosts.map((h) => (
              <option key={h.id} value={h.id}>
                {h.label}
              </option>
            ))}
          </select>
          {models.length > 0 ? (
            <div>
              <label htmlFor={`${uid}-model`} className="sr-only">
                Model
              </label>
              <ModelPicker id={`${uid}-model`} models={models} value={model} onChange={setModel} />
            </div>
          ) : probeNow?.status === "loading" || !probeNow ? (
            <div className="flex items-center gap-2 text-xs text-muted">
              <Spinner /> Loading models
            </div>
          ) : (
            <input className="input" aria-label="Model" placeholder="Type a model name" value={model} disabled={busy} onChange={(e) => setModel(e.target.value)} />
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className="btn btn-sm btn-primary" disabled={busy || !host || !model.trim()} onClick={() => void switchAndRetry()}>
          {busy ? <Spinner className="text-[#06202e]" /> : <FontAwesomeIcon icon={faRotateRight} />} Switch everywhere &amp; retry
        </button>
        <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setDismissed(true)}>
          Dismiss
        </button>
      </div>
      <ErrorNote message={error} />
    </section>
  );
}
