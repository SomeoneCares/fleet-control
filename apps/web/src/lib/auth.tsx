import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { UNAUTHORIZED_EVENT, api, type Me } from "../api/client";

interface AuthState {
  /** undefined while the first check runs, null when nobody is signed in. */
  me: Me | null | undefined;
  setMe: (me: Me | null) => void;
  can: (permission: string) => boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);

  useEffect(() => {
    let live = true;
    api.me().then((m) => live && setMe(m)).catch(() => live && setMe(null));
    const lost = () => setMe(null);
    window.addEventListener(UNAUTHORIZED_EVENT, lost);
    return () => {
      live = false;
      window.removeEventListener(UNAUTHORIZED_EVENT, lost);
    };
  }, []);

  const can = useCallback((permission: string) => Boolean(me?.permissions.includes(permission)), [me]);
  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setMe(null);
    }
  }, []);

  return <AuthContext.Provider value={{ me, setMe, can, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

/** The signed-in person; only screens behind the sign-in gate use it. */
export function useMe(): Me {
  const { me } = useAuth();
  if (!me) throw new Error("not signed in");
  return me;
}
