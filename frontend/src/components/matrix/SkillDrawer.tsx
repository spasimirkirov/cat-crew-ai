import { faFileLines, faLock, faTrash } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import type { Agent, Skill } from "../../lib/types";
import { errorText } from "../../state/store";
import { EmptyState, ErrorNote, Spinner } from "../Ui";
import { Drawer, Field } from "./Drawer";
import { TypeChip } from "./TypeChip";

interface Props {
  /** The matrix row (live assignment state). */
  skill: Skill;
  agents: Agent[];
  onClose: () => void;
  onDeleted: () => void;
}

export function SkillDrawer({ skill, agents, onClose, onDeleted }: Props) {
  const [detail, setDetail] = useState<Skill | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    api.skills
      .get(skill.id)
      .then((d) => !cancelled && setDetail(d))
      .catch((err) => !cancelled && setError(errorText(err)));
    return () => {
      cancelled = true;
    };
  }, [skill.id]);

  const bundled = skill.type === "bundled";
  const info = detail ?? skill;
  const attached = skill.is_global ? agents : agents.filter((a) => skill.agent_ids.includes(a.id));
  const essentialFor = agents.filter((a) => (skill.locked_agent_ids ?? []).includes(a.id));

  const remove = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.skills.remove(skill.id);
      onDeleted();
    } catch (err) {
      setDeleteError(errorText(err));
      setDeleting(false);
    }
  };

  return (
    <Drawer
      title={skill.name}
      onClose={onClose}
      subtitle={
        <>
          <TypeChip type={skill.type} />
          {info.version && <span className="chip font-mono">v{info.version}</span>}
        </>
      }
      footer={
        confirming ? (
          <>
            <span className="mr-auto text-sm text-danger">Delete "{skill.name}" for good?</span>
            <button className="btn btn-sm" onClick={() => setConfirming(false)} disabled={deleting}>
              Cancel
            </button>
            <button className="btn btn-sm btn-danger" onClick={remove} disabled={deleting}>
              {deleting ? <Spinner /> : <FontAwesomeIcon icon={faTrash} />}
              Confirm delete
            </button>
          </>
        ) : (
          <span title={bundled ? "Bundled playbooks ship with CatCrewAI and cannot be deleted." : undefined} className="inline-flex">
            <button className="btn btn-danger btn-sm" disabled={bundled} onClick={() => setConfirming(true)}>
              <FontAwesomeIcon icon={bundled ? faLock : faTrash} />
              Delete skill
            </button>
          </span>
        )
      }
    >
      <ErrorNote message={deleteError} />
      <Field label="Description">{skill.description || <span className="text-muted">No description</span>}</Field>

      <Field label={skill.is_global ? "Attached to" : `Attached to (${attached.length})`}>
        {skill.is_global ? (
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

      {essentialFor.length > 0 && (
        <Field label="Always on">
          <span className="flex flex-wrap items-center gap-1.5 text-sm">
            <FontAwesomeIcon icon={faLock} className="text-muted" />
            Essential for {essentialFor.map((a) => a.name).join(", ")}: it cannot be unassigned.
          </span>
        </Field>
      )}

      <div className="grid grid-cols-2 gap-x-4 gap-y-4">
        <Field label="Type">{skill.type}</Field>
        <Field label="Version">{info.version ? <span className="font-mono">{info.version}</span> : <span className="text-muted">n/a</span>}</Field>
        <Field label="Target roles">
          {info.target_roles.length ? (
            <span className="flex flex-wrap gap-1.5">
              {info.target_roles.map((r) => (
                <span key={r} className="chip">
                  {r}
                </span>
              ))}
            </span>
          ) : (
            <span className="text-muted">Any role</span>
          )}
        </Field>
        <Field label="Source URL">
          {info.source_url ? (
            /^https?:\/\//i.test(info.source_url) ? (
              <a href={info.source_url} target="_blank" rel="noreferrer noopener" className="break-all font-mono text-xs text-cyan hover:underline">
                {info.source_url}
              </a>
            ) : (
              <span className="break-all font-mono text-xs">{info.source_url}</span>
            )
          ) : (
            <span className="text-muted">Local</span>
          )}
        </Field>
      </div>

      <Field label="Playbook file">
        {info.playbook_path ? <code className="break-all font-mono text-xs text-cyan/90">{info.playbook_path}</code> : <span className="text-muted">n/a</span>}
      </Field>

      <div>
        <div className="label">SKILL.md</div>
        {error ? (
          <ErrorNote message={error} />
        ) : !detail ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted">
            <Spinner /> Loading playbook
          </div>
        ) : detail.body ? (
          <pre
            tabIndex={0}
            aria-label="Playbook markdown"
            className="max-h-[46vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-[#0f121a] p-3 font-mono text-xs leading-relaxed text-ink/90"
          >
            {detail.body}
          </pre>
        ) : (
          <EmptyState icon={faFileLines} title="No playbook body" hint="This skill has no Markdown content." />
        )}
      </div>
    </Drawer>
  );
}
