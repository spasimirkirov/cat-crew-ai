import { faShieldHalved } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useState } from "react";

import { api } from "../../lib/api";
import type { Approval, Ask } from "../../lib/types";
import { errorText, useStore } from "../../state/store";

/** Permission prompts and questions from a running turn (POST /api/approvals, /api/asks). */
export function Approvals({ approvals, asks }: { approvals: Approval[]; asks: Ask[] }) {
  const agents = useStore((s) => s.agents);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState<Record<string, string>>({});
  if (approvals.length === 0 && asks.length === 0) return null;
  const guard = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <div className="space-y-2" data-testid="approvals">
      {approvals.map((a) => (
        <div key={a.approval_id} className="rounded-lg border border-warn/40 bg-warn/[0.07] p-3" role="alert">
          <div className="flex items-center gap-2 text-sm font-medium">
            <FontAwesomeIcon icon={faShieldHalved} className="text-warn" />
            {agents[a.bot_id]?.name ?? a.bot_id} wants to use <code className="rounded bg-white/10 px-1 font-mono text-xs">{a.tool}</code>
          </div>
          {a.detail && <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-[#0b0e15] p-2 font-mono text-[11px] text-muted">{a.detail}</pre>}
          <div className="mt-2 flex gap-2">
            <button className="btn btn-sm btn-emerald" onClick={() => void guard(() => api.chat.approve(a.approval_id, "allow"))}>
              Allow
            </button>
            <button className="btn btn-sm" onClick={() => void guard(() => api.chat.approve(a.approval_id, "allow_always"))}>
              Always allow
            </button>
            <button className="btn btn-sm btn-danger" onClick={() => void guard(() => api.chat.approve(a.approval_id, "deny"))}>
              Deny
            </button>
          </div>
        </div>
      ))}
      {asks.map((k) => (
        <div key={k.ask_id} className="rounded-lg border border-violet/40 bg-violet/[0.07] p-3" role="alert">
          <div className="text-sm font-medium">
            {agents[k.bot_id]?.name ?? k.bot_id} asks: <span className="font-normal text-ink/90">{k.prompt}</span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {k.choices.map((c) => (
              <button key={c} className="btn btn-sm" onClick={() => void guard(() => api.chat.answer(k.ask_id, c))}>
                {c}
              </button>
            ))}
            {k.choices.length === 0 && (
              <form
                className="flex flex-1 gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void guard(() => api.chat.answer(k.ask_id, text[k.ask_id] ?? ""));
                }}
              >
                <input className="input flex-1" aria-label="Your answer" value={text[k.ask_id] ?? ""} onChange={(e) => setText({ ...text, [k.ask_id]: e.target.value })} />
                <button className="btn btn-sm btn-primary" type="submit">
                  Answer
                </button>
              </form>
            )}
          </div>
        </div>
      ))}
      {error && <div className="text-xs text-danger">{error}</div>}
    </div>
  );
}
