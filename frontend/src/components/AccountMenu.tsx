import { faArrowRightFromBracket, faKey } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useState } from "react";

import { api } from "../lib/api";
import { useAuth } from "../state/auth";
import { errorText } from "../state/store";
import { Modal } from "./Modal";
import { ErrorNote, Spinner } from "./Ui";

/** Remote mode: who is signed in, sign out, change the password. */
export function AccountMenu() {
  const user = useAuth((s) => s.user);
  const signOut = useAuth((s) => s.signOut);
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!user) return null;

  const change = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.auth.changePassword(oldPw, newPw);
      setChanging(false);
      await signOut(); // every session ended: sign in again with the new password
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative">
      <button
        type="button"
        title={`Signed in as ${user.username}`}
        aria-label="Account"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 w-8 items-center justify-center rounded-full bg-cyan/15 text-xs font-semibold uppercase text-cyan hover:bg-cyan/25 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60"
      >
        {user.username[0]}
      </button>
      {open && (
        <div role="menu" className="card absolute bottom-0 left-10 z-40 w-56 space-y-1 p-2 text-sm shadow-xl">
          <div className="px-2 py-1 text-xs text-muted">
            Signed in as <span className="text-ink">{user.username}</span> ({user.role})
          </div>
          <button role="menuitem" className="btn btn-ghost btn-sm w-full justify-start" onClick={() => { setOpen(false); setChanging(true); }}>
            <FontAwesomeIcon icon={faKey} />
            Change password
          </button>
          <button role="menuitem" className="btn btn-ghost btn-sm w-full justify-start" onClick={() => void signOut()}>
            <FontAwesomeIcon icon={faArrowRightFromBracket} />
            Sign out
          </button>
        </div>
      )}
      {changing && (
        <Modal
          title="Change password"
          width="max-w-sm"
          onClose={() => !busy && setChanging(false)}
          footer={
            <>
              <button className="btn btn-ghost" onClick={() => setChanging(false)} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={() => void change()} disabled={busy || !oldPw || newPw.length < 10}>
                {busy && <Spinner />}
                Change
              </button>
            </>
          }
        >
          <div className="space-y-3">
            <div>
              <label className="label" htmlFor="pw-old">
                Current password
              </label>
              <input id="pw-old" className="input" type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} autoComplete="current-password" />
            </div>
            <div>
              <label className="label" htmlFor="pw-new">
                New password (at least 10 characters)
              </label>
              <input id="pw-new" className="input" type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" />
            </div>
            <p className="text-xs text-muted">Every session of this account ends; sign in again afterwards.</p>
            <ErrorNote message={error} />
          </div>
        </Modal>
      )}
    </div>
  );
}
