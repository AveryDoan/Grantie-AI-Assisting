import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { api, setToken, setUnauthorisedHandler } from "./api";

// Sign-in:
//  - APP_MODE=demo     -> /demo/login issues a demo token (synthetic data only)
//  - APP_MODE=supabase -> Supabase Auth email + password; the access token is
//                         sent to the API, which checks it and reads the role
//                         from `profiles` (never from the token itself).

interface Me {
  user_id: string;
  role: string;
  display_name: string | null;
}

interface AuthState {
  mode: "demo" | "supabase" | null;
  me: Me | null;
  ready: boolean;
  error: string | null;
  loginDemo: (role: string) => Promise<void>;
  loginPassword: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);
const STORAGE_KEY = "grant-review-token";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const supabaseKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;
const supabase: SupabaseClient | null = supabaseUrl && supabaseKey ? createClient(supabaseUrl, supabaseKey) : null;

function store(token: string | null) {
  try {
    if (token) sessionStorage.setItem(STORAGE_KEY, token);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: stay signed in for this page only */
  }
}

function stored(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<AuthState["mode"]>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const logout = useCallback(() => {
    setToken(null);
    store(null);
    setMe(null);
    void supabase?.auth.signOut();
  }, []);

  const activate = useCallback(async (token: string) => {
    setToken(token);
    const profile = await api.me();
    if (profile.role !== "officer" && profile.role !== "admin") {
      setToken(null);
      throw new Error("This workspace is for grants officers. Your account does not have the officer role.");
    }
    store(token);
    setMe(profile);
  }, []);

  useEffect(() => {
    setUnauthorisedHandler(logout);
    (async () => {
      try {
        const health = await api.health();
        setMode(health.mode);
        const saved = stored();
        if (saved) await activate(saved).catch(() => logout());
      } catch {
        setError("Cannot reach the API. Is it running on port 8000?");
      } finally {
        setReady(true);
      }
    })();
  }, [activate, logout]);

  const loginDemo = async (role: string) => {
    setError(null);
    const res = await api.demoLogin(role);
    await activate(res.access_token);
  };

  const loginPassword = async (email: string, password: string) => {
    setError(null);
    if (!supabase) throw new Error("Supabase sign-in is not configured (VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_KEY).");
    const { data, error: err } = await supabase.auth.signInWithPassword({ email, password });
    if (err || !data.session) throw new Error(err?.message ?? "Sign-in failed");
    await activate(data.session.access_token);
  };

  return (
    <AuthContext.Provider value={{ mode, me, ready, error, loginDemo, loginPassword, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
