import { faCheck, faGlobe, faLock } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import type { ReactNode } from "react";

import type { Agent } from "../../lib/types";
import { Avatar } from "../Avatar";
import { Toggle } from "../Ui";

export interface MatrixRow {
  id: string;
  name: string;
  description: string;
  is_global: boolean;
  agent_ids: string[];
  /** Cats that can never lose this row (an essential tool): shown on with a lock, not toggleable. */
  locked_agent_ids?: string[];
  /** Small chip(s) next to the name (skill type). */
  badge?: ReactNode;
  /** Monospace line under the name (tool handler path). */
  mono?: string;
}

interface Props {
  rows: MatrixRow[];
  agents: Agent[];
  /** Header of the sticky first column: "Skill" / "Tool". */
  columnLabel: string;
  selectedId?: string | null;
  pendingIds: ReadonlySet<string>;
  onOpen: (id: string) => void;
  onToggleAgent: (rowId: string, agentId: string) => void;
  onToggleGlobal: (rowId: string, on: boolean) => void;
}

const GLOBAL_HINT = "Global: attached to every cat";
const ESSENTIAL_HINT = "Essential for this cat: always on";
const STICKY_BG = "bg-bg group-hover:bg-[#141824]";

/**
 * Rows x agents grid shared by Skills and Tools. The scroll container is the caller's; the header row and
 * the first column stay pinned while the grid scrolls in both directions.
 */
export function AssignmentMatrix({ rows, agents, columnLabel, selectedId, pendingIds, onOpen, onToggleAgent, onToggleGlobal }: Props) {
  return (
    <table className="w-max min-w-full border-separate border-spacing-0 text-sm">
      <thead>
        <tr>
          <th
            scope="col"
            className="sticky left-0 top-0 z-30 w-[360px] min-w-[360px] border-b border-r border-line bg-[#0f121a] px-4 py-3 text-left text-[11px] font-medium uppercase tracking-wide text-muted"
          >
            {columnLabel}
          </th>
          <th scope="col" className="sticky top-0 z-20 w-[88px] min-w-[88px] border-b border-line bg-[#0f121a] px-2 py-3 text-center">
            <span className="flex flex-col items-center gap-1 text-[11px] font-medium text-emerald">
              <span className="flex h-7 w-7 items-center justify-center rounded-full border border-emerald/40 bg-emerald/10 text-xs">
                <FontAwesomeIcon icon={faGlobe} />
              </span>
              Global
            </span>
          </th>
          {agents.map((a) => (
            <th
              key={a.id}
              scope="col"
              title={a.title ? `${a.name}: ${a.title}` : a.name}
              className="sticky top-0 z-20 w-[92px] min-w-[92px] border-b border-line bg-[#0f121a] px-1.5 py-3 text-center"
            >
              <span className="flex flex-col items-center gap-1">
                <Avatar agent={a} size={28} />
                <span className="max-w-full truncate text-[11px] font-medium text-ink">{a.name}</span>
              </span>
            </th>
          ))}
          <th scope="col" className="sticky top-0 z-20 w-full border-b border-line bg-[#0f121a] p-0" aria-hidden />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const busy = pendingIds.has(row.id);
          return (
            <tr
              key={row.id}
              onClick={() => onOpen(row.id)}
              aria-busy={busy || undefined}
              className={clsx("group cursor-pointer transition-colors hover:bg-[#141824]", selectedId === row.id && "bg-cyan/[0.05]")}
            >
              <th
                scope="row"
                className={clsx(
                  "sticky left-0 z-10 w-[360px] min-w-[360px] max-w-[360px] border-b border-r border-line px-4 py-2.5 text-left font-normal",
                  selectedId === row.id ? "bg-[#101a26]" : STICKY_BG,
                )}
              >
                <button type="button" className="block w-full rounded-md text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60" aria-label={`Open ${row.name}`}>
                  <span className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-ink">{row.name}</span>
                    {row.badge}
                  </span>
                  {row.mono && <span className="mt-0.5 block truncate font-mono text-[11px] text-cyan/80" title={row.mono}>{row.mono}</span>}
                  <span className="mt-0.5 line-clamp-1 text-xs text-muted" title={row.description}>
                    {row.description || "No description"}
                  </span>
                </button>
              </th>
              <td className="border-b border-line px-2 py-2.5 text-center" onClick={(e) => e.stopPropagation()}>
                <Toggle on={row.is_global} disabled={busy} label={`Make ${row.name} global`} onChange={(on) => onToggleGlobal(row.id, on)} />
              </td>
              {agents.map((a) => {
                const essential = !row.is_global && (row.locked_agent_ids ?? []).includes(a.id);
                const locked = row.is_global || essential;
                const on = locked || row.agent_ids.includes(a.id);
                return (
                  <td key={a.id} className="border-b border-line px-1.5 py-2.5 text-center" onClick={(e) => e.stopPropagation()}>
                    <span title={essential ? ESSENTIAL_HINT : locked ? GLOBAL_HINT : undefined} className={clsx("inline-flex", locked && "cursor-not-allowed")}>
                      <button
                        type="button"
                        role="checkbox"
                        aria-checked={on}
                        aria-label={essential ? `${row.name} is essential for ${a.name} and always on` : `Assign ${row.name} to ${a.name}`}
                        disabled={locked || busy}
                        onClick={() => onToggleAgent(row.id, a.id)}
                        className={clsx(
                          "flex h-[22px] w-[22px] items-center justify-center rounded-md border text-[11px] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60",
                          locked
                            ? "pointer-events-none border-emerald/25 bg-emerald/10 text-emerald/60"
                            : on
                              ? "border-cyan/60 bg-cyan/20 text-cyan hover:bg-cyan/30"
                              : "border-line bg-white/[0.02] text-transparent hover:border-[#3a405a] hover:bg-white/[0.06]",
                          busy && !locked && "opacity-50",
                        )}
                      >
                        <FontAwesomeIcon icon={essential ? faLock : faCheck} />
                      </button>
                    </span>
                  </td>
                );
              })}
              <td className="border-b border-line p-0" />
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
