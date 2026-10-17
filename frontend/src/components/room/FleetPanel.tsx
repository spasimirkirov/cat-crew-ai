import { useState } from "react";

import { api } from "../../lib/api";
import type { RosterEntry } from "../../lib/types";
import { ROOM_ID, ROOM_NAME } from "../../lib/types";
import { errorText, useStore } from "../../state/store";
import { ErrorNote } from "../Ui";
import { FleetCard } from "./FleetCard";

interface Props {
  projectId: string;
  roster: RosterEntry[];
  selectedBot: string;
  onSelect: (botId: string) => void;
}

/**
 * The project's fleet. There is no "add a cat" here on purpose: cats join when the orchestrator calls
 * them for a task (`bot.staffed`) and leave when their work is done or they are removed (`bot.released`).
 */
export function FleetPanel({ projectId, roster, selectedBot, onSelect }: Props) {
  const unread = useStore((s) => s.rooms[projectId]?.threads);
  const [error, setError] = useState<string | null>(null);

  const remove = async (id: string) => {
    setError(null);
    try {
      await api.roster.remove(projectId, id);
      if (selectedBot === id) onSelect(ROOM_ID);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div className="space-y-2 p-3">
      {roster.length <= 1 && (
        <p className="px-1 pb-1 text-xs text-muted">Only {ROOM_NAME} so far. Cats join here the moment {ROOM_NAME} calls them.</p>
      )}
      <div className="space-y-2" data-testid="fleet">
        {roster.map((entry) => (
          <FleetCard
            key={entry.bot_id}
            entry={entry}
            projectId={projectId}
            selected={entry.bot_id === selectedBot}
            unread={unread?.[entry.bot_id]?.unread ?? 0}
            onSelect={() => onSelect(entry.bot_id)}
            onRemove={entry.is_room ? undefined : () => void remove(entry.bot_id)}
          />
        ))}
      </div>
      <ErrorNote message={error} />
    </div>
  );
}
