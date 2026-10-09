// Shared UI pieces, taken from the Figma Make design (Study NT Grant - AI Application).
import { useEffect, useState, type ReactNode } from "react";
import type { AIStatus, Decision, Finding } from "./api";

export const Icon = ({ name, size = 18 }: { name: string; size?: number }) => {
  const paths: Record<string, ReactNode> = {
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m7 7 10 10M17 7 7 17" />,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16h16V8z" /><path d="M14 2v6h6M8 13h8M8 17h6" /></>,
    question: <><path d="M9.5 9a3 3 0 1 1 4.8 2.4c-1.3.9-2.3 1.3-2.3 3.1" /><path d="M12 18h.01" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    left: <path d="m15 18-6-6 6-6" />,
    shield: <><path d="M12 3 4 6v5c0 5 3.4 8.7 8 10 4.6-1.3 8-5 8-10V6z" /><path d="m8.5 12 2.2 2.2 4.8-5" /></>,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4.5 21a7.5 7.5 0 0 1 15 0" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    download: <path d="M12 3v12m-5-5 5 5 5-5M5 21h14" />,
    edit: <><path d="m4 20 4.2-1 10.6-10.6a2 2 0 0 0-2.8-2.8L5.4 16.2z" /><path d="m14.5 7 2.8 2.8" /></>,
    send: <path d="m22 2-7 20-4-9-9-4zM22 2 11 13" />,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
    external: <><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 13v7H4V6h7" /></>,
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
    upload: <><path d="M12 16V4m-5 5 5-5 5 5" /><path d="M5 20h14" /></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" /><path d="M10 21h4" /></>,
    help: <><circle cx="12" cy="12" r="9" /><path d="M9.6 9a2.6 2.6 0 1 1 4.2 2.1c-1.1.8-1.8 1.2-1.8 2.4M12 17h.01" /></>,
    robot: <><rect x="5" y="7" width="14" height="11" rx="3" /><path d="M9 12h.01M15 12h.01M9 15h6M12 7V4M9 4h6M3 11v4M21 11v4" /></>,
    list: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="M4 6h.01M4 12h.01M4 18h.01" /></>,
    mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></>,
    record: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="3" /></>,
    collapse: <><path d="m14 7-5 5 5 5" /><path d="M20 4v16" /></>,
  };
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
};

export function Button({
  children, variant = "primary", icon, disabled, onClick, type = "button", title,
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "quiet" | "danger";
  icon?: string;
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
  title?: string;
}) {
  return (
    <button type={type} className={`button button-${variant}`} disabled={disabled} onClick={onClick} title={title}>
      {icon && <Icon name={icon} />}
      {children}
    </button>
  );
}

const STATUS_ICON: Record<AIStatus, string> = {
  Met: "check", "Not met": "close", "Needs evidence": "file", Unclear: "question", "Evidence only": "user",
};

export function StatusChip({ status }: { status: AIStatus }) {
  if (status === "Evidence only") return <span className="judgement-chip">Officer judgement</span>;
  return (
    <span className={`status status-${status.toLowerCase().replace(" ", "-")}`}>
      <Icon name={STATUS_ICON[status]} size={15} />
      {status}
    </span>
  );
}

export function AppStatus({ status }: { status: string }) {
  return <span className="app-status"><span className="app-status-dot" />{status}</span>;
}

export function AIBanner() {
  return (
    <div className="ai-banner">
      <Icon name="shield" size={19} />
      <strong>AI suggests. You decide.</strong>
      <span>Check every finding against the application.</span>
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return <div className="notice error-notice" role="alert"><Icon name="info" />{message}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="notice blue" role="status"><Icon name="clock" />{label}</div>;
}

/** Simple data hook: load(), plus reload() after a change. */
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setError(null);
    setLoading(true);
    loader()
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  // `loading` stays true while a reload is in flight, so callers can lock
  // actions until the screen shows the result of the previous one.
  return { data, error, loading, reload: () => { setLoading(true); setTick((t) => t + 1); } };
}

// ---------------------------------------------------------------- helpers

export const APP_STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  submitted: "Not started",
  in_review: "In review",
  awaiting_applicant: "Awaiting applicant",
  signed_off: "Signed off",
};

export function formatDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-AU", withTime
    ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
    : { day: "numeric", month: "short", year: "numeric" });
}

export function humanise(key: string): string {
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function isDecided(f: Finding): boolean {
  return !!f.latest_review && f.latest_review.action !== "ask_applicant";
}

/** Status to show: the officer's decision once made, otherwise the AI suggestion. */
export function effectiveStatus(f: Finding): AIStatus {
  return isDecided(f) ? (f.latest_review!.final_status as Decision) : f.ai_status;
}

export const CHECK_SOURCE_LABEL: Record<Finding["check_source"], string> = {
  llm: "AI suggestion",
  code: "Checked by code",
  human_only: "Officer only",
};
