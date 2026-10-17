import { faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useNavigate } from "react-router-dom";

import type { ModelIssue } from "../../lib/types";
import { useModelHealth } from "../../state/modelHealth";
import { Modal } from "../Modal";

const NONE: ModelIssue[] = []; // a stable empty list: a fresh [] per render would loop the store hook

/**
 * Shown once when CatCrewAI opens and a model picked for a role can no longer be used
 * (retired, deprecated, renamed, its host offline or without a key).
 */
export function ModelNotice() {
  const open = useModelHealth((s) => s.noticeOpen);
  const issues = useModelHealth((s) => s.report?.issues ?? NONE);
  const dismiss = useModelHealth((s) => s.dismiss);
  const navigate = useNavigate();

  if (!open || issues.length === 0) return null;

  const reassign = () => {
    const roles = issues.map((i) => i.role).join(",");
    dismiss();
    navigate(`/models?fix=${encodeURIComponent(roles)}`);
  };

  return (
    <Modal
      title="Model Configuration Notice"
      width="max-w-md"
      onClose={dismiss}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={dismiss}>
            Dismiss
          </button>
          <button type="button" className="btn btn-primary" onClick={reassign}>
            Re-assign Models
          </button>
        </>
      }
    >
      <p className="flex items-start gap-2 text-sm text-muted">
        <FontAwesomeIcon icon={faTriangleExclamation} className="mt-0.5 shrink-0 text-warn" aria-hidden />
        {issues.length === 1 ? "A model you picked can't be used right now." : "Some models you picked can't be used right now."} Those roles will fail until you choose another model.
      </p>
      <ul className="mt-3 flex flex-col gap-2">
        {issues.map((i) => (
          <li key={i.role} className="rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm" data-role={i.role}>
            {i.message}
          </li>
        ))}
      </ul>
    </Modal>
  );
}
