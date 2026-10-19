import { faCode, faTrash } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import type { Agent, Tool } from "../../lib/types";
import { errorText } from "../../state/store";
import { ErrorNote, Spinner } from "../Ui";
import { Drawer, Field } from "./Drawer";

interface Props {
  tool: Tool;
  agents: Agent[];
  onClose: () => void;
  onDeleted: () => void;
}

export function ToolDrawer({ tool, agents, onClose, onDeleted }: Props) {
  const [detail, setDetail] = useState<Tool | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    api.tools
      .get(tool.id)
      .then((d) => !cancelled && setDetail(d))
      .catch((err) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, [tool.id]);

  const essentialFor = agents.filter((a) => (tool.locked_agent_ids ?? []).includes(a.id));
  const attached = tool.is_global ? agents : agents.filter((a) => tool.agent_ids.includes(a.id));
  const schema = detail?.schema === undefined || detail?.schema === null ? null : JSON.stringify(detail.schema, null, 2);

  const remove = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.tools.remove(tool.id);
      onDeleted();
    } catch (err) {
      setDeleteError(errorText(err));
      setDeleting(false);
    }
  };

  return (
    <Drawer
      title={tool.name}
      onClose={onClose}
      subtitle={<span className="chip font-mono text-cyan/90">function tool</span>}
      footer={
        confirming ? (
          <>
            <span className="mr-auto text-sm text-danger">Unregister "{tool.name}"?</span>
            <button className="btn btn-sm" onClick={() => setConfirming(false)} disabled={deleting}>
              Cancel
            </button>
            <button className="btn btn-sm btn-danger" onClick={remove} disabled={deleting}>
              {deleting ? <Spinner /> : <FontAwesomeIcon icon={faTrash} />}
              Confirm delete
            </button>
          </>
        ) : essentialFor.length > 0 ? (
          <span className="mr-auto text-xs text-muted">In the bundle of {essentialFor.map((a) => a.name).join(", ")}: it cannot be deleted.</span>
        ) : (
          <button className="btn btn-danger btn-sm" onClick={() => setConfirming(true)}>
            <FontAwesomeIcon icon={faTrash} />
            Delete tool
          </button>
        )
      }
    >
      <ErrorNote message={deleteError} />
      <Field label="Handler">
        <code className="break-all font-mono text-xs text-cyan/90">{tool.handler}</code>
      </Field>
      <Field label="Description">{tool.description || <span className="text-muted">No description</span>}</Field>
      <Field label={tool.is_global ? "Attached to" : `Attached to (${attached.length})`}>
        {tool.is_global ? (
          <span className="text-emerald">Global: every cat</span>
        ) : attached.length === 0 ? (
          <span className="text-muted">No cat yet</span>
        ) : (
          <span className="flex flex-wrap gap-1.5">
            {attached.map((a) => (
              <span key={a.id} className="chip">
                {a.name}
              </span>
            ))}
          </span>
        )}
      </Field>
      <div>
        <div className="label">JSON schema</div>
        {error ? (
          <ErrorNote message={error} />
        ) : !detail ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted">
            <Spinner /> Loading schema
          </div>
        ) : schema ? (
          <pre
            tabIndex={0}
            aria-label="JSON schema"
            className="max-h-[52vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-[#0f121a] p-3 font-mono text-xs leading-relaxed text-ink/90"
          >
            {schema}
          </pre>
        ) : (
          <div className="flex items-center gap-2 text-sm text-muted">
            <FontAwesomeIcon icon={faCode} /> This tool has no schema.
          </div>
        )}
      </div>
    </Drawer>
  );
}
