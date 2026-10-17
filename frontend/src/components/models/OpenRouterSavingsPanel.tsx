import { faBolt } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import type { OpenRouterSettings } from "../../lib/types";
import { errorText } from "../../state/store";
import { ErrorNote, Spinner, Toggle } from "../Ui";

const SORTS: { id: OpenRouterSettings["provider_sort"]; label: string }[] = [
  { id: "", label: "OpenRouter default (price-weighted, auto)" },
  { id: "price", label: "Lowest price" },
  { id: "throughput", label: "Highest throughput" },
  { id: "latency", label: "Lowest latency" },
];

/**
 * OpenRouter savings and performance: which provider a request routes to, the optional
 * context-compression plugin, and routing lightweight background calls to a free model. Prompt
 * caching itself needs no toggle here — it is on by default per model (Telemetry shows the
 * savings); "Fastest/cheapest, automatically" is OpenRouter's own default behaviour (no `:auto`
 * model suffix exists) and its provider `sort` here.
 */
export function OpenRouterSavingsPanel() {
  const [state, setState] = useState<OpenRouterSettings | null>(null);
  const [draft, setDraft] = useState<OpenRouterSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    api.openrouter
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

  const dirty = Boolean(state && draft && JSON.stringify(state) !== JSON.stringify(draft));

  const save = async (next: OpenRouterSettings) => {
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await api.openrouter.setSettings(next);
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
        <Spinner /> Loading OpenRouter savings
      </section>
    );
  }

  return (
    <section aria-labelledby="orsave-h" className="card flex flex-col gap-3 p-5">
      <div className="flex items-center gap-2">
        <FontAwesomeIcon icon={faBolt} className="text-cyan" aria-hidden />
        <h2 id="orsave-h" className="text-sm font-semibold">
          OpenRouter savings
        </h2>
      </div>
      <p className="text-xs text-muted">Prompt caching runs automatically per model (see Telemetry for the savings). These knobs are optional extras.</p>

      <div className="flex flex-col gap-1 text-xs">
        <label htmlFor="or-provider-sort" className="font-medium text-ink">
          Provider routing
        </label>
        <select
          id="or-provider-sort"
          aria-label="Provider routing"
          className="input"
          value={draft.provider_sort}
          disabled={saving}
          onChange={(e) => void save({ ...draft, provider_sort: e.target.value as OpenRouterSettings["provider_sort"] })}
        >
          {SORTS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        <span className="text-muted">
          Tip: appending <code className="font-mono">:nitro</code> (fastest) or <code className="font-mono">:floor</code> (cheapest) to a model slug
          picks a provider for that one model, same as OpenRouter's own site.
        </span>
      </div>

      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-ink">Allow fallback providers</div>
          <div className="text-xs text-muted">If the chosen provider is down, let OpenRouter try another.</div>
        </div>
        <Toggle label="Allow fallback providers" on={draft.allow_fallbacks} disabled={saving} onChange={(v) => void save({ ...draft, allow_fallbacks: v })} />
      </div>

      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-ink">Compress over-length prompts</div>
          <div className="text-xs text-muted">OpenRouter's "context-compression" plugin. Can drop or truncate older messages — off by default.</div>
        </div>
        <Toggle label="Compress over-length prompts" on={draft.compression_enabled} disabled={saving} onChange={(v) => void save({ ...draft, compression_enabled: v })} />
      </div>

      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-ink">Free-tier for background calls</div>
          <div className="text-xs text-muted">Route lightweight calls (context-summary compaction) to a free model instead of your chosen one.</div>
        </div>
        <Toggle label="Free-tier for background calls" on={draft.free_tier_enabled} disabled={saving} onChange={(v) => void save({ ...draft, free_tier_enabled: v })} />
      </div>

      {draft.free_tier_enabled && (
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium text-ink">Free-tier model</span>
          <input
            type="text"
            className="input font-mono"
            value={draft.free_tier_model}
            disabled={saving}
            onChange={(e) => setDraft((d) => (d ? { ...d, free_tier_model: e.target.value } : d))}
            onBlur={() => draft.free_tier_model.trim() && dirty && void save(draft)}
            placeholder="meta-llama/llama-3.3-70b-instruct:free"
          />
        </label>
      )}

      <ErrorNote message={saveError} />
    </section>
  );
}
