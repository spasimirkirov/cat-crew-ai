import { faBolt, faBrain, faEye, faGift, faScrewdriverWrench } from "@fortawesome/free-solid-svg-icons";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";

interface Style {
  label: string;
  title: string;
  icon: IconDefinition;
  tone: string;
}

/** Colour-coded badges. `chat` is true of every listed model, so it is not shown. */
const STYLES: Record<string, Style> = {
  vision: { label: "Vision", title: "Accepts images", icon: faEye, tone: "border-violet/40 bg-violet/10 text-violet" },
  reasoning: { label: "Reasoning", title: "Thinks step by step before answering", icon: faBrain, tone: "border-warn/40 bg-warn/10 text-warn" },
  tools: { label: "Tools", title: "Supports tool (function) calling", icon: faScrewdriverWrench, tone: "border-cyan/40 bg-cyan/10 text-cyan" },
};

export const BADGE_ORDER = ["vision", "reasoning", "tools"] as const;

export function CapabilityBadge({ cap, compact }: { cap: string; compact?: boolean }) {
  const s = STYLES[cap];
  if (!s) return null;
  return (
    <span className={clsx("chip !py-0 text-[10px]", s.tone)} title={s.title} data-cap={cap}>
      <FontAwesomeIcon icon={s.icon} className="text-[9px]" aria-hidden />
      {compact ? <span className="sr-only">{s.label}</span> : s.label}
    </span>
  );
}

/** A model that costs nothing on OpenRouter (a `:free` variant, or zero pricing both ways). */
export function FreeBadge({ compact }: { compact?: boolean }) {
  return (
    <span className="chip !py-0 text-[10px] border-emerald/40 bg-emerald/10 text-emerald" title="Free to use on OpenRouter">
      {compact ? (
        <>
          <FontAwesomeIcon icon={faGift} className="text-[9px]" aria-hidden />
          <span className="sr-only">Free</span>
        </>
      ) : (
        "Free"
      )}
    </span>
  );
}

/** OpenRouter reports a prompt-cache discount for this model. */
export function CacheBadge({ compact }: { compact?: boolean }) {
  return (
    <span className="chip !py-0 text-[10px] border-cyan/40 bg-cyan/10 text-cyan" title="Prompt caching: repeated context is billed at a discount">
      {compact ? (
        <>
          <FontAwesomeIcon icon={faBolt} className="text-[9px]" aria-hidden />
          <span className="sr-only">Cache</span>
        </>
      ) : (
        "Cache"
      )}
    </span>
  );
}

type PricedModel = {
  price_prompt_per_1m?: number | null;
  price_completion_per_1m?: number | null;
  is_free?: boolean;
  supports_prompt_cache?: boolean;
};

/**
 * All badges for one model — capability (vision/reasoning/tools), Free, Cache — grouped
 * together in a single row, always in the same order. Price is shown separately via `PriceText`,
 * after every badge, never interleaved with them.
 */
export function ModelBadges({
  caps,
  model,
  compact,
}: {
  caps: string[];
  model?: PricedModel;
  compact?: boolean;
}) {
  const shown = BADGE_ORDER.filter((c) => caps.includes(c));
  const isFree = !!model?.is_free;
  const supportsCache = !!model?.supports_prompt_cache;
  if (shown.length === 0 && !isFree && !supportsCache) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {shown.map((c) => (
        <CapabilityBadge key={c} cap={c} compact={compact} />
      ))}
      {isFree && <FreeBadge compact={compact} />}
      {supportsCache && <CacheBadge compact={compact} />}
    </span>
  );
}

/** "$0.14 / $0.28 per 1M" (prompt / completion), or "" when neither price is known. */
export function priceLabel(promptPer1m?: number | null, completionPer1m?: number | null): string {
  if (promptPer1m == null && completionPer1m == null) return "";
  const fmt = (n: number) => (n === 0 ? "free" : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);
  const p = promptPer1m != null ? fmt(promptPer1m) : "?";
  const c = completionPer1m != null ? fmt(completionPer1m) : "?";
  return `${p} / ${c} per 1M`;
}

/**
 * Price text only, shown after all badges (see `ModelBadges`). Renders nothing when the model is
 * free (the Free badge already says so) or when no price is known.
 */
export function PriceText({ model, compact }: { model: PricedModel; compact?: boolean }) {
  if (model.is_free) return null;
  const label = priceLabel(model.price_prompt_per_1m, model.price_completion_per_1m);
  if (!label) return null;
  return (
    <span className="whitespace-nowrap text-[10px] tabular-nums text-muted" title={`${label} tokens (input / output)`}>
      {compact ? label.replace(" per 1M", "") : label}
    </span>
  );
}
