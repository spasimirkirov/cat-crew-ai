import { faFileCode } from "@fortawesome/free-solid-svg-icons";

import { humanAgo } from "../../lib/time";
import type { FileChange } from "../../lib/types";
import { useNow } from "../../lib/useNow";
import { useStore } from "../../state/store";
import { Avatar } from "../Avatar";
import { EmptyState } from "../Ui";
import { DiffBlock } from "./DiffBlock";
import { useDiff } from "./useDiff";

function ChangeRow({ change }: { change: FileChange }) {
  const agent = useStore((s) => s.agents[change.agent_id]);
  const now = useNow();
  const { diff, error } = useDiff(change.diff_uuid);
  return (
    <li className="card space-y-2 p-3" data-testid="change-row">
      <div className="flex items-center gap-2">
        {agent && <Avatar agent={agent} size={20} />}
        <span className="min-w-0 flex-1 truncate font-mono text-xs" title={change.file_path}>
          {change.file_path}
        </span>
        <span className="shrink-0 text-[11px] text-muted">{humanAgo(change.created_at, now)}</span>
      </div>
      {diff ? <DiffBlock text={diff.diff_content || "(empty diff)"} /> : <div className="text-[11px] text-muted">{error ?? "Loading diff…"}</div>}
    </li>
  );
}

export function ChangesPanel({ changes }: { changes: FileChange[] }) {
  if (changes.length === 0) {
    return <EmptyState icon={faFileCode} title="No file changes yet" hint="Files the cats write show up here the moment they happen." />;
  }
  return (
    <ul className="space-y-2 p-3">
      {changes.map((c) => (
        <ChangeRow key={c.diff_uuid} change={c} />
      ))}
    </ul>
  );
}
