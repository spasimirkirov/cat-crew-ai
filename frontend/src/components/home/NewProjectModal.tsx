import { faFolderOpen } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import clsx from "clsx";
import { useId, useState, type FormEvent } from "react";

import { api } from "../../lib/api";
import type { Project } from "../../lib/types";
import { errorText } from "../../state/store";
import { Modal } from "../Modal";
import { ErrorNote, Spinner } from "../Ui";
import { FolderBrowserPanel } from "./FolderBrowser";

export const FORM_ID = "new-project-form";

interface Props {
  onClose: () => void;
  onCreated: (project: Project) => void;
}

export function NewProjectModal({ onClose, onCreated }: Props) {
  const uid = useId();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [folder, setFolder] = useState("");
  const [kickoff, setKickoff] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [browsing, setBrowsing] = useState(false);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (busy) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError("Give the project a name.");
      return;
    }
    setNameError(null);
    setError(null);
    setBusy(true);
    try {
      const path = folder.trim();
      const brief = kickoff.trim();
      const project = await api.projects.create({
        name: trimmed,
        description: description.trim(),
        folders: path ? [path] : [],
        ...(brief ? { kickoff: brief } : {}),
      });
      onCreated(project);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  // The folder browser replaces the form inside the same dialog, so the form keeps its
  // state and Escape steps back from the browser instead of closing everything.
  if (browsing) {
    return (
      <Modal title="Choose a workspace folder" onClose={() => setBrowsing(false)} width="max-w-xl">
        <FolderBrowserPanel
          startPath={folder}
          onCancel={() => setBrowsing(false)}
          onPick={(path) => {
            setFolder(path);
            setBrowsing(false);
          }}
        />
      </Modal>
    );
  }

  return (
    <Modal
      title="New project"
      onClose={onClose}
      width="max-w-xl"
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" form={FORM_ID} className="btn btn-primary" disabled={busy}>
            {busy && <Spinner className="text-[#06202e]" />}
            Create project
          </button>
        </>
      }
    >
      <form id={FORM_ID} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div>
          <label className="label" htmlFor={`${uid}-name`}>
            Name
          </label>
          <input
            id={`${uid}-name`}
            className={clsx("input", nameError && "border-danger/60")}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (nameError) setNameError(null);
            }}
            placeholder="My next big thing"
            autoFocus
            aria-required="true"
            aria-invalid={nameError ? true : undefined}
            aria-describedby={nameError ? `${uid}-name-err` : undefined}
            maxLength={120}
          />
          {nameError && (
            <p id={`${uid}-name-err`} role="alert" className="mt-1 text-xs text-danger">
              {nameError}
            </p>
          )}
        </div>

        <div>
          <label className="label" htmlFor={`${uid}-desc`}>
            Description
          </label>
          <textarea
            id={`${uid}-desc`}
            className="input min-h-[64px] resize-y"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What are you building? (optional)"
          />
        </div>

        <div>
          <label className="label" htmlFor={`${uid}-folder`}>
            Workspace folder
          </label>
          <div className="flex gap-2">
            <input
              id={`${uid}-folder`}
              className="input font-mono text-xs"
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder="Leave empty to let CatCrewAI create one"
              spellCheck={false}
              autoComplete="off"
            />
            <button type="button" className="btn shrink-0" onClick={() => setBrowsing(true)}>
              <FontAwesomeIcon icon={faFolderOpen} /> Browse...
            </button>
          </div>
        </div>

        <div>
          <label className="label" htmlFor={`${uid}-kickoff`}>
            Kickoff brief (optional)
          </label>
          <textarea
            id={`${uid}-kickoff`}
            className="input min-h-[88px] resize-y"
            rows={4}
            value={kickoff}
            onChange={(e) => setKickoff(e.target.value)}
            placeholder="Describe what the crew should build first."
          />
          <p className="mt-1.5 text-xs text-muted">
            If you write a brief, the crew starts working right away. Cats join the project on their own when a task needs them, so there is no crew to pick.
          </p>
        </div>

        <ErrorNote message={error} />
      </form>
    </Modal>
  );
}
