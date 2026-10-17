import { useId, useState, type FormEvent } from "react";

import { api } from "../../lib/api";
import type { Project } from "../../lib/types";
import { errorText } from "../../state/store";
import { Modal } from "../Modal";
import { ErrorNote, Spinner } from "../Ui";

export function RenameProjectModal({ project, onClose, onSaved }: { project: Project; onClose: () => void; onSaved: (p: Project) => void }) {
  const uid = useId();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [nameError, setNameError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (busy) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError("The project needs a name.");
      return;
    }
    setNameError(null);
    setError(null);
    setBusy(true);
    try {
      const saved = await api.projects.patch(project.id, { name: trimmed, description: description.trim() });
      onSaved(saved);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Rename project"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form={`${uid}-rename`} className="btn btn-primary" disabled={busy}>
            {busy && <Spinner className="text-[#06202e]" />}
            Save
          </button>
        </>
      }
    >
      <form id={`${uid}-rename`} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div>
          <label className="label" htmlFor={`${uid}-n`}>
            Name
          </label>
          <input
            id={`${uid}-n`}
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
            aria-required="true"
            aria-invalid={nameError ? true : undefined}
            maxLength={120}
          />
          {nameError && (
            <p role="alert" className="mt-1 text-xs text-danger">
              {nameError}
            </p>
          )}
        </div>
        <div>
          <label className="label" htmlFor={`${uid}-d`}>
            Description
          </label>
          <textarea id={`${uid}-d`} className="input resize-y" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <ErrorNote message={error} />
      </form>
    </Modal>
  );
}

export function DeleteProjectModal({ project, onClose, onDeleted }: { project: Project; onClose: () => void; onDeleted: (p: Project) => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.projects.remove(project.id);
      onDeleted(project);
    } catch (err) {
      // e.g. 409 "Stop the running turn before deleting this project."
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Delete project"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-danger" onClick={() => void confirm()} disabled={busy}>
            {busy && <Spinner />}
            Delete project
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        <p>
          Delete <span className="font-semibold">{project.name}</span> from CatCrewAI? Files in its workspace folder on your computer are not
          touched.
        </p>
        <ErrorNote message={error} />
      </div>
    </Modal>
  );
}
