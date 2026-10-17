import { faComments } from "@fortawesome/free-solid-svg-icons";
import { useMemo } from "react";

import { humanDuration, parseTs } from "../../lib/time";
import { ROOM_AVATAR, ROOM_ID, ROOM_NAME, isRoomId, type Agent, type FileChange, type Message } from "../../lib/types";
import { useNow } from "../../lib/useNow";
import { emptyThread } from "../../state/reducer";
import { useStore } from "../../state/store";
import { ExecutionCard } from "../ExecutionCard";
import { EmptyState, Spinner } from "../Ui";
import { Approvals } from "./Approvals";
import { BlockedModelCard } from "./BlockedModelCard";
import { Composer } from "./Composer";
import { MessageView } from "./MessageView";
import { useStickToBottom } from "./useStickToBottom";

/** File changes a cat made in the window before one of its messages. */
export function changesForMessage(msgs: Message[], index: number, changes: FileChange[]): FileChange[] {
  const m = msgs[index];
  if (m.role !== "assistant" || !m.bot_id) return [];
  const end = parseTs(m.created_at);
  const start = index > 0 ? parseTs(msgs[index - 1].created_at) : null;
  if (end === null) return [];
  return changes.filter((c) => {
    if (c.agent_id !== m.bot_id) return false;
    const t = parseTs(c.created_at);
    return t !== null && t <= end + 1000 && (start === null || t >= start - 1000);
  });
}

interface Props {
  projectId: string;
  botId: string;
  agent?: Agent;
}

export function ThreadView({ projectId, botId, agent }: Props) {
  const room = useStore((s) => s.rooms[projectId]);
  const agents = useStore((s) => s.agents);
  const turn = useStore((s) => s.currentTurn);
  const thread = room?.threads[botId] ?? emptyThread();
  const now = useNow(30_000);

  const messages = thread.messages;
  const last = messages[messages.length - 1];
  const stickSignal = `${messages.length}:${last?.id ?? ""}:${last?.text.length ?? 0}:${thread.steps.length}:${room?.approvals.length ?? 0}`;
  const { ref, onScroll } = useStickToBottom<HTMLDivElement>(stickSignal);

  const isRoom = botId === ROOM_ID;
  // Cat Crew is not in `agents` (it is not an agent); its card comes from the roster.
  const crewEntry = room?.roster.find((e) => e.is_room)?.agent;
  const crew = useMemo(() => ({ ...(crewEntry ?? { name: ROOM_NAME, color: "#38BDF8" }), avatar: crewEntry?.avatar || ROOM_AVATAR }), [crewEntry]);
  const changes = room?.changes ?? [];

  // Is the running turn about *this* thread? A direct turn owns its own thread;
  // a room turn shows live steps in the room and in each cat's thread.
  const turnHere = turn && turn.project_id === projectId && (turn.bot_id === botId || (turn.kind === "room" && (thread.steps.length > 0 || isRoom)));
  const showLive = Boolean(turnHere) || thread.steps.length > 0;
  const tick = useNow(1000);
  const elapsed = turn?.started_at && showLive ? Math.max(0, (tick - (parseTs(turn.started_at) ?? tick)) / 1000) : undefined;

  // "Now:" on the live card: what the turn is doing at this moment and for how long, so a long
  // wait reads as "waiting for your answer" or "thinking for 2m", not as nothing happening.
  const activity = useStore((s) => s.activity);
  const nowLine = useMemo(() => {
    if (!turnHere) return undefined;
    if (room?.asks.length) return "Waiting for your answer below";
    if (room?.approvals.length) return "Waiting for your approval below";
    const who = isRoom ? room?.activeCat?.cat_id || ROOM_ID : botId;
    const live = activity[who];
    if (!live || live.state === "idle") return undefined;
    const since = live.updated_at ? Math.max(0, (tick - (parseTs(live.updated_at) ?? tick)) / 1000) : 0;
    const what = live.text || "Working";
    return since >= 5 ? `${what} — ${humanDuration(since)}` : what;
  }, [turnHere, room?.asks.length, room?.approvals.length, room?.activeCat?.cat_id, isRoom, botId, activity, tick]);

  const rendered = useMemo(
    () =>
      messages.map((m, i) => (
        <MessageView key={m.id} message={m} agent={m.bot_id ? (isRoomId(m.bot_id) ? crew : agents[m.bot_id]) : m.role === "assistant" ? agent : undefined} now={now} changes={changesForMessage(messages, i, changes)} />
      )),
    [messages, agents, agent, crew, now, changes],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={ref} onScroll={onScroll} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5" data-testid="thread" data-loaded={thread.loaded}>
        {!thread.loaded && messages.length === 0 ? (
          <div className="flex justify-center py-12">
            <Spinner />
          </div>
        ) : messages.length === 0 && !showLive ? (
          <EmptyState
            icon={faComments}
            title={isRoom ? "Start the crew" : `Say hi to ${agent?.name ?? "this cat"}`}
            hint={isRoom ? "Talk to Cat Crew like any assistant: ask, plan, analyse requirements. Describe work and it calls the right cats — you'll see each one appear on the left." : "A direct 1:1 chat. This cat only sees this conversation."}
          />
        ) : (
          rendered
        )}
        {showLive && (
          <div className="animate-fade-up" data-testid="live-card">
            <ExecutionCard running steps={thread.steps} elapsed={elapsed} now={nowLine} title={isRoom ? (turn?.mode === "chat" ? `${ROOM_NAME} is thinking` : `${ROOM_NAME} is working`) : `${agent?.name ?? "Cat"} is working`} />
            {turn && elapsed !== undefined && elapsed > 90 && <div className="mt-1 text-[11px] text-muted">Running for {humanDuration(elapsed)}. Use Stop if this is taking too long.</div>}
          </div>
        )}
      </div>
      <div className="space-y-2 px-4 pb-2">
        {room?.modelIssue && <BlockedModelCard key={room.modelIssue.at} projectId={projectId} issue={room.modelIssue} />}
        <Approvals approvals={room?.approvals ?? []} asks={room?.asks ?? []} />
      </div>
      <Composer projectId={projectId} botId={botId} botName={isRoom ? ROOM_NAME : agent?.name ?? botId} isRoom={isRoom} turn={turn} />
    </div>
  );
}
