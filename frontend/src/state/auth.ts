import { create } from "zustand";

import { api, UNAUTHORIZED_EVENT } from "../lib/api";
import type { AuthUser } from "../lib/types";
import { errorText } from "./store";

/**
 * Who is signed in. Local mode (the desktop app) has no login: the person is the local admin.
 * Remote mode (a server) shows the login, the first-admin setup or an invite until a session exists.
 */
interface AuthState {
  phase: "checking" | "signed-in" | "signed-out" | "error";
  mode: "local" | "remote";
  needsSetup: boolean;
  user: AuthUser | null;
  error: string | null;
  check: () => Promise<void>;
  signedIn: (user: AuthUser) => void;
  signOut: () => Promise<void>;
}

export const useAuth = create<AuthState>()((set) => ({
  phase: "checking",
  mode: "local",
  needsSetup: false,
  user: null,
  error: null,
  check: async () => {
    try {
      const s = await api.auth.status();
      set({
        mode: s.mode,
        needsSetup: s.needs_setup,
        user: s.user,
        error: null,
        phase: s.mode === "local" || s.user ? "signed-in" : "signed-out",
      });
    } catch (err) {
      set({ phase: "error", error: errorText(err) });
    }
  },
  signedIn: (user) => set({ user, needsSetup: false, phase: "signed-in", error: null }),
  signOut: async () => {
    try {
      await api.auth.logout();
    } finally {
      set({ user: null, phase: "signed-out" });
    }
  },
}));

/** True for the local admin and for admins of a server. */
export const useIsAdmin = (): boolean => useAuth((s) => s.mode === "local" || s.user?.role === "admin");

if (typeof window !== "undefined") {
  window.addEventListener(UNAUTHORIZED_EVENT, () => {
    const { mode, phase } = useAuth.getState();
    if (mode === "remote" && phase === "signed-in") useAuth.setState({ user: null, phase: "signed-out" });
  });
}
