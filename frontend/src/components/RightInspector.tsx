import { faFileCode, faGaugeHigh, faListCheck } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useState } from "react";

import type { FileChange, Task } from "../lib/types";
import type { useResizableWidth } from "../state/useResizableWidth";
import { ModelTelemetry } from "./ModelTelemetry";
import { ChangesPanel } from "./room/ChangesPanel";
import { TasksPanel } from "./room/TasksPanel";
import { Spinner } from "./Ui";

export type InspectorTab = "telemetry" | "tasks" | "changes";

const TAB_KEY = "catcrew.inspectorTab";
const TABS: InspectorTab[] = ["telemetry", "tasks", "changes"];

function readTab(): InspectorTab {
  try {
    const stored = window.localStorage.getItem(TAB_KEY);
    return TABS.includes(stored as InspectorTab) ? (stored as InspectorTab) : "telemetry";
  } catch {
    return "telemetry";
  }
}

interface Props {
  projectId: string;
  tasks: Task[];
  changes: FileChange[];
  /** The project's roster / tasks / changes have been fetched at least once. */
  loaded: boolean;
  resize: ReturnType<typeof useResizableWidth>;
}

/**
 * The project's right-hand inspector: live model telemetry, the task list and the file changes. It is
 * opened and closed from the room header; while closed, nothing in it is mounted (so nothing polls).
 */
export function RightInspector({ projectId, tasks, changes, loaded, resize }: Props) {
  const [tab, setTab] = useState<InspectorTab>(readTab);
  const pick = (next: InspectorTab) => {
    setTab(next);
    try {
      window.localStorage.setItem(TAB_KEY, next);
    } catch {
      /* private window or blocked storage: the tab is only a convenience */
    }
  };
  const openTasks = tasks.filter((t) => t.status === "pending" || t.status === "in_progress").length;
  const items: { id: InspectorTab; label: string; title: string; icon: typeof faGaugeHigh; badge?: number }[] = [
    { id: "telemetry", label: "Telemetry", title: "Telemetry & Usage", icon: faGaugeHigh },
    { id: "tasks", label: "Tasks", title: "Tasks", icon: faListCheck, badge: openTasks },
    { id: "changes", label: "Changes", title: "Changes", icon: faFileCode, badge: changes.length },
  ];

  return (
    <aside style={{ width: resize.width }} className="relative flex shrink-0 flex-col border-l border-line bg-[#0d1017]" aria-label="Inspector">
      {/* A 6px grab zone on the inspector's outer (left) edge; only a hairline shows, on hover, focus or drag. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize inspector"
        aria-valuenow={resize.width}
        aria-valuemin={300}
        aria-valuemax={resize.max}
        tabIndex={0}
        title="Drag to resize (double-click to reset)"
        {...resize.handleProps}
        className="group absolute inset-y-0 -left-[3px] z-20 w-[6px] cursor-col-resize outline-none"
      >
        <span
          aria-hidden
          className={clsx(
            "absolute inset-y-0 left-[2px] w-px bg-transparent transition-colors group-hover:bg-cyan/70 group-focus-visible:bg-cyan/70",
            resize.dragging && "bg-cyan",
          )}
        />
      </div>

      <div role="tablist" aria-label="Inspector" className="flex gap-1 border-b border-line px-2 py-2">
        {items.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            title={t.title}
            onClick={() => pick(t.id)}
            className={clsx(
              "flex flex-1 items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium transition",
              tab === t.id ? "bg-cyan/12 text-cyan shadow-[inset_0_0_0_1px_rgba(56,189,248,.3)]" : "text-muted hover:bg-white/5 hover:text-ink",
            )}
          >
            <FontAwesomeIcon icon={t.icon} />
            {t.label}
            {t.badge ? <span className="rounded-full bg-white/10 px-1.5 text-[10px] text-ink">{t.badge}</span> : null}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto" role="tabpanel">
        {tab === "telemetry" ? (
          <ModelTelemetry projectId={projectId} />
        ) : !loaded ? (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        ) : tab === "tasks" ? (
          <TasksPanel projectId={projectId} tasks={tasks} />
        ) : (
          <ChangesPanel changes={changes} projectId={projectId} />
        )}
      </div>
    </aside>
  );
}
