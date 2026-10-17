import { faDownload } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useId, useState, type FormEvent } from "react";

import { errorText } from "../../state/store";
import { Modal } from "../Modal";
import { ErrorNote, Spinner } from "../Ui";

interface Props {
  title: string;
  hint: string;
  placeholder: string;
  /** Runs the import; resolves with the backend's human message. */
  onImport: (url: string) => Promise<string>;
  onDone: (message: string) => void;
  onClose: () => void;
}

export function ImportUrlModal({ title, hint, placeholder, onImport, onDone, onClose }: Props) {
  const formId = useId();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = url.trim().length > 0 && !busy;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const message = await onImport(url.trim());
      onDone(message);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form={formId} className="btn btn-primary" disabled={!ready}>
            {busy ? <Spinner className="text-[#06202e]" /> : <FontAwesomeIcon icon={faDownload} />}
            Import
          </button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="space-y-3">
        <p className="text-sm text-muted">{hint}</p>
        <div>
          <label className="label" htmlFor={`${formId}-url`}>
            URL
          </label>
          <input
            id={`${formId}-url`}
            className="input font-mono text-xs"
            type="text"
            inputMode="url"
            spellCheck={false}
            autoFocus
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={placeholder}
          />
        </div>
        <ErrorNote message={error} />
      </form>
    </Modal>
  );
}
