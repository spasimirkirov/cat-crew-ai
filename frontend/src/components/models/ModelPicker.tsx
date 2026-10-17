import { faCheck, faChevronDown, faMagnifyingGlass } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useEffect, useId, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";

import type { HostModel } from "../../lib/types";
import { ModelBadges, PriceText } from "./CapabilityBadge";

interface Props {
  /** Id of the trigger button, so a `<label htmlFor>` can point at it. */
  id: string;
  models: HostModel[];
  value: string;
  onChange: (name: string) => void;
  invalid?: boolean;
  /** Id of the element that explains why the field is invalid. */
  describedBy?: string;
  /** The capability the role cannot do without: only models that have it are listed, and its
   * checkbox is not shown (Vision lists vision models, Planning reasoning models, ...). */
  require?: Capability;
}

export type Capability = "vision" | "reasoning" | "tools";

/**
 * The vendors whose models are worth picking for agent work. OpenRouter lists several hundred
 * models (every fine-tune, preview and community upload); by default only these vendors' models
 * are shown, and "All vendors" brings the rest back. Hosts that are a vendor themselves (DeepSeek,
 * Anthropic ...) list ids without a "vendor/" prefix and are never filtered.
 */
export const TOP_VENDORS = [
  "anthropic",
  "openai",
  "google",
  "deepseek",
  "qwen",
  "x-ai",
  "mistralai",
  "moonshotai",
  "z-ai",
  "minimax",
] as const;

const vendorOf = (name: string): string => (name.includes("/") ? name.split("/")[0].toLowerCase() : "");
const isTopVendor = (name: string): boolean => {
  const vendor = vendorOf(name);
  return !vendor || (TOP_VENDORS as readonly string[]).includes(vendor);
};

interface Item {
  model: HostModel;
  unlisted: boolean;
}

interface Filters {
  vision: boolean;
  reasoning: boolean;
  tools: boolean;
  free: boolean;
  allVendors: boolean;
}

const NO_FILTERS: Filters = { vision: false, reasoning: false, tools: false, free: false, allVendors: false };

const CAP_FILTERS: { key: Capability; label: string; accent: string }[] = [
  { key: "vision", label: "Vision", accent: "accent-violet" },
  { key: "reasoning", label: "Reasoning", accent: "accent-amber-400" },
  { key: "tools", label: "Tools", accent: "accent-cyan" },
];

function matchesQuery(item: Item, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return item.model.name.toLowerCase().includes(q) || item.model.display_name.toLowerCase().includes(q);
}

function matchesFilters(item: Item, filters: Filters, require?: Capability): boolean {
  if (require && !item.model.capabilities.includes(require)) return false;
  if (!filters.allVendors && !isTopVendor(item.model.name)) return false;
  if (filters.vision && !item.model.capabilities.includes("vision")) return false;
  if (filters.reasoning && !item.model.capabilities.includes("reasoning")) return false;
  if (filters.tools && !item.model.capabilities.includes("tools")) return false;
  if (filters.free && !item.model.is_free) return false;
  return true;
}

/** Cheapest first: prompt price ascending, then completion price, then original order. Models
 * with no price data (any host but OpenRouter) sort after priced ones but keep their relative order. */
function byCheapest(models: HostModel[]): HostModel[] {
  return models
    .map((model, i) => ({ model, i }))
    .sort((a, b) => {
      const pa = a.model.price_prompt_per_1m ?? Infinity;
      const pb = b.model.price_prompt_per_1m ?? Infinity;
      if (pa !== pb) return pa - pb;
      const ca = a.model.price_completion_per_1m ?? Infinity;
      const cb = b.model.price_completion_per_1m ?? Infinity;
      if (ca !== cb) return ca - cb;
      return a.i - b.i;
    })
    .map((x) => x.model);
}

/**
 * A model dropdown that shows capability badges next to every model, which a native `<select>` cannot.
 * It follows the ARIA combobox/listbox pattern: the button opens a listbox; arrows move, Enter/Space
 * choose, Escape closes. The open panel also has a search field and Vision/Reasoning/Tools/Free
 * checkbox filters (minus the capability the role requires, which always applies), only the top
 * vendors' models unless "All vendors" is ticked, and the list is sorted cheapest-first. Badges are
 * icons (named in their tooltip), so the model name keeps the room.
 */
export function ModelPicker({ id, models, value, onChange, invalid, describedBy, require }: Props) {
  const list = useId();
  const root = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);

  // The full, cheapest-first list (unfiltered) — used for the closed-button display so the
  // currently selected model is always found regardless of search text or filters.
  const baseItems: Item[] = useMemo(() => byCheapest(models).map((model) => ({ model, unlisted: false })), [models]);

  // A saved model the host no longer lists stays visible (and selected) so it can be seen and
  // replaced. It's pinned to the top and exempt from search/filters.
  const unlistedItem: Item | null = useMemo(() => {
    if (value && !models.some((m) => m.name === value)) {
      return { model: { name: value, display_name: value, loaded: false, capabilities: [], context_length: 0 }, unlisted: true };
    }
    return null;
  }, [models, value]);

  const allItems: Item[] = useMemo(() => (unlistedItem ? [unlistedItem, ...baseItems] : baseItems), [unlistedItem, baseItems]);

  const visibleItems: Item[] = useMemo(() => {
    const filtered = baseItems.filter((i) => matchesFilters(i, filters, require) && matchesQuery(i, query));
    return unlistedItem ? [unlistedItem, ...filtered] : filtered;
  }, [unlistedItem, baseItems, filters, query, require]);
  // Only offer "All vendors" when the vendor filter actually hides something.
  const vendorFiltered = useMemo(() => baseItems.some((i) => !isTopVendor(i.model.name)), [baseItems]);

  const selected = allItems.find((i) => i.model.name === value);
  const optionId = (i: number) => `${list}-o${i}`;

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  // Keep the highlighted option in view while arrowing through a long list.
  useEffect(() => {
    if (open) document.getElementById(optionId(active))?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]); // eslint-disable-line react-hooks/exhaustive-deps

  // Focus the search field whenever the dropdown opens (a pure side effect on `open` — it never
  // touches `active`, so it can't race the explicit setActive() calls below).
  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  const show = () => {
    // Reset the search text on every open, but keep the checkbox filters as the user left them.
    // Compute the reset-query visible list right here so `active` lands on the current selection
    // without waiting for the `visibleItems` memo to catch up (avoids a stale-state race).
    const resetFiltered = baseItems.filter((i) => matchesFilters(i, filters, require));
    const resetVisible = unlistedItem ? [unlistedItem, ...resetFiltered] : resetFiltered;
    setQuery("");
    setActive(Math.max(0, resetVisible.findIndex((i) => i.model.name === value)));
    setOpen(true);
  };

  const choose = (i: number) => {
    const item = visibleItems[i];
    if (item && !item.unlisted) onChange(item.model.name);
    setOpen(false);
  };

  const onQueryChange = (e: ChangeEvent<HTMLInputElement>) => {
    setQuery(e.target.value);
    setActive(0);
  };

  const toggleFilter = (key: keyof Filters) => {
    setFilters((f) => ({ ...f, [key]: !f[key] }));
    setActive(0);
  };

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const last = visibleItems.length - 1;
    switch (e.key) {
      case "ArrowDown":
      case "ArrowUp": {
        e.preventDefault();
        if (!open) return show();
        setActive((a) => Math.min(last, Math.max(0, a + (e.key === "ArrowDown" ? 1 : -1))));
        break;
      }
      case "Home":
        if (open) {
          e.preventDefault();
          setActive(0);
        }
        break;
      case "End":
        if (open) {
          e.preventDefault();
          setActive(last);
        }
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (open) choose(active);
        else show();
        break;
      case "Escape":
        if (open) {
          e.preventDefault();
          e.stopPropagation();
          setOpen(false);
        }
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  };

  // Same navigation keys as the button, minus Space (which should type into the search field).
  const onSearchKey = (e: KeyboardEvent<HTMLInputElement>) => {
    const last = visibleItems.length - 1;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((a) => Math.min(last, Math.max(0, a + 1)));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((a) => Math.max(0, a - 1));
        break;
      case "Home":
        e.preventDefault();
        setActive(0);
        break;
      case "End":
        e.preventDefault();
        setActive(last);
        break;
      case "Enter":
        e.preventDefault();
        choose(active);
        break;
      case "Escape":
        e.preventDefault();
        e.stopPropagation();
        setOpen(false);
        buttonRef.current?.focus();
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  };

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        id={id}
        ref={buttonRef}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={list}
        aria-activedescendant={open ? optionId(active) : undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        className={clsx("input flex items-center justify-between gap-2 text-left", invalid && "!border-danger/70 ring-1 ring-danger/50")}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onKey}
      >
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
          {selected ? (
            <>
              <span className="truncate" title={selected.model.name}>
                {selected.model.name}
              </span>
              {selected.unlisted ? (
                <span className="text-xs text-danger">(not listed)</span>
              ) : (
                <>
                  <ModelBadges caps={selected.model.capabilities} model={selected.model} compact />
                  <PriceText model={selected.model} compact />
                </>
              )}
            </>
          ) : (
            <span className="text-muted/70">Select a model</span>
          )}
        </span>
        <FontAwesomeIcon icon={faChevronDown} className="shrink-0 text-xs text-muted" aria-hidden />
      </button>

      {open && (
        <div className="absolute left-0 right-0 z-30 mt-1 flex max-h-96 flex-col overflow-hidden rounded-lg border border-line bg-[#0f121a] shadow-card">
          <div className="space-y-2 border-b border-line p-2">
            <label className="flex items-center gap-2 rounded-md border border-line bg-black/20 px-2 py-1">
              <FontAwesomeIcon icon={faMagnifyingGlass} className="text-xs text-muted" aria-hidden />
              <input
                ref={searchRef}
                type="text"
                value={query}
                onChange={onQueryChange}
                onKeyDown={onSearchKey}
                placeholder="Search models…"
                aria-label="Search models"
                autoComplete="off"
                className="w-full bg-transparent text-sm text-fg outline-none placeholder:text-muted/70"
              />
            </label>
            <div className="flex flex-wrap items-center gap-3">
              {require && (
                <span className="text-[11px] text-muted/80" data-testid="required-cap">
                  {CAP_FILTERS.find((c) => c.key === require)?.label} models only
                </span>
              )}
              {CAP_FILTERS.filter((c) => c.key !== require).map((c) => (
                <label key={c.key} className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                  <input type="checkbox" checked={filters[c.key]} onChange={() => toggleFilter(c.key)} className={c.accent} />
                  {c.label}
                </label>
              ))}
              <label className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                <input type="checkbox" checked={filters.free} onChange={() => toggleFilter("free")} className="accent-emerald" />
                Free
              </label>
              {vendorFiltered && (
                <label className="inline-flex items-center gap-1.5 text-[11px] text-muted" title={`Shown by default: ${TOP_VENDORS.join(", ")}`}>
                  <input type="checkbox" checked={filters.allVendors} onChange={() => toggleFilter("allVendors")} className="accent-cyan" />
                  All vendors
                </label>
              )}
            </div>
          </div>

          <ul id={list} role="listbox" aria-label="Models" className="max-h-72 overflow-y-auto py-1">
            {visibleItems.length === 0 ? (
              <li role="presentation" className="px-3 py-4 text-center text-xs text-muted">
                No models match.
              </li>
            ) : (
              visibleItems.map((item, i) => {
                const isSel = item.model.name === value;
                return (
                  <li
                    key={item.model.name}
                    id={optionId(i)}
                    role="option"
                    aria-selected={isSel}
                    aria-disabled={item.unlisted || undefined}
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={(e) => e.preventDefault() /* keep focus on the search field */}
                    onClick={() => choose(i)}
                    className={clsx(
                      "flex cursor-pointer items-center gap-2 px-3 py-2 text-sm",
                      active === i && "bg-white/[0.06]",
                      item.unlisted && "cursor-default text-muted",
                    )}
                  >
                    <FontAwesomeIcon icon={faCheck} className={clsx("w-3 shrink-0 text-xs text-cyan", !isSel && "invisible")} aria-hidden />
                    <span className="min-w-0 flex-1 truncate" title={item.model.name}>
                      {item.model.name}
                    </span>
                    {item.unlisted ? (
                      <span className="text-xs text-danger">not listed</span>
                    ) : (
                      <>
                        <ModelBadges caps={item.model.capabilities} model={item.model} compact />
                        <PriceText model={item.model} compact />
                      </>
                    )}
                  </li>
                );
              })
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
