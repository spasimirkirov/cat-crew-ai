import { faArrowLeft, faFolderOpen, faGaugeHigh, faGear, faRotateLeft, faTerminal } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { RightInspector } from "../components/RightInspector";
import { FleetPanel } from "../components/room/FleetPanel";
import { ThreadView } from "../components/room/ThreadView";
import { StatusPill } from "../components/StatusPill";
import { Avatar } from "../components/Avatar";
import { TerminalPanel } from "../components/TerminalPanel";
import { EmptyState, ErrorNote, Spinner } from "../components/Ui";
import { api } from "../lib/api";
import { levelFor } from "../lib/telemetry";
import { ROOM_AVATAR, ROOM_ID, ROOM_NAME, isRoomId } from "../lib/types";
import { faFolderClosed } from "@fortawesome/free-solid-svg-icons";
import { emptyRoom } from "../state/reducer";
import { useResizableWidth } from "../state/useResizableWidth";
import { errorText, useStore } from "../state/store";

const INSPECTOR_KEY = "catcrew.inspectorOpen";

/** Whether the right inspector is open: the viewer's last choice, else open on a window wide enough to spare it. */
function readInspectorOpen(): boolean {
  try {
    const stored = window.localStorage.getItem(INSPECTOR_KEY);
    if (stored === "1") return true;
    if (stored === "0") return false;
  } catch {
    /* blocked storage: fall through to the default */
  }
  return window.innerWidth >= 1280;
}

const PHASE_NAMES: Record<string, string> = {
  blueprint: "Blueprint",
  architecture: "Architecture",
  distribution: "Distribution",
  devops: "DevOps",
  build: "Build",
  design: "Design",
  qa: "QA",
  "qa-fix": "QA fix",
  done: "Finished",
};

export function ProjectRoomPage() {
  const { pid = "", botId: rawBot = ROOM_ID } = useParams();
  const botId = isRoomId(rawBot) ? ROOM_ID : rawBot; // old links used the legacy "chief" id
  const navigate = useNavigate();
  const [inspectorOpen, setInspectorOpen] = useState(readInspectorOpen);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  // The terminal is created the first time it is opened and then only hidden, so a dev server keeps
  // running while it is collapsed. Another project (or leaving this page) closes it for good; the
  // state is per project, so a new project always starts with the terminal closed.
  const [term, setTerm] = useState<{ pid: string; open: boolean } | null>(null);
  const terminalUsed = term?.pid === pid;
  const terminalOpen = terminalUsed && Boolean(term?.open);
  // The fleet sidebar: 320 px by default, draggable, and never so wide that the chat next to it drops under 480 px.
  const sidebar = useResizableWidth({ storageKey: "catcrew.projectSidebarWidth", initial: 320, min: 240, max: 560, reserve: 480 });
  // The right inspector (telemetry / tasks / changes) yields to the sidebar: it may use what the sidebar and the chat leave.
  const inspector = useResizableWidth({ storageKey: "catcrew.inspectorWidth", initial: 340, min: 300, max: 560, reserve: 480 + sidebar.width, edge: "right" });
  const toggleInspector = () =>
    setInspectorOpen((open) => {
      try {
        window.localStorage.setItem(INSPECTOR_KEY, open ? "0" : "1");
      } catch {
        /* the choice just is not remembered */
      }
      return !open;
    });
  const toggleTerminal = () => setTerm((t) => ({ pid, open: t?.pid === pid ? !t.open : true }));

  const ready = useStore((s) => s.ready);
  const project = useStore((s) => s.projects.find((p) => p.id === pid));
  const room = useStore((s) => s.rooms[pid]) ?? emptyRoom();
  const activity = useStore((s) => s.activity);
  const agents = useStore((s) => s.agents);
  const turn = useStore((s) => s.currentTurn);
  const loadProject = useStore((s) => s.loadProject);
  const loadThread = useStore((s) => s.loadThread);
  const setActiveThread = useStore((s) => s.setActiveThread);
  const connection = useStore((s) => s.connection);

  // Ctrl/Cmd+` shows or hides the terminal, wherever the focus is.
  useEffect(() => {
    if (!pid) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.code === "Backquote" && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey) {
        e.preventDefault();
        setTerm((t) => ({ pid, open: t?.pid === pid ? !t.open : true }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pid]);

  // Open the project: fetch its roster/tasks/changes once, then live events keep it current.
  useEffect(() => {
    if (!pid) return;
    void loadProject(pid);
  }, [pid, loadProject]);

  // Open a thread: mark it read and fetch its transcript.
  useEffect(() => {
    if (!pid) return;
    setActiveThread({ pid, botId });
    void loadThread(pid, botId);
    return () => setActiveThread(null);
  }, [pid, botId, setActiveThread, loadThread]);

  // A reconnect (`hello`) resyncs everything through the store; nothing to do here.

  if (!ready && !project) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (!project) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState
          icon={faFolderClosed}
          title="Project not found"
          hint="It may have been deleted."
          action={
            <Link className="btn btn-primary btn-sm mt-2" to="/">
              Back to Home
            </Link>
          }
        />
      </div>
    );
  }

  const entry = room.roster.find((r) => r.bot_id === botId);
  const agent = botId === ROOM_ID ? entry?.agent : agents[botId] ?? entry?.agent;
  const live = activity[botId] ?? entry?.activity;
  const runningHere = turn && turn.project_id === pid;
  const phase = room.phase?.label || (project.phase ? PHASE_NAMES[project.phase] ?? project.phase : "");
  const contextAlert = room.context ? levelFor(room.context.percent) === "danger" : false;

  const reset = async () => {
    if (!window.confirm("Reset this project? The Cat Crew chat and shared state are cleared. Files and the cats' own chats are kept.")) return;
    try {
      await api.projects.reset(pid);
      await loadThread(pid, ROOM_ID);
      setNotice({ text: "Cat Crew cleared.", error: false });
    } catch (e) {
      setNotice({ text: errorText(e), error: true });
    }
  };

  return (
    <div className={clsx("flex h-full min-w-0", (sidebar.dragging || inspector.dragging) && "cursor-col-resize select-none")}>
      {/* left: the project and its fleet (cats join and leave as the orchestrator calls them) */}
      <aside style={{ width: sidebar.width }} className="relative flex shrink-0 flex-col border-r border-line bg-[#0d1017]" aria-label="Project sidebar">
        <div className="border-b border-line px-4 py-3">
          <Link to="/" className="inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink">
            <FontAwesomeIcon icon={faArrowLeft} /> Home
          </Link>
          <h1 className="mt-1 truncate text-base font-semibold" title={project.name}>
            {project.name}
          </h1>
          {project.folders[0] && (
            <div className="mt-0.5 flex items-center gap-1.5 truncate font-mono text-[11px] text-muted" title={project.folders[0]}>
              <FontAwesomeIcon icon={faFolderOpen} />
              <span className="truncate">{project.folders[0]}</span>
            </div>
          )}
        </div>
        <div className="flex items-center justify-between border-b border-line px-4 py-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">Fleet</h2>
          {room.roster.length > 1 && (
            <span className="rounded-full bg-white/10 px-1.5 text-[10px] text-ink" title="Cats working in this project">
              {room.roster.length - 1}
            </span>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {!room.loaded ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : (
            <FleetPanel projectId={pid} roster={room.roster} selectedBot={botId} onSelect={(id) => navigate(`/project/${pid}/${id}`)} />
          )}
        </div>
        {/* A 6px grab zone on the sidebar's outer edge; only a hairline shows, on hover, focus or drag. */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize sidebar"
          aria-valuenow={sidebar.width}
          aria-valuemin={240}
          aria-valuemax={sidebar.max}
          tabIndex={0}
          title="Drag to resize (double-click to reset)"
          {...sidebar.handleProps}
          className="group absolute inset-y-0 -right-[3px] z-20 w-[6px] cursor-col-resize outline-none"
        >
          <span
            aria-hidden
            className={clsx(
              "absolute inset-y-0 right-[2px] w-px bg-transparent transition-colors group-hover:bg-cyan/70 group-focus-visible:bg-cyan/70",
              sidebar.dragging && "bg-cyan",
            )}
          />
        </div>
      </aside>

      {/* main: the selected thread */}
      <section className="flex min-w-0 flex-1 flex-col" aria-label="Conversation">
        <header className="flex items-center justify-between gap-3 border-b border-line px-6 py-3">
          <div className="flex min-w-0 items-center gap-3">
            {agent && <Avatar agent={botId === ROOM_ID ? { ...agent, avatar: agent.avatar || ROOM_AVATAR } : agent} size={32} />}
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">{botId === ROOM_ID ? ROOM_NAME : agent?.name ?? botId}</div>
              <div className="truncate text-xs text-muted">
                {botId === ROOM_ID ? "Orchestrator — chat freely, or describe work and it calls the right cats" : agent?.title || agent?.role || ""}
              </div>
            </div>
            {live && <StatusPill status={live.status} />}
            {/* Only while a turn runs here: the project's last saved phase is history, not activity. */}
            {botId === ROOM_ID && runningHere && phase && (
              <span className="chip" data-testid="phase-chip">
                {runningHere && room.activeCat ? `${room.activeCat.name} · ${phase}` : phase}
              </span>
            )}
            {runningHere && <span className="chip border-cyan/40 text-cyan">Turn running</span>}
          </div>
          <div className="flex items-center gap-2">
            {connection !== "open" && <span className="chip border-warn/40 text-warn">{connection === "down" ? "Offline" : "Connecting…"}</span>}
            <button
              type="button"
              className={clsx("btn btn-sm btn-ghost relative", inspectorOpen && "text-cyan")}
              onClick={toggleInspector}
              aria-pressed={inspectorOpen}
              aria-label="Toggle inspector"
              title="Inspector: telemetry, tasks, changes"
            >
              <FontAwesomeIcon icon={faGaugeHigh} />
              {contextAlert && <span data-testid="inspector-alert" className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-danger" />}
            </button>
            <button
              type="button"
              className={clsx("btn btn-sm btn-ghost", terminalOpen && "text-cyan")}
              onClick={toggleTerminal}
              aria-pressed={terminalOpen}
              aria-label="Toggle terminal"
              title="Terminal (Ctrl+`)"
            >
              <FontAwesomeIcon icon={faTerminal} />
            </button>
            <Link to={`/project/${pid}/settings`} className="btn btn-sm btn-ghost" aria-label="Project settings" title="Project settings (model overrides)">
              <FontAwesomeIcon icon={faGear} />
            </Link>
            <button className="btn btn-sm btn-ghost" onClick={() => void reset()} disabled={Boolean(runningHere)} title="Clear the Cat Crew chat and shared state">
              <FontAwesomeIcon icon={faRotateLeft} /> Reset
            </button>
          </div>
        </header>
        {notice && (
          <div className="border-b border-line px-6 py-2">
            {notice.error ? (
              <ErrorNote message={notice.text} />
            ) : (
              <p role="status" className="text-sm text-muted">
                {notice.text}
              </p>
            )}
          </div>
        )}
        <ThreadView key={`${pid}:${botId}`} projectId={pid} botId={botId} agent={agent} />
        {terminalUsed && <TerminalPanel key={pid} projectId={pid} open={terminalOpen} onClose={() => setTerm({ pid, open: false })} />}
      </section>

      {/* right: telemetry, tasks and changes; unmounted while closed, so it polls nothing */}
      {inspectorOpen && <RightInspector projectId={pid} tasks={room.tasks} changes={room.changes} loaded={room.loaded} resize={inspector} />}
    </div>
  );
}
