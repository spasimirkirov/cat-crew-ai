import { faClipboardCheck, faCode, faComments, faDatabase, faEye, faImage, faListCheck } from "@fortawesome/free-solid-svg-icons";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import type { HostModel, HostModels, ModelHost, ModelsState, RoleBrain } from "../../lib/types";
import { errorText } from "../../state/store";

export type RoleKey = keyof ModelsState["roles"];

export interface RoleMeta {
  key: RoleKey;
  label: string;
  icon: IconDefinition;
  blurb: string;
  /** Left unset, the role runs on this other role's model ("Chat", or "Coding" for Review). */
  follows?: string;
  /** The capability this role works best with (a hint, never a block). */
  needs?: { cap: string; message: string };
  /** The model list for this role shows only models with this capability (Vision: vision models). */
  requires?: "vision" | "reasoning" | "tools";
  /** The (i) tooltip: what the role does, and what kind of model suits it and why (no model names). */
  info: { does: string; pick: string };
}

export const ROLES: RoleMeta[] = [
  {
    key: "chat",
    label: "Chat",
    icon: faComments,
    blurb: "Everyday conversation and dispatching the crew.",
    requires: "tools",
    info: {
      does:
        "Cat Crew itself: talks with you, answers questions, asks what is unclear and decides which cats get the work. Every role you leave unset runs on this model too.",
      pick:
        "A capable general model with reliable tool calling. It does not have to be your biggest one: you wait for every reply here, so speed matters as much as depth.",
    },
  },
  {
    key: "coding",
    label: "Coding",
    icon: faCode,
    blurb: "Writing and editing code.",
    follows: "Chat",
    needs: { cap: "tools", message: "Coding works best with a model that supports tool calling." },
    requires: "tools",
    info: {
      does:
        "Every working phase: DevOps setup, the build, design and fixes. Each one is a long tool loop -- often 20 to 40 steps of reading files, writing code and running commands -- and the cats' 1:1 chats.",
      pick:
        "Your strongest model, with excellent tool calling and a long context window. Do not save money here: a small model loses the thread, answers with nothing or reads the same files in circles. Large coding-tuned models do best.",
    },
  },
  {
    key: "memory",
    label: "Memory",
    icon: faDatabase,
    blurb: "Summaries and long-term recall. Also shrinks long chats.",
    follows: "Chat",
    info: {
      does:
        "Summarises a long chat (about 8k tokens and up) before it reaches the next model, and writes long-term notes. Short, single calls with no tools.",
      pick:
        "A small, fast, inexpensive model. A summary needs no deep reasoning, so a big model here only adds cost and waiting time.",
    },
  },
  {
    key: "vision",
    label: "Vision",
    icon: faEye,
    blurb: "Describes screenshots during QA and design checks.",
    follows: "Chat",
    needs: { cap: "vision", message: "Vision needs a model that accepts images." },
    requires: "vision",
    info: {
      does:
        "Looks at a screenshot and describes it in words when QA or the designer needs to see a page (charts, images, visual glitches). Called rarely; text-based page checks do most of the work.",
      pick:
        "Any model that accepts images: a fast, inexpensive multimodal model is enough. Turn it Off when none of your models can see; the cats then inspect pages as text.",
    },
  },
  {
    key: "planning",
    label: "Planning",
    icon: faListCheck,
    blurb: "Step-by-step breakdown of a new project.",
    follows: "Chat",
    needs: { cap: "reasoning", message: "Planning works best with a reasoning model." },
    requires: "reasoning",
    info: {
      does:
        "The architecture and the work split of a project: only a few calls per run, but a mistake here is the most expensive one, because every later phase builds on it.",
      pick:
        "A reasoning (thinking) model. Slow and pricey is fine: it runs only a few times per project, and a better plan saves many build rounds.",
    },
  },
  {
    key: "review",
    label: "Review",
    icon: faClipboardCheck,
    blurb: "QA: checks the finished work against the brief.",
    follows: "Coding",
    needs: { cap: "tools", message: "Review runs tests and opens pages: it needs a model that supports tool calling." },
    requires: "tools",
    info: {
      does:
        "The model Aegis (QA) checks the work with: runs the tests, opens every page, compares the result with the brief and answers PASS or FAIL. A FAIL sends the work back for a fix. Unset, it uses the Coding model.",
      pick:
        "A strong model with good tool calling, ideally from a different vendor than Coding. A model tends to overlook its own mistakes; a second opinion from another model family catches far more of them.",
    },
  },
  {
    key: "images",
    label: "Images",
    icon: faImage,
    blurb: "Image generation.",
    info: {
      does:
        "Creates new images (generate_image) through your local ComfyUI or OpenRouter's image models. Finding existing photos on the web is a separate tool and needs no model here.",
      pick:
        "An image-generation model, not a chat model: choose it by style and quality. Leave it Off when your sites use real photos or placeholders; it saves resources.",
    },
  },
];

/** A one-line hint when the picked model lacks what the role works best with, else "". */
export function fitWarning(meta: RoleMeta, model: HostModel | undefined): string {
  if (!meta.needs || !model) return "";
  return model.capabilities.includes(meta.needs.cap) ? "" : meta.needs.message;
}

/** ComfyUI serves the Images role locally. The backend accepts it but does not list it in `hosts`. */
export const COMFY_HOST = "comfyui";
/** The other Images host: OpenRouter's image-generation models, over the same OpenRouter key
 * already configured for chat. Not a separate entry in the generic `hosts` list -- Images treats
 * it as a second fixed choice next to ComfyUI, same as ComfyUI itself is fixed and never in `hosts`. */
export const OPENROUTER_IMAGE_HOST = "openrouter";
/** A role switched off: Images (the image tools say so instead of starting ComfyUI) and Vision (for
 * a chat model that cannot see: browser_look says so instead of sending it a screenshot). */
export const ROLE_OFF = "off";
export const IMAGES_OFF = ROLE_OFF;
/** Roles that offer "Off" in their Host list. */
export const canTurnOff = (role: RoleKey) => role === "images" || role === "vision";

/** No override / no pick: "use whatever the Models page says" in Project Model Overrides, or
 * "unset, follows Chat" on the Models page itself. Both screens use the same empty-string value. */
export const INHERIT = "";

export interface HostOption {
  id: string;
  label: string;
  disabled: boolean;
  note?: string;
}

/**
 * The Host <select> options for one role. `inheritLabel`, when given, is Project Model Overrides
 * asking for a leading "Use Global Setting (...)" option instead of the Models page's plain
 * "Not set" -- same list shape either way, just a different first label.
 */
export function hostOptions(role: RoleKey, hosts: ModelHost[], current: string, inheritLabel?: string): HostOption[] {
  if (role === "images") {
    const opts: HostOption[] = [];
    if (inheritLabel !== undefined) opts.push({ id: INHERIT, label: inheritLabel, disabled: false });
    opts.push({ id: IMAGES_OFF, label: "Off", disabled: false });
    opts.push({ id: COMFY_HOST, label: "ComfyUI (local)", disabled: false });
    const or = hosts.find((h) => h.id === OPENROUTER_IMAGE_HOST);
    if (or) {
      const noKey = Boolean(or.cloud && !or.configured);
      opts.push({ id: OPENROUTER_IMAGE_HOST, label: noKey ? "OpenRouter (no API key)" : "OpenRouter", disabled: noKey && current !== OPENROUTER_IMAGE_HOST });
    }
    return opts;
  }
  const follows = ROLES.find((r) => r.key === role)?.follows;
  const opts: HostOption[] = [{ id: INHERIT, label: inheritLabel ?? (follows ? `Not set (same as ${follows})` : "Not set"), disabled: false }];
  if (canTurnOff(role)) opts.push({ id: ROLE_OFF, label: "Off", disabled: false });
  for (const h of hosts) {
    const noKey = h.cloud && !h.configured;
    opts.push({ id: h.id, label: noKey ? `${h.label} (no API key)` : h.label, disabled: noKey, note: noKey ? "no API key saved" : undefined });
  }
  if (current && !opts.some((o) => o.id === current)) opts.push({ id: current, label: current, disabled: false });
  return opts;
}

/** The backend accepts ComfyUI, OpenRouter or Off for Images; anything else (an old settings file, a
 * dropped host) falls back to ComfyUI. Only used on the Models page -- Project Model Overrides
 * keeps its own "" (inherit) untouched, which this would otherwise stomp on. */
export const normalizeBrain = (role: RoleKey, b: RoleBrain): RoleBrain =>
  role === "images" && b.host !== OPENROUTER_IMAGE_HOST && b.host !== IMAGES_OFF ? { host: COMFY_HOST, model: b.model } : b;

export const sameBrain = (a: RoleBrain, b: RoleBrain) => a.host === b.host && a.model.trim() === b.model.trim();

// -- live probes ----------------------------------------------------------------------

export interface Probe {
  status: "loading" | "done" | "error";
  data?: HostModels;
  error?: string;
}

/** The `probes` record key for a host, or its Images-specific image-model variant (OpenRouter has
 * two completely different model lists -- chat and image -- behind the same host id). */
export const probeKey = (host: string, image?: boolean): string => (image ? `${host}:image` : host);

/**
 * Probes a host's live model list on demand: `GET /models/hosts/{host}` (short cache) the first time,
 * `POST /hosts/{host}/refresh-models` (asks the provider now) when forced. A newer probe of the same
 * host (or host+variant) wins. `opts.image` asks for OpenRouter's image-generation models instead of
 * its chat models -- only meaningful for the Images role.
 */
export function useHostProbes() {
  const [probes, setProbes] = useState<Record<string, Probe>>({});
  const gen = useRef<Record<string, number>>({});
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const probe = useCallback((host: string, force = false, opts?: { image?: boolean }) => {
    if (!host) return;
    const key = probeKey(host, opts?.image);
    if (!force && gen.current[key]) return; // already probed or probing
    const mine = (gen.current[key] ?? 0) + 1;
    gen.current[key] = mine;
    setProbes((p) => ({ ...p, [key]: { status: "loading", data: p[key]?.data } }));
    // Call with a bare `host` when there's no variant, matching the pre-image-support call shape exactly.
    const call = force ? api.models.refreshHost : api.models.hostModels;
    (opts?.image ? call(host, opts) : call(host))
      .then((data) => {
        if (alive.current && gen.current[key] === mine) setProbes((p) => ({ ...p, [key]: { status: "done", data } }));
      })
      .catch((err) => {
        if (alive.current && gen.current[key] === mine) setProbes((p) => ({ ...p, [key]: { status: "error", error: errorText(err) } }));
      });
  }, []);

  return { probes, probe };
}
