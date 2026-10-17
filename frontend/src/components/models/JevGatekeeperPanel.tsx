import { faScaleBalanced, faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useId, useState } from "react";

import { api } from "../../lib/api";
import type { JevSettings } from "../../lib/types";
import { errorText } from "../../state/store";
import { ErrorNote, Spinner, Toggle } from "../Ui";
import { KeyBadge } from "./KeyBadge";

/**
 * Jev ("System One"), TypeSafe AI's decision model: fast yes/no checks that spare the harness whole
 * model calls -- the clarify rewrite of a request that is already clear, and the skill-proposal call
 * after a one-off task. Laid out like a role card: a host and a model. Jev is the only decision
 * model; it runs on OpenRouter's Decisions API (the OpenRouter key) or on TypeSafe's official API
 * (the TypeSafe key). It keeps no key of its own: both keys live with every other key under Hosts.
 * The tool-call guard and the "no tools needed" precheck are opt-in: the harness evals measured both
 * as worse than no gate.
 */
export function JevGatekeeperPanel() {
  const uid = useId();
  const [state, setState] = useState<JevSettings | null>(null);
  const [draft, setDraft] = useState<JevSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    api.jev
      .getSettings()
      .then((s) => {
        if (!alive) return;
        setState(s);
        setDraft(s);
      })
      .catch((err) => alive && setLoadError(errorText(err)));
    return () => {
      alive = false;
    };
  }, []);

  const dirty = Boolean(state && draft && state.confidence_threshold !== draft.confidence_threshold);

  const save = async (next: JevSettings) => {
    setSaving(true);
    setSaveError(null);
    setDraft(next);
    try {
      const saved = await api.jev.setSettings({
        enabled: next.enabled,
        backend: next.backend,
        confidence_threshold: next.confidence_threshold,
        guard_tool_calls: next.guard_tool_calls,
        skip_tools_precheck: next.skip_tools_precheck,
      });
      setState(saved);
      setDraft(saved);
    } catch (err) {
      setSaveError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  if (loadError) return <ErrorNote message={loadError} />;
  if (!draft) {
    return (
      <section className="card flex items-center justify-center gap-2 p-5 text-sm text-muted">
        <Spinner /> Loading Jev
      </section>
    );
  }

  const onTypeSafe = draft.backend === "typesafe";

  return (
    <section aria-labelledby={`${uid}-h`} className="card flex flex-col gap-4 p-5">
      <header className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line bg-white/[0.03] text-cyan">
          <FontAwesomeIcon icon={faScaleBalanced} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id={`${uid}-h`} className="text-sm font-semibold">
            Jev gatekeeper
          </h2>
          <p className="text-xs text-muted">Fast yes/no checks that save whole model calls: no rewrite of a clear request, no skill proposal for a one-off task.</p>
        </div>
        <Toggle label="Enable Jev gatekeeper" on={draft.enabled} disabled={saving} onChange={(v) => void save({ ...draft, enabled: v })} />
      </header>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="label !mb-0" htmlFor={`${uid}-host`}>
            Host
          </label>
          <KeyBadge source={draft.key_source} required />
        </div>
        <select
          id={`${uid}-host`}
          className="input"
          value={draft.backend}
          disabled={saving}
          onChange={(e) => void save({ ...draft, backend: e.target.value as JevSettings["backend"] })}
        >
          <option value="openrouter">OpenRouter</option>
          <option value="typesafe">TypeSafe (official API)</option>
        </select>
        {draft.key_source === "none" && (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-warn">
            <FontAwesomeIcon icon={faTriangleExclamation} />
            {onTypeSafe ? "No TypeSafe key is saved yet. Add it in Models → Hosts." : "No OpenRouter key is configured yet. Add one in Models → Hosts."}
          </p>
        )}
      </div>

      <div>
        <label className="label" htmlFor={`${uid}-model`}>
          Model
        </label>
        <input id={`${uid}-model`} className="input" value={draft.model} readOnly disabled />
      </div>

      <label className="flex flex-col gap-1.5 text-xs">
        <span className="flex items-center justify-between">
          <span className="font-medium text-ink">Confidence threshold</span>
          <span className="tabular-nums text-muted">{Math.round(draft.confidence_threshold * 100)}%</span>
        </span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={draft.confidence_threshold}
          disabled={saving}
          aria-label="Confidence threshold"
          onChange={(e) => setDraft((d) => (d ? { ...d, confidence_threshold: Number(e.target.value) } : d))}
          onMouseUp={() => dirty && void save(draft)}
          onTouchEnd={() => dirty && void save(draft)}
          onKeyUp={() => dirty && void save(draft)}
        />
        <span className="text-muted">Jev only acts when it is at least this sure. Below it, everything runs as if Jev were off.</span>
      </label>

      <div className="flex flex-col gap-2 text-xs">
        <div className="flex items-center gap-2">
          <Toggle
            label="Block tool calls Jev is sure are unsafe"
            on={draft.guard_tool_calls}
            disabled={saving}
            onChange={(v) => void save({ ...draft, guard_tool_calls: v })}
          />
          <span className="text-ink">Block tool calls Jev is sure are unsafe</span>
        </div>
        <div className="flex items-center gap-2">
          <Toggle
            label="Drop tool schemas from turns that need no tools"
            on={draft.skip_tools_precheck}
            disabled={saving}
            onChange={(v) => void save({ ...draft, skip_tools_precheck: v })}
          />
          <span className="text-ink">Drop tool schemas from turns that need no tools</span>
        </div>
        <span className="text-muted">
          Both off by default: the guard sees no files, so it doubts most real edits, and dropping schemas costs more in lost
          prompt-cache hits than it saves.
        </span>
      </div>

      <ErrorNote message={saveError} />
    </section>
  );
}
