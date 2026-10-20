import { faArrowRightToBracket, faCat, faUserPlus } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";

import { api } from "../lib/api";
import { useAuth } from "../state/auth";
import { errorText } from "../state/store";
import { ErrorNote, Spinner } from "./Ui";

/** ``#/invite/<token>`` from an admin's invite link. */
function inviteToken(): string | null {
  const match = /^#\/invite\/([A-Za-z0-9_-]+)/.exec(typeof window !== "undefined" ? window.location.hash : "");
  return match ? match[1] : null;
}

type Kind = "setup" | "login" | "invite";

function AuthForm({ kind, token }: { kind: Kind; token: string | null }) {
  const formId = useId();
  const signedIn = useAuth((s) => s.signedIn);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inviteOk, setInviteOk] = useState<boolean | null>(kind === "invite" ? null : true);

  useEffect(() => {
    if (kind !== "invite" || !token) return;
    api.auth
      .invite(token)
      .then(() => setInviteOk(true))
      .catch((err) => {
        setInviteOk(false);
        setError(errorText(err));
      });
  }, [kind, token]);

  const creating = kind !== "login";
  const ready = username.trim() && password && (!creating || password === repeat) && (kind !== "setup" || code.trim()) && !busy && inviteOk;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const res =
        kind === "setup"
          ? await api.auth.register(username.trim(), password, code.trim())
          : kind === "invite" && token
            ? await api.auth.acceptInvite(token, username.trim(), password)
            : await api.auth.login(username.trim(), password);
      if (kind === "invite") window.location.hash = "#/";
      signedIn(res.user);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  const title = kind === "setup" ? "Create the admin account" : kind === "invite" ? "Join CatCrewAI" : "Sign in";
  const hint =
    kind === "setup"
      ? "This server has no accounts yet. The first one is the admin. The setup code is printed in the server's console."
      : kind === "invite"
        ? "You were invited. Pick a username and a password."
        : "This CatCrewAI runs on a server: sign in to reach your projects.";

  return (
    <form id={formId} onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6" aria-label={title}>
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-cyan/30 to-violet/30">
          <FontAwesomeIcon icon={faCat} className="text-cyan" aria-hidden />
        </span>
        <div>
          <h1 className="text-base font-semibold text-ink">{title}</h1>
          <p className="text-xs text-muted">{hint}</p>
        </div>
      </div>
      {kind === "setup" && (
        <div>
          <label className="label" htmlFor={`${formId}-code`}>
            Setup code
          </label>
          <input id={`${formId}-code`} className="input font-mono uppercase" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" />
        </div>
      )}
      <div>
        <label className="label" htmlFor={`${formId}-user`}>
          Username
        </label>
        <input
          id={`${formId}-user`}
          className="input"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus={kind !== "setup"}
        />
      </div>
      <div>
        <label className="label" htmlFor={`${formId}-pass`}>
          Password
        </label>
        <input
          id={`${formId}-pass`}
          className="input"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={creating ? "new-password" : "current-password"}
        />
        {creating && <p className="mt-1 text-[11px] text-muted">At least 10 characters.</p>}
      </div>
      {creating && (
        <div>
          <label className="label" htmlFor={`${formId}-repeat`}>
            Repeat the password
          </label>
          <input id={`${formId}-repeat`} className="input" type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" />
          {repeat && repeat !== password && <p className="mt-1 text-[11px] text-danger">The passwords differ.</p>}
        </div>
      )}
      <ErrorNote message={error} />
      <button type="submit" className="btn btn-primary w-full justify-center" disabled={!ready}>
        {busy ? <Spinner className="text-[#06202e]" /> : <FontAwesomeIcon icon={creating ? faUserPlus : faArrowRightToBracket} />}
        {kind === "login" ? "Sign in" : "Create account"}
      </button>
    </form>
  );
}

/**
 * Before anything talks to the engine: local mode passes straight through; a server shows the
 * first-admin setup, an invite or the login until there is a session. ``children`` (the live socket
 * and the app) mount only once signed in, so nothing connects without a session.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { phase, needsSetup, error, check } = useAuth();
  const [token, setToken] = useState(inviteToken);

  useEffect(() => {
    void check();
    const onHash = () => setToken(inviteToken());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [check]);

  if (phase === "signed-in") return <>{children}</>;
  if (phase === "checking") {
    return (
      <div className="flex h-full items-center justify-center bg-bg">
        <Spinner />
      </div>
    );
  }
  if (phase === "error") {
    // The engine is not answering yet: the startup screen inside explains and retries.
    return <>{children}</>;
  }
  const kind: Kind = token ? "invite" : needsSetup ? "setup" : "login";
  return (
    <div className="flex h-full items-center justify-center bg-bg p-4 text-ink">
      <AuthForm key={kind} kind={kind} token={token} />
      {error && <span className="sr-only">{error}</span>}
    </div>
  );
}
