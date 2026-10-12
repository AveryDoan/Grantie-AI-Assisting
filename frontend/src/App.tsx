import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "./api";
import { useAuth } from "./auth";
import { Button, ErrorNotice, Icon, Loading } from "./ui";
import Queue from "./screens/Queue";
import Dashboard from "./screens/Dashboard";
import Application from "./screens/Application";
import Signoff from "./screens/Signoff";
import LetterScreen from "./screens/Letter";
import Audit from "./screens/Audit";
import Evaluation from "./screens/Evaluation";
import AiTrace from "./screens/AiTrace";
import Lists from "./screens/Lists";
import PublicHome from "./screens/PublicHome";
import ApplicantFlow from "./ApplicantFlow";
import grantieLogo from "./assets/grantie-logo.png";
import studyNtLogo from "./assets/study-nt-logo.svg";

// Hash routes keep deep links working without server configuration:
//   public:  #/  #/apply
//   officer: #/dashboard  #/queue[?q=][&filter=&officer=&pack=]  #/applications/:id  #/applications/:id/trace
//            #/applications/:id/signoff  #/applications/:id/letter  #/audit  #/evaluation
export type Route =
  | { name: "home" }
  | { name: "apply" }
  | { name: "dashboard" }
  | { name: "queue"; q?: string; filter?: string; officer?: "me"; pack?: string }
  | { name: "lists" }
  | { name: "review"; id: string; step?: number; sub?: string }
  | { name: "trace"; id: string; item?: string }
  | { name: "signoff"; id: string }
  | { name: "letter"; id: string }
  | { name: "audit" }
  | { name: "evaluation" };

function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "applications" && parts[1]) {
    if (parts[2] === "signoff" || parts[2] === "letter") return { name: "review", id: parts[1], step: 4 };
    if (parts[2] === "trace") return { name: "trace", id: parts[1], item: new URLSearchParams(query).get("item") ?? undefined };
    const q = new URLSearchParams(query);
    const stepParam = Number(q.get("step"));
    const sub = q.get("sub") ?? undefined;   // step 3 sub-step: eligibility, consistency, ai_use or merit
    return { name: "review", id: parts[1], step: stepParam >= 1 && stepParam <= 4 ? stepParam : undefined, sub: stepParam === 3 ? sub : undefined };
  }
  if (parts[0] === "dashboard") return { name: "dashboard" };
  if (parts[0] === "queue") {
    const q = new URLSearchParams(query);
    return { name: "queue", q: q.get("q") ?? undefined, filter: q.get("filter") ?? undefined, officer: q.get("officer") === "me" ? "me" : undefined,
             pack: q.get("pack") ?? undefined };
  }
  if (parts[0] === "apply") return { name: "apply" };
  if (parts[0] === "lists") return { name: "lists" };
  if (parts[0] === "audit") return { name: "audit" };
  if (parts[0] === "evaluation") return { name: "evaluation" };
  return { name: "home" };
}

export function href(route: Route): string {
  switch (route.name) {
    case "home": return "#/";
    case "apply": return "#/apply";
    case "lists": return "#/lists";
    case "dashboard": return "#/dashboard";
    case "queue": {
      const q = new URLSearchParams({ ...(route.q ? { q: route.q } : {}), ...(route.filter ? { filter: route.filter } : {}),
        ...(route.officer ? { officer: route.officer } : {}), ...(route.pack ? { pack: route.pack } : {}) }).toString();
      return q ? `#/queue?${q}` : "#/queue";
    }
    case "review": return route.step ? `#/applications/${route.id}?step=${route.step}${route.step === 3 && route.sub ? `&sub=${route.sub}` : ""}` : `#/applications/${route.id}`;
    case "trace": return `#/applications/${route.id}/trace${route.item ? `?item=${encodeURIComponent(route.item)}` : ""}`;
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
  home: "", apply: "", dashboard: "Overview", queue: "Applications", lists: "Reference lists", review: "Application review", trace: "Redaction & AI trace", signoff: "Sign-off",
  letter: "Outcome letter", audit: "Audit trail", evaluation: "Evaluation",
};

function Shell({ route, navigate, children }: { route: Route; navigate: Navigate; children: ReactNode }) {
  const { me, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem("sidebar-collapsed") === "1"; } catch { return false; } });
  const [search, setSearch] = useState("");
  const [attention, setAttention] = useState<number | null>(null);
  useEffect(() => {
    api.queue().then((q) => setAttention(q.filter((i) => i.status !== "signed_off" && i.open_items > 0).length)).catch(() => setAttention(null));
  }, [route]);
  // Keyboard: G then D opens the overview; / focuses search (not while typing in a field).
  useEffect(() => {
    let g = 0;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)))) return;
      if (e.key === "/") { e.preventDefault(); document.querySelector<HTMLInputElement>(".global-search input")?.focus(); return; }
      if (e.key === "g" || e.key === "G") { g = Date.now(); return; }
      if ((e.key === "d" || e.key === "D") && Date.now() - g < 1500) { g = 0; navigate({ name: "dashboard" }); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navigate]);
  const toggle = () => setCollapsed((c) => { try { localStorage.setItem("sidebar-collapsed", c ? "0" : "1"); } catch { /* ignore */ } return !c; });

  const nav: { route: Route; label: string; icon: string; active: boolean; badge?: number | null }[] = [
    { route: { name: "dashboard" }, label: "Overview", icon: "grid", active: route.name === "dashboard" },
    { route: { name: "queue" }, label: "Applications", icon: "list", active: ["queue", "review", "signoff", "letter", "trace"].includes(route.name), badge: attention },
    { route: { name: "audit" }, label: "Audit trail", icon: "clock", active: route.name === "audit" },
  ];
  const name = me?.display_name ?? "Officer";
  const initials = name.split(/\s+/).filter((w) => /^[A-Za-z]/.test(w)).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "OF";
  const roleLabel = me?.role === "admin" ? "Administrator" : "Grants officer";
  const go = (r: Route) => { navigate(r); setOpen(false); };

  return (
    <div className={collapsed ? "officer-shell sidebar-collapsed" : "officer-shell"}>
      <aside className={open ? "workspace-sidebar open" : "workspace-sidebar"} aria-label="Workspace navigation">
        <button className="workspace-brand brand-grantie" onClick={() => go({ name: "dashboard" })} aria-label="Grantie – go to the overview">
          <img src={grantieLogo} alt="Grantie – AI solution for grants in NT" />
        </button>
        <nav className="workspace-nav" aria-label="Officer navigation">
          {nav.map((item) => (
            <button key={item.label} title={collapsed ? item.label : undefined} className={item.active ? "workspace-link active" : "workspace-link"} onClick={() => go(item.route)}
              aria-current={item.active ? "page" : undefined}>
              <Icon name={item.icon} /><b>{item.label}</b>{item.badge ? <span aria-label={`${item.badge} need attention`}>{item.badge}</span> : null}
            </button>
          ))}
        </nav>
        <div className="workspace-sidebar-footer">
          <div className="partner-tile"><small>Officer workspace for</small><img src={studyNtLogo} alt="Study NT" /></div>
          <button className="workspace-link" title={collapsed ? "Applicant form" : undefined} onClick={() => go({ name: "home" })}><Icon name="external" /><b>Applicant pages</b></button>
          <button className="workspace-link" title={collapsed ? "Sign out" : undefined} onClick={logout}><Icon name="left" /><b>Sign out</b></button>
          <button className="sidebar-collapse" onClick={toggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}><Icon name="collapse" /><span>Collapse sidebar</span></button>
        </div>
      </aside>
      {open && <button className="sidebar-scrim" onClick={() => setOpen(false)} aria-label="Close navigation" />}
      <div className="officer-main">
        <header className="workspace-topbar">
          <button className="mobile-menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
          <form className="global-search" role="search" onSubmit={(e) => { e.preventDefault(); go({ name: "queue", q: search.trim() || undefined }); }}>
            <Icon name="search" /><input aria-label="Search applications" placeholder="Search applications by reference or name" value={search} onChange={(e) => setSearch(e.target.value)} />
          </form>
          <div className="topbar-actions">
            <div className="topbar-officer"><span className="avatar">{initials}</span><span><strong>{name}</strong><small>{roleLabel}</small></span></div>
          </div>
        </header>
        <div className="sr-only" aria-live="polite">{TITLES[route.name]}</div>
        <div className="workspace-content">{children}</div>
      </div>
    </div>
  );
}

function Login() {
  const { mode, me, error, loginDemo, loginPassword, logout } = useAuth();
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
        <div className="login-logos">
          <img className="login-grantie" src={grantieLogo} alt="Grantie – AI solution for grants in NT" />
          <span className="login-for">Officer workspace for</span>
          <img className="login-studynt" src={studyNtLogo} alt="Study NT" />
        </div>
        <h1>Sign in to review applications</h1>
        <ErrorNotice error={error ?? failure} />
        {me && me.role === "applicant" && (
          <div className="notice warn-notice"><Icon name="info" /><span>You are signed in as an applicant. Sign out to sign in as an officer. <button className="link-button" onClick={logout}>Sign out</button></span></div>
        )}
        {mode === "demo" && (
          <>
            <div className="notice"><Icon name="info" />Demo mode: synthetic, fictional data only. Changes are lost when the API restarts.</div>
            <Button icon="user" disabled={busy} onClick={() => void run(async () => { logout(); await loginDemo("officer"); })}>Sign in as demo officer</Button>
          </>
        )}
        {mode === "supabase" && (
          <form onSubmit={submit} className="login-form">
            <label className="field"><span>Email</span><input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <label className="field"><span>Password</span><input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
            <Button type="submit" disabled={busy}>Sign in</Button>
          </form>
        )}
        <a className="back-link" href="#/">← Applicant pages</a>
      </div>
    </main>
  );
}

export default function App() {
  const { me, ready } = useAuth();
  const [route, navigate] = useRoute();
  if (!ready) return <main className="page narrow"><Loading label="Connecting…" /></main>;
  if (route.name === "home") return <PublicHome onApply={() => navigate({ name: "apply" })} onOfficer={() => navigate({ name: "dashboard" })} />;
  if (route.name === "apply") return <ApplicantFlow onHome={() => navigate({ name: "home" })} />;
  if (!me || (me.role !== "officer" && me.role !== "admin")) return <Login />;
  return (
    <Shell route={route} navigate={navigate}>
      {route.name === "dashboard" && <Dashboard />}
      {route.name === "queue" && <Queue key={href(route)} navigate={navigate} initialSearch={route.q} filter={route.filter} officer={route.officer} pack={route.pack} />}
      {/* A new key per application: the screen never shows one application's content (or buttons) under another's address. */}
      {route.name === "review" && <Application key={route.id} id={route.id} step={route.step} sub={route.sub} navigate={navigate} />}
      {route.name === "trace" && <AiTrace key={`${route.id}-${route.item ?? ""}`} id={route.id} item={route.item} navigate={navigate} />}
      {route.name === "lists" && <Lists />}
      {route.name === "signoff" && <Signoff key={route.id} id={route.id} navigate={navigate} />}
      {route.name === "letter" && <LetterScreen key={route.id} id={route.id} navigate={navigate} />}
      {route.name === "audit" && <Audit />}
      {route.name === "evaluation" && <Evaluation />}
    </Shell>
  );
}
