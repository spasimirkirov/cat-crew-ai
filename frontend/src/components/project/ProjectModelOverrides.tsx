import { faFloppyDisk, faRotateLeft } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useCallback, useEffect, useId, useMemo, useState } from "react";

import { api } from "../../lib/api";
import type { EffectiveModel, ModelHost, ProjectModelsState, RoleBrain, RoleName } from "../../lib/types";
import { errorText } from "../../state/store";
import { ErrorNote, Spinner } from "../Ui";
import { ModelSelectionCard } from "../models/ModelSelectionCard";
import { COMFY_HOST, INHERIT, OPENROUTER_IMAGE_HOST, probeKey, ROLE_OFF, ROLES, sameBrain, useHostProbes } from "../models/modelsUtil";

export { INHERIT };

type Drafts = Record<RoleName, RoleBrain>;

const draftsFrom = (overrides: ProjectModelsState["overrides"]): Drafts => {
  const out = {} as Drafts;
  for (const r of ROLES) out[r.key] = overrides[r.key] ?? { host: INHERIT, model: "" };
  return out;
};

/** "OpenRouter: deepseek/deepseek-v4-pro", or "not set" when the role has no model anywhere. */
export function describeModel(eff: EffectiveModel | undefined, hosts: ModelHost[]): string {
  if (!eff || !eff.host) return "not set";
  if (eff.host === ROLE_OFF) return "Off";
  const host = hosts.find((h) => h.id === eff.host)?.label ?? (eff.host === COMFY_HOST ? "ComfyUI" : eff.host);
  return eff.model ? `${host}: ${eff.model}` : host;
}

/**
 * "Project Model Overrides": per role, run this project on its own host and model, or leave it on the
 * global pick from the Models page. Saved per project; other projects are untouched.
 */
export function ProjectModelOverrides({ projectId }: { projectId: string }) {
  const uid = useId();
  const [state, setState] = useState<ProjectModelsState | null>(null);
  const [drafts, setDrafts] = useState<Drafts | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Partial<Record<RoleName, string>>>({});
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const { probes, probe } = useHostProbes();

  const load = useCallback(() => {
    setLoadError(null);
    api.models
      .getProject(projectId)
      .then((s) => {
        setState(s);
        setDrafts(draftsFrom(s.overrides));
      })
      .catch((err) => setLoadError(errorText(err)));
  }, [projectId]);
  useEffect(load, [load]);

  const hosts = useMemo(() => state?.hosts ?? [], [state]);
  const picked = drafts ? ROLES.map((r) => drafts[r.key].host).join("|") : "";
  useEffect(() => {
    for (const id of picked.split("|")) if (id && hosts.some((h) => h.id === id)) probe(id);
  }, [picked, hosts, probe]);
  // Images on OpenRouter has its own model list (image-generation models, not chat ones).
  const imagesOnOpenRouter = drafts?.images.host === OPENROUTER_IMAGE_HOST;
  useEffect(() => {
    if (imagesOnOpenRouter) probe(OPENROUTER_IMAGE_HOST, false, { image: true });
  }, [imagesOnOpenRouter, probe]);

  const saved = useMemo(() => (state ? draftsFrom(state.overrides) : null), [state]);
  const dirty = useMemo(() => (drafts && saved ? ROLES.filter((r) => !sameBrain(drafts[r.key], saved[r.key])).map((r) => r.key) : []), [drafts, saved]);
  const anyOverride = drafts ? ROLES.some((r) => drafts[r.key].host !== INHERIT) : false;

  const change = (role: RoleName, brain: RoleBrain) => {
    setDrafts((d) => (d ? { ...d, [role]: brain } : d));
    setRowErrors((e) => (e[role] ? { ...e, [role]: undefined } : e));
    setSaveError(null);
    setJustSaved(false);
  };

  const save = async () => {
    if (!drafts || dirty.length === 0) return;
    const errors: Partial<Record<RoleName, string>> = {};
    const roles: Partial<Record<RoleName, RoleBrain | null>> = {};
    for (const key of dirty) {
      const d = drafts[key];
      if (d.host === INHERIT) roles[key] = null;
      else if (d.host === ROLE_OFF) roles[key] = { host: ROLE_OFF, model: "" };
      else if (!d.model.trim()) errors[key] = "Pick a model, or choose Use Global Setting.";
      else roles[key] = { host: d.host, model: d.model.trim() };
    }
    setRowErrors(errors);
    if (Object.keys(errors).length > 0) return;
    setSaving(true);
    setSaveError(null);
    try {
      const res = await api.models.setProject(projectId, roles);
      setState(res);
      setDrafts(draftsFrom(res.overrides));
      setJustSaved(true);
    } catch (err) {
      setSaveError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section aria-labelledby={`${uid}-h`} className="card p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id={`${uid}-h`} className="text-sm font-semibold">
            Project Model Overrides
          </h2>
          <p className="mt-0.5 max-w-2xl text-xs text-muted">
            Run this project on its own models. A role left on <span className="text-ink">Use Global Setting</span> follows the Models page, and follows any change made there.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {anyOverride && (
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => drafts && setDrafts(draftsFrom({}))} title="Put every role back on the global setting (then Save)">
              <FontAwesomeIcon icon={faRotateLeft} /> Use global for all
            </button>
          )}
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void save()} disabled={dirty.length === 0 || saving}>
            {saving ? <Spinner className="text-[#06202e]" /> : <FontAwesomeIcon icon={faFloppyDisk} />} Save overrides
          </button>
        </div>
      </header>

      <div className="mt-4 flex flex-col gap-3">
        <ErrorNote message={saveError} />
        {justSaved && dirty.length === 0 && (
          <p role="status" className="text-xs text-emerald">
            Saved. New turns in this project use these models.
          </p>
        )}
        {loadError ? (
          <div className="flex flex-col items-center gap-3 py-8">
            <ErrorNote message={loadError} />
            <button type="button" className="btn" onClick={load}>
              Try again
            </button>
          </div>
        ) : !state || !drafts ? (
          <div role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
            <Spinner /> Loading models
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {ROLES.map((meta) => {
              const globalLabel = describeModel(state.global_effective[meta.key], hosts);
              const draft = drafts[meta.key];
              // "Same As Chat" copies what Chat actually runs on (its effective model), not this
              // project's own Chat override -- which is usually empty/inherit and not worth copying.
              const chatEffective = state.effective.chat;
              return (
                <ModelSelectionCard
                  key={meta.key}
                  meta={meta}
                  hosts={hosts}
                  draft={draft}
                  probe={probes[probeKey(draft.host, meta.key === "images" && draft.host === OPENROUTER_IMAGE_HOST)]}
                  dirty={dirty.includes(meta.key)}
                  saving={false}
                  error={rowErrors[meta.key] ?? null}
                  onChange={(_, b) => change(meta.key, b)}
                  onRefresh={(h, image) => probe(h, true, { image })}
                  chatDraft={chatEffective.host ? { host: chatEffective.host, model: chatEffective.model } : undefined}
                  inheritLabel={`🌐 Use Global Setting (${globalLabel})`}
                  inheritModelLabel={globalLabel === "not set" ? "Nothing set globally" : globalLabel.split(": ").slice(1).join(": ") || globalLabel}
                  overridden={meta.key in state.overrides}
                />
              );
            })}
          </div>
        )}
      </div>
      <p className={clsx("mt-4 text-xs text-muted")}>Long-term memory recall and the start-up model check always use the global models.</p>
    </section>
  );
}
