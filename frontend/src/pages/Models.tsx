import { faFloppyDisk } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { HostsPanel } from "../components/models/HostsPanel";
import { HybridStrategyPanel } from "../components/models/HybridStrategyPanel";
import { JevGatekeeperPanel } from "../components/models/JevGatekeeperPanel";
import { normalizeBrain, OPENROUTER_IMAGE_HOST, probeKey, ROLES, sameBrain, useHostProbes, type RoleKey } from "../components/models/modelsUtil";
import { OpenRouterSavingsPanel } from "../components/models/OpenRouterSavingsPanel";
import { ModelSelectionCard } from "../components/models/ModelSelectionCard";
import { ErrorNote, PageHeader, Spinner } from "../components/Ui";
import { api } from "../lib/api";
import type { KeyInfo, ModelsState, RoleBrain } from "../lib/types";
import { issuesByRole, useModelHealth } from "../state/modelHealth";
import { errorText } from "../state/store";

type Drafts = Record<RoleKey, RoleBrain>;

const draftsFrom = (roles: ModelsState["roles"]): Drafts => {
  const out = {} as Drafts;
  for (const r of ROLES) out[r.key] = normalizeBrain(r.key, roles[r.key] ?? { host: "", model: "" });
  return out;
};

export function ModelsPage() {
  const [state, setState] = useState<ModelsState | null>(null);
  const [drafts, setDrafts] = useState<Drafts | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState<RoleKey[]>([]);
  const [roleErrors, setRoleErrors] = useState<Partial<Record<RoleKey, string>>>({});
  const [pageError, setPageError] = useState<string | null>(null);
  const [keys, setKeys] = useState<KeyInfo[]>([]);
  const { probes, probe } = useHostProbes();
  const health = useModelHealth((s) => s.report);
  const checkHealth = useModelHealth((s) => s.check);
  const [params] = useSearchParams();

  const load = useCallback(() => {
    setLoadError(null);
    api.models
      .get()
      .then((s) => {
        setState(s);
        setDrafts(draftsFrom(s.roles));
      })
      .catch((err) => setLoadError(errorText(err)));
  }, []);

  useEffect(load, [load]);

  // Look again whenever this page opens: what the start-up check found may have been fixed since.
  useEffect(() => {
    void checkHealth(true);
  }, [checkHealth]);

  // Key details (hints, where to get one, the optional services) are a nicety: the page works without them.
  const loadKeys = useCallback(() => api.keys.list().then(setKeys).catch(() => undefined), []);
  useEffect(() => {
    void loadKeys();
  }, [loadKeys]);

  /** A key was saved or removed: refresh the hosts (not the role drafts, which may hold unsaved edits) and re-probe. */
  const keyChanged = useCallback(
    async (id: string) => {
      try {
        const [fresh] = await Promise.all([api.models.get(), loadKeys()]);
        setState((s) => (s ? { ...s, hosts: fresh.hosts } : fresh));
        if (fresh.hosts.some((h) => h.id === id)) probe(id, true); // services (Brave) are not hosts
        void checkHealth(true);
      } catch (err) {
        setPageError(errorText(err));
      }
    },
    [loadKeys, probe, checkHealth],
  );

  const hosts = state?.hosts ?? [];

  // Probe every host that can actually answer, plus whichever host a role has picked.
  useEffect(() => {
    for (const h of hosts) if (h.enabled && (!h.cloud || h.configured)) probe(h.id);
  }, [hosts, probe]);
  const pickedHosts = drafts ? ROLES.map((r) => drafts[r.key].host).join("|") : "";
  useEffect(() => {
    for (const id of pickedHosts.split("|")) if (id && hosts.some((h) => h.id === id)) probe(id);
  }, [pickedHosts, hosts, probe]);
  // Images on OpenRouter has its own model list (image-generation models, not chat ones).
  const imagesOnOpenRouter = drafts?.images.host === OPENROUTER_IMAGE_HOST;
  useEffect(() => {
    if (imagesOnOpenRouter) probe(OPENROUTER_IMAGE_HOST, false, { image: true });
  }, [imagesOnOpenRouter, probe]);

  // A role is flagged while it still holds the very model the check found missing; picking another clears it.
  const issues = useMemo(() => issuesByRole(health), [health]);
  const invalidMessage = (role: RoleKey): string | null => {
    const issue = issues[role];
    const d = drafts?.[role];
    return issue && d && d.host === issue.host && d.model.trim() === issue.model ? issue.message : null;
  };

  // Arriving from the notice ("/models?fix=coding,memory"): bring the first flagged card into view.
  const fix = params.get("fix") ?? "";
  const ready = drafts !== null;
  useEffect(() => {
    if (!ready || !fix) return;
    const first = fix.split(",").find((r) => ROLES.some((x) => x.key === r));
    document.getElementById(`role-${first}`)?.scrollIntoView?.({ block: "center" });
  }, [ready, fix]);

  const saved = useMemo(() => (state ? draftsFrom(state.roles) : null), [state]);
  const dirtyKeys = useMemo(
    () => (drafts && saved ? ROLES.filter((r) => !sameBrain(drafts[r.key], saved[r.key])).map((r) => r.key) : []),
    [drafts, saved],
  );

  const change = (role: RoleKey, brain: RoleBrain) => {
    setDrafts((d) => (d ? { ...d, [role]: brain } : d));
    setRoleErrors((e) => (e[role] ? { ...e, [role]: undefined } : e));
    setPageError(null);
  };

  /** Sends only the given roles (all changed ones by default). */
  const save = async (keys: RoleKey[]) => {
    if (!drafts || keys.length === 0) return;
    const body: Partial<ModelsState["roles"]> = {};
    for (const k of keys) body[k] = { host: drafts[k].host, model: drafts[k].model.trim() };
    setSaving(keys);
    setPageError(null);
    setRoleErrors((e) => {
      const next = { ...e };
      for (const k of keys) next[k] = undefined;
      return next;
    });
    try {
      const res = await api.models.setRoles(body);
      setState(res);
      void checkHealth(true); // the picks changed: the problem list may have shrunk
      const fresh = draftsFrom(res.roles);
      setDrafts((d) => {
        if (!d) return fresh;
        const next = { ...d };
        for (const k of keys) next[k] = fresh[k];
        return next;
      });
    } catch (err) {
      const msg = errorText(err);
      if (keys.length === 1) setRoleErrors((e) => ({ ...e, [keys[0]]: msg }));
      else setPageError(msg);
    } finally {
      setSaving([]);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Models"
        subtitle="Choose which host and model each role of the crew runs on."
        actions={
          <button type="button" className="btn btn-primary" onClick={() => void save(dirtyKeys)} disabled={dirtyKeys.length === 0 || saving.length > 0}>
            {saving.length > 0 ? <Spinner className="text-[#06202e]" /> : <FontAwesomeIcon icon={faFloppyDisk} />}
            Save changes
          </button>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-8 py-6">
        <div role="note" className="mb-4 rounded-xl border border-cyan/30 bg-cyan/[0.06] px-4 py-3 text-sm">
          <span className="font-semibold text-cyan">🌐 Global Default Settings</span>
          <span className="text-muted"> — These model selections apply across all crew operations unless specifically overridden in an individual Project&apos;s Settings.</span>
        </div>
        {loadError ? (
          <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-16">
            <ErrorNote message={loadError} />
            <button type="button" className="btn" onClick={load}>
              Try again
            </button>
          </div>
        ) : !state || !drafts ? (
          <div role="status" className="flex items-center justify-center gap-2 py-24 text-sm text-muted">
            <Spinner /> Loading models
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <ErrorNote message={pageError} />
            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="grid gap-4 md:grid-cols-2">
                {ROLES.map((r) => (
                  <ModelSelectionCard
                    key={r.key}
                    meta={r}
                    hosts={hosts}
                    draft={drafts[r.key]}
                    probe={probes[probeKey(drafts[r.key].host, r.key === "images" && drafts[r.key].host === OPENROUTER_IMAGE_HOST)]}
                    dirty={dirtyKeys.includes(r.key)}
                    saving={saving.includes(r.key)}
                    error={roleErrors[r.key] ?? null}
                    invalid={invalidMessage(r.key)}
                    onChange={change}
                    onRefresh={(h, image) => probe(h, true, { image })}
                    onSave={(k) => void save([k])}
                    chatDraft={drafts.chat}
                  />
                ))}
                <JevGatekeeperPanel />
              </div>
              <div className="flex flex-col gap-4">
                <HostsPanel
                  hosts={hosts}
                  probes={probes}
                  keys={Object.fromEntries(keys.map((k) => [k.id, k]))}
                  onRefresh={(id) => probe(id, true)}
                  onKeyChanged={keyChanged}
                />
                <OpenRouterSavingsPanel />
              </div>
            </div>
            <HybridStrategyPanel />
          </div>
        )}
      </div>
    </div>
  );
}
