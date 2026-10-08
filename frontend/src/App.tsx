import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "./api";
import { useAuth } from "./auth";
import { AIBanner, Button, ErrorNotice, Icon, Loading } from "./ui";
import Queue from "./screens/Queue";
import Review from "./screens/Review";
import Signoff from "./screens/Signoff";
import LetterScreen from "./screens/Letter";
import Audit from "./screens/Audit";
import Evaluation from "./screens/Evaluation";
import "./officer.css";

// Hash routes keep deep links working without server configuration:
//   #/queue  #/applications/:id  #/applications/:id/signoff
//   #/applications/:id/letter  #/audit  #/evaluation
export type Route =
  | { name: "queue" }
  | { name: "review"; id: string }
  | { name: "signoff"; id: string }
  | { name: "letter"; id: string }
  | { name: "audit" }
  | { name: "evaluation" };

function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "applications" && parts[1]) {
    if (parts[2] === "signoff") return { name: "signoff", id: parts[1] };
    if (parts[2] === "letter") return { name: "letter", id: parts[1] };
    return { name: "review", id: parts[1] };
  }
  if (parts[0] === "audit") return { name: "audit" };
  if (parts[0] === "evaluation") return { name: "evaluation" };
  return { name: "queue" };
}

export function href(route: Route): string {
  switch (route.name) {
    case "queue": return "#/queue";
    case "review": return `#/applications/${route.id}`;
    case "signoff": return `#/applications/${route.id}/signoff`;
    case "letter": return `#/applications/${route.id}/letter`;
    case "audit": return "#/audit";
    case "evaluation": return "#/evaluation";
  }
}

export type Navigate = (route: Route) => void;

function useRoute(): [Route, Navigate] {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      setRoute(parse(window.location.hash));
      window.scrollTo({ top: 0, behavior: "smooth" });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return [route, (r) => { window.location.hash = href(r); }];
}

const TITLES: Record<Route["name"], string> = {
  queue: "Applications", review: "Application review", signoff: "Sign-off", letter: "Outcome letter",
  audit: "Audit trail", evaluation: "Evaluation",
};

function Shell({ route, navigate, children }: { route: Route; navigate: Navigate; children: ReactNode }) {
  const { me, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [attention, setAttention] = useState<number | null>(null);
  useEffect(() => {
    api.queue().then((q) => setAttention(q.filter((i) => i.status !== "signed_off" && i.open_items > 0).length)).catch(() => setAttention(null));
  }, [route]);

  const nav: { route: Route; label: string; icon: string; active: boolean; badge?: number | null }[] = [
    { route: { name: "queue" }, label: "Applications", icon: "file", active: ["queue", "review", "signoff", "letter"].includes(route.name), badge: attention },
    { route: { name: "audit" }, label: "Audit trail", icon: "shield", active: route.name === "audit" },
    { route: { name: "evaluation" }, label: "Evaluation", icon: "check", active: route.name === "evaluation" },
  ];
  const name = me?.display_name ?? "Officer";
  const initials = name.split(/\s+/).filter((w) => /^[A-Za-z]/.test(w)).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "OF";
  const roleLabel = me?.role === "admin" ? "Administrator" : "Grants officer";
  const go = (r: Route) => { navigate(r); setOpen(false); };

  return (
    <div className="officer-shell">
      <aside className={open ? "workspace-sidebar open" : "workspace-sidebar"} aria-label="Workspace navigation">
        <button className="workspace-brand" onClick={() => go({ name: "queue" })} aria-label="Go to applications">
          <span className="logo-placeholder small">Study NT<br />logo here</span>
          <span>Grant review<br />Officer workspace</span>
        </button>
        <p className="workspace-label">Workspace</p>
        <nav className="workspace-nav">
          {nav.map((item) => (
            <button key={item.label} className={item.active ? "workspace-link active" : "workspace-link"} onClick={() => go(item.route)}
              aria-current={item.active ? "page" : undefined}>
              <Icon name={item.icon} />{item.label}{item.badge ? <span aria-label={`${item.badge} need attention`}>{item.badge}</span> : null}
            </button>
          ))}
        </nav>
        <div className="workspace-sidebar-footer">
          <button className="workspace-link" onClick={logout}><Icon name="left" />Sign out</button>
          <div className="sidebar-officer"><span className="avatar">{initials}</span><span><strong>{name}</strong><small>{roleLabel}</small></span></div>
        </div>
      </aside>
      {open && <button className="sidebar-scrim" onClick={() => setOpen(false)} aria-label="Close navigation" />}
      <div className="officer-main">
        <header className="workspace-topbar">
          <button className="mobile-menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
          <div><span>Officer workspace</span><strong>{TITLES[route.name]}</strong></div>
          <div className="topbar-actions">
            <span className="topbar-divider" />
            <div className="topbar-officer"><span className="avatar">{initials}</span><span><strong>{name}</strong><small>{roleLabel}</small></span></div>
          </div>
        </header>
        {route.name !== "evaluation" && <AIBanner />}
        <div className="workspace-content">{children}</div>
      </div>
    </div>
  );
}

function Login() {
  const { mode, error, loginDemo, loginPassword } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setFailure(null);
    try { await fn(); } catch (e) { setFailure(e); } finally { setBusy(false); }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); void run(() => loginPassword(email, password)); };

  return (
    <main className="page narrow login-page">
      <div className="panel login-panel">
        <span className="logo-placeholder small">Study NT<br />logo here</span>
        <p className="eyebrow">Officer workspace</p>
        <h1>Sign in to review applications</h1>
        <p>AI suggests. You decide. Every finding needs your confirmation.</p>
        <ErrorNotice error={error ?? failure} />
        {mode === "demo" && (
          <>
            <div className="notice"><Icon name="info" />Demo mode: synthetic, fictional data only. Changes are lost when the API restarts.</div>
            <Button icon="user" disabled={busy} onClick={() => void run(() => loginDemo("officer"))}>Sign in as demo officer</Button>
          </>
        )}
        {mode === "supabase" && (
          <form onSubmit={submit} className="login-form">
            <label className="field"><span>Email</span><input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <label className="field"><span>Password</span><input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
            <Button type="submit" disabled={busy}>Sign in</Button>
          </form>
        )}
      </div>
    </main>
  );
}

export default function App() {
  const { me, ready } = useAuth();
  const [route, navigate] = useRoute();
  if (!ready) return <main className="page narrow"><Loading label="Connecting…" /></main>;
  if (!me) return <Login />;
  return (
    <Shell route={route} navigate={navigate}>
      {route.name === "queue" && <Queue navigate={navigate} />}
      {route.name === "review" && <Review id={route.id} navigate={navigate} />}
      {route.name === "signoff" && <Signoff id={route.id} navigate={navigate} />}
      {route.name === "letter" && <LetterScreen id={route.id} navigate={navigate} />}
      {route.name === "audit" && <Audit />}
      {route.name === "evaluation" && <Evaluation />}
    </Shell>
  );
}
