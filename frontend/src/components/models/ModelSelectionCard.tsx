import { faCircleInfo, faClone, faRotate, faRotateLeft, faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { ModelHost, RoleBrain } from "../../lib/types";
import { ErrorNote, Spinner } from "../Ui";
import { ModelPicker } from "./ModelPicker";
import { canTurnOff, COMFY_HOST, fitWarning, hostOptions, INHERIT, ROLE_OFF, OPENROUTER_IMAGE_HOST, type Probe, type RoleKey, type RoleMeta } from "./modelsUtil";

interface Props {
  meta: RoleMeta;
  hosts: ModelHost[];
  draft: RoleBrain;
  probe?: Probe;
  dirty: boolean;
  saving: boolean;
  error: string | null;
  /** Why the saved model cannot be used (from the start-up check); highlights the card until it is replaced. */
  invalid?: string | null;
  onChange: (role: RoleKey, brain: RoleBrain) => void;
  onRefresh: (host: string, image?: boolean) => void;
  /** Present on the Models page (per-card Save); absent in Project Model Overrides, which saves every
   * changed role together from its own page-level button -- everything else about the card is the same. */
  onSave?: (role: RoleKey) => void;
  /** The Chat role's current draft, for the "Same As Chat" button. Omitted on the Chat card itself. */
  chatDraft?: RoleBrain;
  /** Project Model Overrides only: turns the first Host option into "Use Global Setting (...)" instead
   * of the Models page's plain "Not set", and enables the reset-to-global button and the Overridden chip. */
  inheritLabel?: string;
  /** Project Model Overrides only: what this role actually runs on while inherited, e.g.
   * "DeepSeek: deepseek-v4-pro" -- shown as the model field's placeholder instead of a generic hint. */
  inheritModelLabel?: string;
  /** This role has a saved override that isn't the current (possibly still-unsaved) draft. */
  overridden?: boolean;
}

const TIP_WIDTH = 320;
const TIP_GAP = 8;

/**
 * The (i) next to a role's name: hover or focus it for what the role does and what kind of model
 * suits it. The tooltip is portalled to <body> with fixed coordinates, so no card, scroll area or
 * sidebar can clip it, and it is kept inside the window (above the icon when there is no room below).
 */
function RoleInfo({ uid, meta }: { uid: string; meta: RoleMeta }) {
  const tipId = `${uid}-info`;
  const anchor = useRef<HTMLButtonElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: -9999, top: -9999 });

  useLayoutEffect(() => {
    if (!open || !anchor.current) return;
    const place = () => {
      const r = anchor.current!.getBoundingClientRect();
      const width = Math.min(TIP_WIDTH, window.innerWidth - 2 * TIP_GAP);
      const height = tip.current?.offsetHeight ?? 0;
      const left = Math.min(Math.max(TIP_GAP, r.left + r.width / 2 - width / 2), window.innerWidth - width - TIP_GAP);
      const below = r.bottom + TIP_GAP;
      const top = below + height > window.innerHeight - TIP_GAP ? Math.max(TIP_GAP, r.top - TIP_GAP - height) : below;
      setPos({ left, top });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="rounded text-[12px] text-muted transition-colors hover:text-cyan focus:text-cyan focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan/60"
        aria-label={`About the ${meta.label} role`}
        aria-describedby={tipId}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
      >
        <FontAwesomeIcon icon={faCircleInfo} />
      </button>
      {createPortal(
        <div
          ref={tip}
          id={tipId}
          role="tooltip"
          data-open={open}
          style={{ left: pos.left, top: pos.top, width: Math.min(TIP_WIDTH, typeof window === "undefined" ? TIP_WIDTH : window.innerWidth - 2 * TIP_GAP) }}
          className={clsx(
            "pointer-events-none fixed z-[1000] rounded-lg border border-line bg-[#13161F] p-3 text-left text-xs font-normal leading-relaxed text-ink/90 shadow-xl transition-opacity duration-150",
            open ? "visible opacity-100" : "invisible opacity-0",
          )}
        >
          <span className="mb-1 block font-semibold text-cyan">What it does</span>
          <span className="block">{meta.info.does}</span>
          <span className="mb-1 mt-2.5 block font-semibold text-emerald">What kind of model</span>
          <span className="block">{meta.info.pick}</span>
        </div>,
        document.body,
      )}
    </>
  );
}

export function ModelSelectionCard({ meta, hosts, draft, probe, dirty, saving, error, invalid, onChange, onRefresh, onSave, chatDraft, inheritLabel, inheritModelLabel, overridden }: Props) {
  const uid = useId();
  const isImageOpenRouter = meta.key === "images" && draft.host === OPENROUTER_IMAGE_HOST;
  const options = hostOptions(meta.key, hosts, draft.host, inheritLabel);
  const known = hosts.find((h) => h.id === draft.host);
  const probeable = Boolean(known); // comfyui / unset hosts have no model listing endpoint
  const noKey = Boolean(known?.cloud && !known.configured);

  const loading = probeable && (!probe || probe.status === "loading");
  const offline = probeable && !loading && (probe?.status === "error" || probe?.data?.online === false);
  const models = probe?.data?.models ?? [];
  const useSelect = probeable && !loading && !offline && models.length > 0;
  const selectedModel = models.find((m) => m.name === draft.model);
  const fit = isImageOpenRouter ? "" : fitWarning(meta, selectedModel);
  const offlineReason = probe?.status === "error" ? probe.error : probe?.data?.error;

  const setHost = (host: string) => onChange(meta.key, { host, model: host === draft.host ? draft.model : "" });
  const setModel = (model: string) => onChange(meta.key, { ...draft, model });
  const canSameAsChat = Boolean(chatDraft && meta.key !== "chat" && (chatDraft.host || chatDraft.model) && (chatDraft.host !== draft.host || chatDraft.model !== draft.model));
  const canResetToGlobal = inheritLabel !== undefined && draft.host !== INHERIT;

  return (
    <section
      id={`role-${meta.key}`}
      data-testid={inheritLabel !== undefined ? `override-${meta.key}` : undefined}
      aria-labelledby={`${uid}-h`}
      data-invalid={invalid ? "true" : undefined}
      className={clsx("card flex flex-col gap-4 p-5", invalid && "!border-danger/60 shadow-[0_0_0_1px_rgba(248,113,113,.35)]")}
    >
      <header className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line bg-white/[0.03] text-cyan">
          <FontAwesomeIcon icon={meta.icon} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <h2 id={`${uid}-h`} className="text-sm font-semibold">
              {meta.label}
            </h2>
            <RoleInfo uid={uid} meta={meta} />
          </div>
          <p className="text-xs text-muted">{meta.blurb}</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {overridden && !dirty && <span className="chip border-cyan/40 text-cyan">Overridden</span>}
          {invalid && <span className="chip border-danger/40 bg-danger/10 text-danger">Needs a new model</span>}
          {dirty && <span className="chip border-warn/40 bg-warn/10 text-warn">Unsaved</span>}
          {chatDraft && meta.key !== "chat" && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={!canSameAsChat}
              onClick={() => chatDraft && onChange(meta.key, { ...chatDraft })}
              title={chatDraft.host || chatDraft.model ? `Copy the Chat role's host and model (${chatDraft.host || "not set"}${chatDraft.model ? `: ${chatDraft.model}` : ""})` : "Chat has no host or model set yet"}
            >
              <FontAwesomeIcon icon={faClone} /> Same As Chat
            </button>
          )}
        </div>
      </header>
      {invalid && (
        <p id={`${uid}-invalid`} role="alert" className="-mt-2 flex items-start gap-1.5 text-xs text-danger">
          <FontAwesomeIcon icon={faTriangleExclamation} className="mt-0.5 shrink-0" /> {invalid}
        </p>
      )}

      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="label !mb-0" htmlFor={`${uid}-host`}>
            Host
          </label>
          {canResetToGlobal && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange(meta.key, { host: INHERIT, model: "" })} title="Put this role back on the global setting">
              <FontAwesomeIcon icon={faRotateLeft} /> Use global
            </button>
          )}
        </div>
        <select
          id={`${uid}-host`}
          className="input"
          value={draft.host}
          aria-invalid={invalid ? true : undefined}
          onChange={(e) => setHost(e.target.value)}
          disabled={options.length === 1}
        >
          {options.map((o) => (
            <option key={o.id} value={o.id} disabled={o.disabled && o.id !== draft.host}>
              {o.label}
            </option>
          ))}
        </select>
        {noKey && (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-warn">
            <FontAwesomeIcon icon={faTriangleExclamation} /> No API key is saved for {known?.label}. Add one under Hosts to use it.
          </p>
        )}
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="label !mb-0" htmlFor={`${uid}-model`}>
            Model
          </label>
          {probeable && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => onRefresh(draft.host, isImageOpenRouter)} disabled={loading} aria-label={`Refresh models for ${meta.label}`}>
              <FontAwesomeIcon icon={faRotate} spin={loading} />
            </button>
          )}
        </div>

        {canTurnOff(meta.key) && draft.host === ROLE_OFF ? (
          <input
            id={`${uid}-model`}
            className="input"
            value=""
            disabled
            placeholder={meta.key === "images" ? "Image generation is off" : "Vision is off: screenshots are never sent to a model"}
            readOnly
          />
        ) : !draft.host ? (
          <input
            id={`${uid}-model`}
            className="input"
            value={draft.model}
            disabled
            placeholder={inheritModelLabel ?? (meta.follows ? `Follows the ${meta.follows} model` : "Pick a host first")}
            readOnly
          />
        ) : loading ? (
          <div className="flex items-center gap-2">
            <select id={`${uid}-model`} className="input" disabled value="" aria-busy="true">
              <option value="">Loading models...</option>
            </select>
            <Spinner />
          </div>
        ) : useSelect ? (
          <>
            <ModelPicker
              id={`${uid}-model`}
              models={models}
              value={draft.model}
              onChange={setModel}
              invalid={Boolean(invalid)}
              describedBy={invalid ? `${uid}-invalid` : undefined}
              require={isImageOpenRouter ? undefined : meta.requires}
            />
            {fit && (
              <p className="mt-1.5 flex items-start gap-1.5 text-xs text-warn">
                <FontAwesomeIcon icon={faTriangleExclamation} className="mt-0.5 shrink-0" /> {fit}
              </p>
            )}
          </>
        ) : (
          <>
            <input
              id={`${uid}-model`}
              className={clsx("input", invalid && "!border-danger/70 ring-1 ring-danger/50")}
              aria-invalid={invalid ? true : undefined}
              aria-describedby={invalid ? `${uid}-invalid` : undefined}
              value={draft.model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={meta.key === "images" && draft.host === COMFY_HOST ? "Checkpoint name" : "Type a model name"}
              spellCheck={false}
              autoComplete="off"
            />
            {offline && (
              <p className={clsx("mt-1.5 text-xs text-muted")}>
                Host offline{offlineReason ? <span className="text-muted/70"> ({offlineReason})</span> : null}. You can still type a model name.
              </p>
            )}
            {probeable && !offline && <p className="mt-1.5 text-xs text-muted">This host reported no models. You can type a name.</p>}
            {!probeable && meta.key === "images" && draft.host === COMFY_HOST && <p className="mt-1.5 text-xs text-muted">Image models come from your local ComfyUI. Type the model name.</p>}
          </>
        )}
      </div>

      <ErrorNote message={error} />

      {dirty && onSave && (
        <div className="flex justify-end">
          <button type="button" className="btn btn-primary btn-sm" onClick={() => onSave(meta.key)} disabled={saving}>
            {saving && <Spinner className="text-[#06202e]" />}
            Save
          </button>
        </div>
      )}
    </section>
  );
}
