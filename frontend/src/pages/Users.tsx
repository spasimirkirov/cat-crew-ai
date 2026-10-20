import { faCopy, faShieldHalved, faTrash, faUserPlus, faUsers } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorNote, PageHeader, Spinner } from "../components/Ui";
import { api } from "../lib/api";
import type { AuthUser, UsersPayload } from "../lib/types";
import { useAuth } from "../state/auth";
import { errorText } from "../state/store";

/** Admins only (remote mode): accounts and one-time invite links. */
export function UsersPage() {
  const me = useAuth((s) => s.user);
  const mode = useAuth((s) => s.mode);
  const [data, setData] = useState<UsersPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState<{ url: string; role: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    api.users
      .list()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err) => setError(errorText(err)));
  }, []);

  useEffect(() => {
    if (mode === "remote") load();
  }, [load, mode]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const makeInvite = (role: "user" | "admin") =>
    act(async () => {
      const res = await api.users.invite(role);
      setInvite({ url: res.url, role: res.role });
      setCopied(false);
    });

  const copy = async () => {
    if (!invite) return;
    try {
      await navigator.clipboard.writeText(invite.url);
      setCopied(true);
    } catch {
      /* the link is selectable on the page */
    }
  };

  if (mode !== "remote") {
    return (
      <div className="flex h-full flex-col">
        <PageHeader title="Users" />
        <EmptyState icon={faUsers} title="CatCrewAI runs locally" hint="Accounts exist only when it runs on a server (python -m backend --ui --host 0.0.0.0)." />
      </div>
    );
  }

  const sandboxReady = data?.sandbox.ready ?? false;
  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Users"
        subtitle="Who can sign in to this server. New accounts come from one-time invite links."
        actions={
          <>
            <button className="btn" onClick={() => void makeInvite("admin")}>
              <FontAwesomeIcon icon={faShieldHalved} />
              Invite an admin
            </button>
            <button
              className="btn btn-primary"
              onClick={() => void makeInvite("user")}
              disabled={!sandboxReady}
              title={sandboxReady ? undefined : `Regular users need agent isolation first: ${data?.sandbox.detail ?? ""}`}
            >
              <FontAwesomeIcon icon={faUserPlus} />
              Invite a user
            </button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto px-8 pb-6 pt-4">
        <ErrorNote message={error} />
        {data && !sandboxReady && (
          <div className="rounded-lg border border-line bg-white/[0.02] px-3 py-2 text-xs text-muted" role="note">
            Regular users can be invited once their agents run isolated: {data.sandbox.detail} Admins can be invited now; they are trusted with the whole server.
          </div>
        )}
        {invite && (
          <div className="card space-y-2 p-3" role="status">
            <p className="text-sm text-ink">
              One-time invite for {invite.role === "admin" ? "an admin" : "a user"}. Send this link; it works once and expires in 3 days.
            </p>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-white/5 px-2 py-1 font-mono text-xs" title={invite.url}>
                {invite.url}
              </code>
              <button className="btn btn-sm" onClick={() => void copy()}>
                <FontAwesomeIcon icon={faCopy} />
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
        )}
        {!data ? (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        ) : (
          <ul className="space-y-2">
            {data.users.map((u: AuthUser) => (
              <li key={u.id} className="card flex items-center gap-3 p-3" data-testid="user-row">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-cyan/15 text-sm font-semibold uppercase text-cyan">{u.username[0]}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm text-ink">
                    {u.username}
                    {u.id === me?.id && <span className="ml-2 text-xs text-muted">(you)</span>}
                  </span>
                  <span className="text-xs text-muted">{u.role === "admin" ? "Admin: settings, models, keys and users" : "User: own projects only"}</span>
                </span>
                {u.id !== me?.id && (
                  <>
                    <select
                      className="input w-auto py-1 text-xs"
                      aria-label={`Role of ${u.username}`}
                      value={u.role}
                      onChange={(e) => void act(() => api.users.setRole(u.id, e.target.value as "user" | "admin"))}
                    >
                      <option value="user">User</option>
                      <option value="admin">Admin</option>
                    </select>
                    <button className="btn btn-ghost btn-sm" aria-label={`Remove ${u.username}`} onClick={() => {
                        if (window.confirm(`Remove ${u.username}? They cannot sign in any more; their files stay on the server.`)) void act(() => api.users.remove(u.id));
                      }}>
                      <FontAwesomeIcon icon={faTrash} />
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
