import { faChevronDown, faChevronUp, faListCheck, faPlus } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useState } from "react";

import { api } from "../../lib/api";
import { humanAgo } from "../../lib/time";
import { isRoomId, type Agent, type Task, type TaskStatus } from "../../lib/types";
import { useNow } from "../../lib/useNow";
import { errorText, useStore } from "../../state/store";
import { Avatar } from "../Avatar";
import { EmptyState, ErrorNote } from "../Ui";

const STATUS: Record<TaskStatus, { label: string; cls: string }> = {
  pending: { label: "Pending", cls: "border-line text-muted" },
  in_progress: { label: "In progress", cls: "border-cyan/40 text-cyan" },
  blocked: { label: "Blocked", cls: "border-warn/40 text-warn" },
  failed: { label: "Failed", cls: "border-danger/40 text-danger" },
  unverified: { label: "Unverified", cls: "border-warn/40 text-warn" },
  done: { label: "Done", cls: "border-emerald/40 text-emerald" },
};

export function TaskStatusChip({ status }: { status: TaskStatus }) {
  const s = STATUS[status] ?? STATUS.pending;
  return (
    <span data-status={status} className={clsx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium", s.cls)}>
      {status === "in_progress" && <span className="h-1.5 w-1.5 animate-dot-pulse rounded-full bg-cyan" />}
      {s.label}
    </span>
  );
}

const KIND_LABEL: Record<string, string> = { bug: "Bug", plan: "Fix plan", fix: "Fix", review: "QA check" };

function TaskRow({ task, agent }: { task: Task; agent?: Agent }) {
  const [open, setOpen] = useState(false);
  const now = useNow();
  const body = task.latest_summary || task.description;
  return (
    <li className="card p-3" data-testid="task-row" data-status={task.status}>
      <div className="flex items-start gap-2">
        {agent ? <Avatar agent={agent} size={24} /> : <span className="h-6 w-6 shrink-0 rounded-full bg-white/5" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-sm font-medium" title={task.title}>
              {task.title}
            </span>
            <TaskStatusChip status={task.status} />
          </div>
          <div className="mt-0.5 text-[11px] text-muted">
            {task.kind && KIND_LABEL[task.kind] && (
              <span data-testid="task-kind" className="mr-1.5 rounded border border-line px-1 py-px text-[10px] uppercase tracking-wide">
                {KIND_LABEL[task.kind]}
                {task.attempt ? ` ${task.attempt + 1}` : ""}
              </span>
            )}
            {agent?.name ?? task.assigned_to} · updated {humanAgo(task.updated_at, now)}
          </div>
          {task.acceptance && task.kind !== "review" && (
            <div className="mt-1 text-[11px] text-muted" data-testid="task-acceptance">
              Accepted when: {task.acceptance}
            </div>
          )}
        </div>
      </div>
      {body && (
        <div className="mt-2">
          <p className={clsx("break-words text-xs leading-snug text-muted", open ? "whitespace-pre-wrap" : "line-clamp-2")} data-testid="task-summary">
            {body}
          </p>
          {body.length > 90 && (
            <button type="button" className="btn btn-ghost btn-sm mt-1 -ml-1 text-[11px]" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              {open ? "Show less" : "See more"}
              <FontAwesomeIcon icon={open ? faChevronUp : faChevronDown} className="text-[10px]" />
            </button>
          )}
        </div>
      )}
    </li>
  );
}

function NewTask({ projectId, agents }: { projectId: string; agents: Agent[] }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState(agents[0]?.id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!open) {
    return (
      <button type="button" className="btn btn-sm w-full" onClick={() => setOpen(true)}>
        <FontAwesomeIcon icon={faPlus} /> New task
      </button>
    );
  }
  const submit = async () => {
    if (!title.trim() || !assignee) return;
    setBusy(true);
    setError(null);
    try {
      await api.tasks.create(projectId, { assigned_to: assignee, title: title.trim() });
      setTitle("");
      setOpen(false);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="card space-y-2 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label className="label" htmlFor="new-task-title">
        Task
      </label>
      <input id="new-task-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What should be done?" autoFocus />
      <label className="label" htmlFor="new-task-agent">
        Assign to
      </label>
      <select id="new-task-agent" className="input" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
        {agents.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
      </select>
      <ErrorNote message={error} />
      <div className="flex justify-end gap-2">
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button type="submit" className="btn btn-sm btn-primary" disabled={busy || !title.trim()}>
          Add task
        </button>
      </div>
    </form>
  );
}

/** Tasks of ONE project. Tasks are never shown anywhere else in the app. */
export function TasksPanel({ projectId, tasks }: { projectId: string; tasks: Task[] }) {
  const agents = useStore((s) => s.agents);
  const list = Object.values(agents).filter((a) => !isRoomId(a.id));
  // newest first: what just changed is what you want to see
  const ordered = [...tasks].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return (
    <div className="space-y-2 p-3">
      <NewTask projectId={projectId} agents={list} />
      {ordered.length === 0 ? (
        <EmptyState icon={faListCheck} title="No tasks yet" hint="Tasks appear here as Cat Crew hands work to the cats." />
      ) : (
        <ul className="space-y-2">
          {ordered.map((t) => (
            <TaskRow key={t.id} task={t} agent={agents[t.assigned_to]} />
          ))}
        </ul>
      )}
    </div>
  );
}
