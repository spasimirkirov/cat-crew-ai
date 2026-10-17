import { faBug, faPaperPlane, faStop, faWandMagicSparkles } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import type { Turn } from "../../lib/types";
import { errorText, useStore } from "../../state/store";
import { ErrorNote } from "../Ui";

interface Props {
  projectId: string;
  botId: string;
  botName: string;
  isRoom: boolean;
  turn: Turn | null;
}

export function Composer({ projectId, botId, botName, isRoom, turn }: Props) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const area = useRef<HTMLTextAreaElement | null>(null);
  const sendMessage = useStore((s) => s.sendMessage);

  const running = turn !== null;
  const mine = running && turn.project_id === projectId;

  // auto-grow up to ~8 lines
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  useEffect(() => {
    setNote(null);
    setError(null);
  }, [botId, projectId]);

  const send = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await sendMessage(projectId, botId, body);
      setText("");
      setNote(res.queued ? "A turn is running: your message is queued and starts right after it." : null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const report = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.chat.report(projectId, botId, body);
      setText("");
      setNote(
        res.queued
          ? "A turn is running: the report is queued and starts right after it."
          : "Problem reported: the team lead is writing the fix plan. Follow it in Cat Crew.",
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const steer = async () => {
    const body = text.trim();
    if (!body || !turn || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.chat.steer(turn.id, body);
      setText("");
      setNote("Steering note sent: it lands at the next step.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!turn) return;
    try {
      await api.chat.abort(turn.id);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div className="border-t border-line bg-[#0e1119] px-4 py-3">
      <ErrorNote message={error} />
      {note && <div className="mb-2 text-xs text-muted">{note}</div>}
      {running && !mine && <div className="mb-2 text-xs text-muted">Another project's turn is running. New messages queue behind it.</div>}
      <div className="flex items-end gap-2">
        <textarea
          ref={area}
          rows={1}
          value={text}
          aria-label={`Message ${botName}`}
          placeholder={isRoom ? "Talk to Cat Crew — ask, plan, or describe what to build…" : `Message ${botName}…`}
          className="input max-h-[200px] min-h-[42px] flex-1 resize-none py-2.5 leading-snug"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
          }}
        />
        {mine && (
          <>
            <button type="button" className="btn h-[42px]" title="Add guidance to the running turn" disabled={!text.trim() || busy} onClick={() => void steer()}>
              <FontAwesomeIcon icon={faWandMagicSparkles} /> Steer
            </button>
            <button type="button" className="btn btn-danger h-[42px]" onClick={() => void stop()} aria-label="Stop the running turn">
              <FontAwesomeIcon icon={faStop} /> Stop
            </button>
          </>
        )}
        <button
          type="button"
          className="btn h-[42px]"
          title={isRoom ? "Report a problem: the team lead plans the fix, the cats fix it, QA verifies" : `Report a problem with ${botName}'s work: the team lead plans the fix and ${botName} gets it`}
          disabled={!text.trim() || busy}
          onClick={() => void report()}
        >
          <FontAwesomeIcon icon={faBug} /> Report problem
        </button>
        <button type="button" className="btn btn-primary h-[42px]" disabled={!text.trim() || busy} onClick={() => void send()} aria-label={running ? "Queue message" : "Send message"}>
          <FontAwesomeIcon icon={faPaperPlane} /> {running ? "Queue" : "Send"}
        </button>
      </div>
      <div className="mt-1.5 flex justify-between text-[11px] text-muted">
        <span>
          <span className="kbd">Enter</span> send · <span className="kbd">Shift</span>+<span className="kbd">Enter</span> new line
        </span>
        {isRoom && <span>@name talks to one cat</span>}
      </div>
    </div>
  );
}
