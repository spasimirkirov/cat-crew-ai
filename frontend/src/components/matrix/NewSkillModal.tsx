import { faPlus } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useId, useState, type FormEvent } from "react";

import { api } from "../../lib/api";
import type { Skill } from "../../lib/types";
import { errorText } from "../../state/store";
import { Modal } from "../Modal";
import { ErrorNote, Spinner } from "../Ui";

interface Props {
  onCreated: (skill: Skill) => void;
  onClose: () => void;
}

export function NewSkillModal({ onCreated, onClose }: Props) {
  const formId = useId();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = name.trim().length > 0 && !busy;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.skills.create({ name: name.trim(), description: description.trim(), body }));
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <Modal
      title="New skill"
      width="max-w-2xl"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form={formId} className="btn btn-primary" disabled={!ready}>
            {busy ? <Spinner className="text-[#06202e]" /> : <FontAwesomeIcon icon={faPlus} />}
            Create skill
          </button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="space-y-3">
        <div>
          <label className="label" htmlFor={`${formId}-name`}>
            Name
          </label>
          <input id={`${formId}-name`} className="input" autoFocus required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. release-checklist" />
        </div>
        <div>
          <label className="label" htmlFor={`${formId}-desc`}>
            Description
          </label>
          <input
            id={`${formId}-desc`}
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="One line: when should a cat reach for this playbook?"
          />
        </div>
        <div>
          <label className="label" htmlFor={`${formId}-body`}>
            Playbook (Markdown)
          </label>
          <textarea
            id={`${formId}-body`}
            className="input min-h-[240px] resize-y font-mono text-xs leading-relaxed"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            spellCheck={false}
            placeholder={"# Release checklist\n\n1. Run the test suite\n2. Bump the version\n3. Tag and publish"}
          />
        </div>
        <ErrorNote message={error} />
      </form>
    </Modal>
  );
}
