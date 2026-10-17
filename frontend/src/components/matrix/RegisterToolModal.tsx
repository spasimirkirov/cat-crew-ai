import { faCheck, faPlus } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useMemo, useState } from "react";

import { api } from "../../lib/api";
import type { AvailableTool, Tool } from "../../lib/types";
import { errorText } from "../../state/store";
import { Modal } from "../Modal";
import { EmptyState, ErrorNote, Spinner } from "../Ui";
import { SearchBox } from "./SearchBox";

interface Props {
  onRegistered: (tool: Tool) => void;
  onClose: () => void;
}

/** Lists the functions in `backend/tool_functions` that are not registered yet. */
export function RegisterToolModal({ onRegistered, onClose }: Props) {
  const [available, setAvailable] = useState<AvailableTool[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [done, setDone] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    api.tools
      .available()
      .then((list) => !cancelled && setAvailable(list))
      .catch((err) => !cancelled && setLoadError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (available ?? []).filter(
      (t) =>
        !t.registered &&
        !done.includes(t.handler) &&
        (!q || t.name.toLowerCase().includes(q) || t.handler.toLowerCase().includes(q) || t.description.toLowerCase().includes(q)),
    );
  }, [available, query, done]);

  const register = async (t: AvailableTool) => {
    setBusy(t.handler);
    setErrors((e) => {
      const { [t.handler]: _drop, ...rest } = e;
      return rest;
    });
    try {
      const tool = await api.tools.register(t.handler);
      setDone((d) => [...d, t.handler]);
      onRegistered(tool);
    } catch (err) {
      setErrors((e) => ({ ...e, [t.handler]: errorText(err) }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      title="Register tool"
      width="max-w-2xl"
      onClose={onClose}
      footer={
        <button className="btn" onClick={onClose}>
          Done
        </button>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-muted">Public functions found in backend/tool_functions that are not registered yet.</p>
        <SearchBox value={query} onChange={setQuery} placeholder="Search by name, handler or description" label="Search available tools" />
        {done.length > 0 && (
          <div role="status" className="flex items-center gap-2 text-xs text-emerald">
            <FontAwesomeIcon icon={faCheck} />
            Registered {done.length} {done.length === 1 ? "tool" : "tools"} this session.
          </div>
        )}
        {loadError ? (
          <ErrorNote message={loadError} />
        ) : available === null ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
            <Spinner /> Looking for tool functions
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={faCheck}
            title={query ? "Nothing matches" : "Everything is registered"}
            hint={query ? "Try another search." : "There are no unregistered tool functions left."}
          />
        ) : (
          <ul className="divide-y divide-line rounded-lg border border-line">
            {rows.map((t) => (
              <li key={t.handler} className="flex items-start gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{t.name}</div>
                  <div className="truncate font-mono text-[11px] text-cyan/80">{t.handler}</div>
                  <p className="mt-0.5 line-clamp-2 text-xs text-muted">{t.description || "No description"}</p>
                  {errors[t.handler] && (
                    <div className="mt-1.5">
                      <ErrorNote message={errors[t.handler]} />
                    </div>
                  )}
                </div>
                <button
                  className="btn btn-sm shrink-0"
                  disabled={busy !== null}
                  onClick={() => register(t)}
                  aria-label={`Register ${t.name}`}
                >
                  {busy === t.handler ? <Spinner /> : <FontAwesomeIcon icon={faPlus} className="text-cyan" />}
                  Register
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
