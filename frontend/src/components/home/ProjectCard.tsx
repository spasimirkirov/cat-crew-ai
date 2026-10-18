import { faEllipsis, faFolder, faPen, faTrash } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";

import { humanAgo } from "../../lib/time";
import type { Agent, Project } from "../../lib/types";
import { runningElsewhere, turnFor } from "../../state/reducer";
import { useStore } from "../../state/store";
import { Avatar } from "../Avatar";
import { phaseLabel, phaseTone } from "./projectMeta";

const MAX_AVATARS = 6;

interface Props {
  project: Project;
  now: number;
  onOpen: (project: Project) => void;
  onRename: (project: Project) => void;
  onDelete: (project: Project) => void;
}

const TONE = {
  idle: "text-muted",
  active: "border-cyan/40 bg-cyan/10 text-cyan",
  done: "border-emerald/40 bg-emerald/10 text-emerald",
} as const;

export function ProjectCard({ project, now, onOpen, onRename, onDelete }: Props) {
  const agents = useStore((s) => s.agents);
  const activity = useStore((s) => s.activity);
  const busyHere = useStore((s) => Boolean(turnFor(s, project.id)));
  const busyElsewhere = useStore((s) => runningElsewhere(s, project.id));

  const crew = useMemo(() => project.bot_ids.map((id) => agents[id]).filter((a): a is Agent => Boolean(a)), [project.bot_ids, agents]);
  // A cat's status is global, so while only another project runs a turn its cats are not "ours".
  const working = busyElsewhere && !busyHere ? 0 : crew.filter((a) => activity[a.id]?.state === "working").length;

  const folder = project.folders[0] ?? "";
  const created = humanAgo(project.created_at, now);
  const tone = phaseTone(project.phase);

  return (
    <article className="card group relative flex flex-col gap-3 p-4 transition hover:border-[#343a52] hover:bg-[#151926] focus-within:border-cyan/50">
      <div className="flex items-start justify-between gap-2">
        <h3 className="min-w-0 text-[15px] font-semibold leading-snug">
          <button
            type="button"
            onClick={() => onOpen(project)}
            className="block max-w-full text-left after:absolute after:inset-0 after:rounded-xl after:content-[''] focus:outline-none focus-visible:after:ring-2 focus-visible:after:ring-cyan/60"
          >
            <span className="block truncate">{project.name}</span>
          </button>
        </h3>
        <ProjectMenu name={project.name} onRename={() => onRename(project)} onDelete={() => onDelete(project)} />
      </div>

      {project.description ? (
        <p className="line-clamp-2 min-h-[2.5rem] text-[13px] leading-5 text-muted">{project.description}</p>
      ) : (
        <p className="min-h-[2.5rem] text-[13px] leading-5 text-muted/50">No description</p>
      )}

      <div className={clsx("flex items-center gap-2 font-mono text-[11px]", folder ? "text-muted" : "text-muted/50")} title={folder || undefined}>
        <FontAwesomeIcon icon={faFolder} className="shrink-0" />
        <span className="truncate">{folder || "No workspace folder"}</span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className={clsx("chip", TONE[tone])} data-testid="phase-chip">
          {phaseLabel(project.phase)}
        </span>
        {working > 0 && (
          <span className="chip border-emerald/40 bg-emerald/10 text-emerald" data-testid="working-chip">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald animate-dot-pulse" />
            {working} {working === 1 ? "cat" : "cats"} working
          </span>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between gap-3 border-t border-line pt-3">
        {crew.length > 0 ? (
          <span role="img" aria-label={`Crew: ${crew.map((a) => a.name).join(", ")}`} className="flex items-center -space-x-2">
            {crew.slice(0, MAX_AVATARS).map((a) => (
              <span key={a.id} title={a.name} className="rounded-full ring-2 ring-card">
                <Avatar agent={a} size={26} />
              </span>
            ))}
            {crew.length > MAX_AVATARS && (
              <span className="z-0 flex h-[26px] min-w-[26px] items-center justify-center rounded-full border border-line bg-[#1b1f2c] px-1 text-[10px] font-semibold text-muted ring-2 ring-card">
                +{crew.length - MAX_AVATARS}
              </span>
            )}
          </span>
        ) : (
          <span className="text-xs text-muted/60">No crew yet</span>
        )}
        {created && <span className="shrink-0 text-xs text-muted">Created {created}</span>}
      </div>
    </article>
  );
}

function ProjectMenu({ name, onRename, onDelete }: { name: string; onRename: () => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };

  return (
    <div ref={ref} className="relative z-10 -mr-1 -mt-1 shrink-0">
      <button
        type="button"
        className="btn btn-ghost btn-sm h-7 w-7 !px-0"
        aria-label={`Actions for ${name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <FontAwesomeIcon icon={faEllipsis} />
      </button>
      {open && (
        <div role="menu" aria-label={`${name} actions`} className="card absolute right-0 top-8 z-20 w-40 overflow-hidden bg-[#171b26] py-1 text-sm shadow-xl">
          <button type="button" role="menuitem" onClick={pick(onRename)} className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left hover:bg-white/5 focus:bg-white/5 focus:outline-none">
            <FontAwesomeIcon icon={faPen} className="w-3.5 text-muted" /> Rename
          </button>
          <button type="button" role="menuitem" onClick={pick(onDelete)} className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-danger hover:bg-danger/10 focus:bg-danger/10 focus:outline-none">
            <FontAwesomeIcon icon={faTrash} className="w-3.5" /> Delete
          </button>
        </div>
      )}
    </div>
  );
}
