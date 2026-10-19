import { faCheck, faGlobe } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import type { ReactNode } from "react";

import type { Agent } from "../../lib/types";
import { Avatar } from "../Avatar";

export interface MatrixRow {
  id: string;
  name: string;
  description: string;
  is_global: boolean;
  agent_ids: string[];
  /** Cats whose built-in bundle holds this row. */
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
  onOpen: (id: string) => void;
}

const STICKY_BG = "bg-bg group-hover:bg-[#141824]";

/**
 * Rows x cats grid shared by Skills and Tools: who has what. Read-only: each cat's bundle is built in, and a
 * skill or tool the user adds is placed by the backend. The scroll container is the caller's; the header row
 * and the first column stay pinned while the grid scrolls in both directions.
 */
export function AssignmentMatrix({ rows, agents, columnLabel, selectedId, onOpen }: Props) {
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
        {rows.map((row) => (
          <tr
            key={row.id}
            onClick={() => onOpen(row.id)}
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
            <td className="border-b border-line px-2 py-2.5 text-center">
              {row.is_global && (
                <span role="img" aria-label={`${row.name} is global`} title="Global: every cat has it" className="text-emerald">
                  <FontAwesomeIcon icon={faGlobe} />
                </span>
              )}
            </td>
            {agents.map((a) => {
              const bundled = (row.locked_agent_ids ?? []).includes(a.id);
              const has = row.is_global || bundled || row.agent_ids.includes(a.id);
              const hint = bundled ? "Built into this cat's bundle" : row.is_global ? "Global: every cat has it" : "Added for this cat";
              return (
                <td key={a.id} className="border-b border-line px-1.5 py-2.5 text-center">
                  {has && (
                    <span
                      role="img"
                      aria-label={`${a.name} has ${row.name}`}
                      title={hint}
                      className={clsx(
                        "inline-flex h-[22px] w-[22px] items-center justify-center rounded-md border text-[11px]",
                        bundled || !row.is_global ? "border-cyan/50 bg-cyan/15 text-cyan" : "border-emerald/25 bg-emerald/10 text-emerald/60",
                      )}
                    >
                      <FontAwesomeIcon icon={faCheck} />
                    </span>
                  )}
                </td>
              );
            })}
            <td className="border-b border-line p-0" />
          </tr>
        ))}
      </tbody>
    </table>
  );
}
