import { faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useEffect } from "react";

import { ROOM_AVATAR, type Activity, type RosterEntry } from "../../lib/types";
import { useStore } from "../../state/store";
import { Avatar } from "../Avatar";
import { StatusPill } from "../StatusPill";

const POP_MS = 2600;

interface Props {
  entry: RosterEntry;
  projectId: string;
  selected: boolean;
  unread: number;
  onSelect: () => void;
  onRemove?: () => void;
}

const oneLine = (s: string) => s.replace(/\s+/g, " ").trim();

/** One cat in the project's fleet strip. Pops in (animation + halo) the moment the orchestrator staffs it. */
export function FleetCard({ entry, projectId, selected, unread, onSelect, onRemove }: Props) {
  const live: Activity = useStore((s) => s.activity[entry.bot_id]) ?? entry.activity;
  const freshAt = useStore((s) => s.fresh[`${projectId}:${entry.bot_id}`]);
  const clearFresh = useStore((s) => s.clearFresh);

  useEffect(() => {
    if (!freshAt) return;
    const t = setTimeout(() => clearFresh(`${projectId}:${entry.bot_id}`), POP_MS);
    return () => clearTimeout(t);
  }, [freshAt, clearFresh, projectId, entry.bot_id]);

  const working = live.state === "working";
  const caption = working && live.text ? oneLine(live.text) : entry.agent.title || entry.agent.role;

  return (
    <div
      data-testid="fleet-card"
      data-bot={entry.bot_id}
      data-status={live.status}
      data-fresh={Boolean(freshAt)}
      className={clsx(
        "group relative rounded-xl border transition-colors",
        selected ? "border-cyan/50 bg-cyan/[0.08]" : "border-line bg-card hover:border-[#343a52]",
        freshAt && "animate-pop-in",
      )}
    >
      <button type="button" onClick={onSelect} aria-current={selected} className="flex w-full items-center gap-3 px-3 py-2.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60 rounded-xl">
        <Avatar
          agent={entry.is_room ? { ...entry.agent, avatar: entry.agent.avatar || ROOM_AVATAR } : entry.agent}
          size={40}
          ring={working}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-sm font-semibold">{entry.agent.name}</span>
            <StatusPill status={live.status} />
          </div>
          <div className={clsx("mt-0.5 truncate text-xs", working ? "text-ink/80" : "text-muted")} title={caption}>
            {caption}
          </div>
        </div>
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-cyan px-1 text-[10px] font-bold text-[#06202e]" aria-label={`${unread} unread`}>
            {unread}
          </span>
        )}
      </button>
      {onRemove && (
        <button
          type="button"
          className="btn btn-ghost btn-sm absolute bottom-1 right-1 hidden h-6 w-6 !p-0 text-[10px] group-hover:inline-flex"
          aria-label={`Remove ${entry.agent.name} from this project`}
          title="Remove from this project"
          onClick={onRemove}
        >
          <FontAwesomeIcon icon={faXmark} />
        </button>
      )}
    </div>
  );
}
