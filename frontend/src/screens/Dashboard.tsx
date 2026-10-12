// Overview: "Where is the round up to, and what should I do next?" A workload and progress view, never a performance view.
// - Every number comes from GET /dashboard, which uses the queue's own rows and filters. Each tile, card and bar opens the queue
//   with the same filter, so the count and the rows always match.
// - No score, rank, comparison or sort by merit. Merit shows how many criteria are marked, never a mark.
// - AI suggestions are counted apart from officer decisions. The AI-use check is a small count, hidden when it is switched off.
// - Nothing is broken down by officer. Bars only (no pie, donut or gauge); every chart has a table view.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api, type Dashboard as Data, type DashQuery } from "../api";
import { STAGE_SEC, TILES, plural, queueHref, reminderText, updatedAgo } from "../dashboard";
import { Button, Icon, formatDate } from "../ui";

type Theme = "light" | "dark";
const readTheme = (): Theme => {
  try {
    const t = localStorage.getItem("overview-theme");
    if (t === "light" || t === "dark") return t;
  } catch { /* storage blocked */ }
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
};

export default function Dashboard() {
  const [query, setQuery] = useState<DashQuery>(() => {
    try { return { officer: localStorage.getItem("overview-officer") === "me" ? "me" : "all" }; } catch { return { officer: "all" }; }
  });
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [theme, setTheme] = useState<Theme>(readTheme);

  const load = useCallback((refresh = false) => {
    setBusy(true);
    setError(null);
    api.dashboard({ ...query, refresh }).then(setData, setError).finally(() => setBusy(false));
  }, [query]);
  useEffect(() => { load(); }, [load]);

  const setOfficer = (officer: "me" | "all") => {
    try { localStorage.setItem("overview-officer", officer); } catch { /* ignore */ }
    setQuery((q) => ({ ...q, officer }));
  };
  const toggleTheme = () => setTheme((t) => {
    const next = t === "dark" ? "light" : "dark";
    try { localStorage.setItem("overview-theme", next); } catch { /* ignore */ }
    return next;
  });

  return (
    <DashboardView data={data} error={error} busy={busy} theme={theme} officer={query.officer ?? "all"} onOfficer={setOfficer}
      onPack={(rule_pack) => setQuery((q) => ({ ...q, rule_pack }))} onRefresh={() => load(true)} onTheme={toggleTheme} />
  );
}

export function DashboardView({ data, error, busy = false, theme = "light", officer = "all", onOfficer, onPack, onRefresh, onTheme }: {
  data: Data | null; error?: unknown; busy?: boolean; theme?: Theme; officer?: "me" | "all";
  onOfficer?: (o: "me" | "all") => void; onPack?: (id: string) => void; onRefresh?: () => void; onTheme?: () => void;
}) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((n) => n + 1), 30000); return () => clearInterval(t); }, []);
  const pack = data?.round?.id;

  return (
    <main className="page db" data-theme={theme} aria-busy={busy}>
      <header className="db-band">
        <div className="db-title">
          <h1>Overview</h1>
          <p>{data?.round ? <>{data.round.program}{data.round.name ? <>, <b>{data.round.name}</b></> : null}</> : data ? "No applications yet" : " "}</p>
        </div>
        <div className="db-controls">
          {data && data.rule_packs.length > 1 && (
            <label className="db-select"><span>Rule pack</span>
              <select value={pack ?? ""} onChange={(e) => onPack?.(e.target.value)}>
                {data.rule_packs.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </label>
          )}
          <div className="db-seg" role="group" aria-label="Whose applications">
            {(["me", "all"] as const).map((o) => (
              <button key={o} aria-pressed={officer === o} className={officer === o ? "on" : ""} onClick={() => onOfficer?.(o)}>{o === "me" ? "Me" : "All officers"}</button>
            ))}
          </div>
          <button className="db-icon-btn" onClick={onTheme} aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} title="Light or dark">
            <Icon name={theme === "dark" ? "record" : "eye"} />
          </button>
          <Button variant="secondary" icon="clock" onClick={onRefresh} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</Button>
        </div>
        <p className="db-updated" aria-live="polite">{data ? updatedAgo(data.generated_at) : ""}</p>
        {data?.round && <Deadline round={data.round} />}
      </header>

      {error && !data ? <ErrorState onRetry={onRefresh} /> : !data ? <Skeleton /> : <Body data={data} officer={officer} pack={pack} />}
      {error && data ? <p className="db-inline-error" role="alert">We couldn't refresh the overview. The numbers below are from the last load. <button className="link-button" onClick={onRefresh}>Try again</button></p> : null}
    </main>
  );
}

function Deadline({ round }: { round: NonNullable<Data["round"]> }) {
  if (!round.closes_at) return null;
  const pct = Math.round((round.elapsed ?? 0) * 100);
  return (
    <div className={`db-deadline ${round.closed ? "closed" : ""}`}>
      <span><Icon name="clock" size={16} /> Closes <b>{formatDate(round.closes_at)}</b></span>
      <span>{round.closed ? <b className="db-closed">Closed {plural(-(round.days_left ?? 0), "day")} ago</b> : <>{plural(round.days_left ?? 0, "day")} left</>}</span>
      {round.decisions_due && <span>Decisions due <b>{formatDate(round.decisions_due)}</b></span>}
      <span className="db-progress" role="progressbar" aria-label="Days of the round elapsed" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <i style={{ width: `${pct}%` }} />
      </span>
    </div>
  );
}

function Body({ data, officer, pack }: { data: Data; officer: "me" | "all"; pack?: string }) {
  const href = (filter: string) => queueHref(filter, officer, pack);
  return (
    <>
      <section aria-label="Summary">
        <div className="db-tiles">
          {TILES.map((t) => (
            <a key={t.key} className={`db-tile ${t.sec}`} href={href(t.key)} aria-label={`${t.label}: ${data.tiles[t.key]}. Open in the queue`}>
              <span className="db-tile-icon"><Icon name={t.icon} /></span>
              <b className="db-tile-n">{data.tiles[t.key]}</b>
              <span className="db-tile-label">{t.label}</span>
              <small>{t.line}</small>
            </a>
          ))}
        </div>
      </section>

      <Pipeline data={data} href={href} />

      <div className="db-grid">
        <Attention data={data} href={href} />
        <div className="db-side">
          <DocumentsCard data={data} href={href} />
          <WaitingCard data={data} href={href} />
          <EligibilityCard data={data} />
          <SignalsCard data={data} href={href} />
          <MeritCard data={data} />
        </div>
      </div>

      <Outcomes data={data} />
      <Trust data={data} />
    </>
  );
}

// ------------------------------------------------------------------ pipeline

function Pipeline({ data, href }: { data: Data; href: (f: string) => string }) {
  const ref = useRef<HTMLDivElement>(null);
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const cards = [...(ref.current?.querySelectorAll<HTMLElement>(".db-stage") ?? [])];
    const i = cards.indexOf(e.currentTarget);
    const next = cards[(i + (e.key === "ArrowRight" ? 1 : -1) + cards.length) % cards.length];
    e.preventDefault();
    next?.focus();
    next?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  return (
    <section className="db-section" aria-labelledby="db-pipeline">
      <h2 id="db-pipeline">Where applications are</h2>
      <p className="sr-only">{data.pipeline.filter((c) => c.enabled).map((c) => `${c.title}: ${c.count}`).join(". ")}</p>
      <div className="db-pipeline" ref={ref}>
        {data.pipeline.map((c, i) => {
          const body = (
            <>
              <span className="db-stage-head"><span className="db-stage-n">{i + 1}</span>{c.title}</span>
              <b className="db-stage-count">{c.enabled ? c.count : "Off"}</b>
              <span className="db-chips">
                {c.enabled ? <>
                  <span className="db-chip"><Icon name="user" size={14} />Needs you {c.needs_you}</span>
                  <span className="db-chip"><Icon name="mail" size={14} />Waiting on applicant {c.waiting_applicant}</span>
                </> : <span className="db-chip">Switched off by the fairness check</span>}
              </span>
              <small className="db-stage-note">{c.never_blocks ? (c.preview && c.enabled ? "Never blocks · demo preview" : "Never blocks") : " "}</small>
            </>
          );
          return c.enabled
            ? <a key={c.stage} className={`db-stage ${STAGE_SEC[c.stage]}`} href={href(`stage:${c.stage}`)} onKeyDown={onKey}
                aria-label={`${c.title}: ${c.count}. Needs you ${c.needs_you}, waiting on applicant ${c.waiting_applicant}${c.never_blocks ? ". Never blocks" : ""}. Open in the queue`}>{body}</a>
            : <div key={c.stage} className="db-stage off" tabIndex={0} onKeyDown={onKey} aria-disabled="true" aria-label={`${c.title}: off`}>{body}</div>;
        })}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ needs attention

function Attention({ data, href }: { data: Data; href: (f: string) => string }) {
  return (
    <section className="db-card db-attention" aria-labelledby="db-attention">
      <div className="db-card-head">
        <h2 id="db-attention">Needs attention</h2>
        <small>Longest wait first. Never ordered by merit.</small>
      </div>
      {data.attention.length === 0 ? (
        <div className="db-empty"><Icon name="check" size={28} /><p>Nothing is waiting for you.{data.next_deadline ? <> Next deadline: {formatDate(data.next_deadline)}.</> : null}</p></div>
      ) : (
        <div className="table-wrap">
          <table className="db-table">
            <thead><tr><th>Reference</th><th>Applicant</th><th>Stage</th><th>What is needed</th><th>Waiting</th><th><span className="sr-only">Open</span></th></tr></thead>
            <tbody>
              {data.attention.map((a) => (
                <tr key={a.application_id}>
                  <td><a className="table-link" href={a.deep_link}>{a.ref}</a></td>
                  <td>{a.applicant_name ?? "—"}</td>
                  <td><span className={`db-stage-chip ${STAGE_SEC[a.stage]}`}>{a.stage_title}</span></td>
                  <td>{a.needed}</td>
                  <td>{a.waiting_days == null ? "—" : plural(a.waiting_days, "day")}</td>
                  <td><a className="button button-secondary db-open" href={a.deep_link} aria-label={`Open ${a.ref}`}>Open</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.attention_total > data.attention.length && <a className="db-more" href={href("need_action")}>View all {data.attention_total} in the queue <Icon name="arrow" size={16} /></a>}
    </section>
  );
}

// ------------------------------------------------------------------ charts

function Chart({ title, summary, table, children }: { title: string; summary: string; table: ReactNode; children: ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <div className="db-chart">
      <div className="db-chart-head">
        <span className="sr-only">{title}. {summary}</span>
        <button className="link-button" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>{asTable ? "View as chart" : "View as table"}</button>
      </div>
      {asTable ? table : children}
    </div>
  );
}

function Bars({ rows, max }: { rows: { key: string; label: string; value: number; href?: string; sub?: string }[]; max?: number }) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="db-bars">
      {rows.map((r) => {
        const inner = (
          <>
            <span className="db-bar-label">{r.label}{r.sub ? <small> · {r.sub}</small> : null}</span>
            <span className="db-bar-track"><i style={{ width: `${(r.value / top) * 100}%` }} /></span>
            <b className="db-bar-value">{r.value}</b>
          </>
        );
        return <li key={r.key}>{r.href ? <a className="db-bar" href={r.href} aria-label={`${r.label}${r.sub ? `, ${r.sub}` : ""}: ${r.value}. Open in the queue`}>{inner}</a> : <span className="db-bar">{inner}</span>}</li>;
      })}
    </ul>
  );
}

// ------------------------------------------------------------------ right column

function DocumentsCard({ data, href }: { data: Data; href: (f: string) => string }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [drafting, setDrafting] = useState(false);
  const rows = data.documents_missing;
  const key = (d: Data["documents_missing"][number]) => `docs:${d.slot}:${d.reason}`;
  const apps = new Map<string, string>();
  rows.filter((d) => selected.has(key(d))).forEach((d) => d.applications.forEach((a) => apps.set(a.application_id, a.ref)));
  return (
    <section className="db-card" aria-labelledby="db-docs">
      <div className="db-card-head"><h2 id="db-docs"><Icon name="file" /> Documents: what is missing</h2></div>
      {rows.length === 0 ? <p className="db-muted">No documents to ask about.</p> : (
        <>
          <Chart title="Documents to ask about, by kind" summary={rows.map((d) => `${d.type}, ${d.reason_label}: ${d.count}`).join(". ")}
            table={<table className="db-table compact"><thead><tr><th>Document</th><th>Reason</th><th>Applications</th></tr></thead>
              <tbody>{rows.map((d) => <tr key={key(d)}><td>{d.type}</td><td>{d.reason_label}</td><td><a href={href(key(d))}>{d.count}</a></td></tr>)}</tbody></table>}>
            <Bars rows={rows.map((d) => ({ key: key(d), label: d.type, sub: d.reason_label, value: d.count, href: href(key(d)) }))} />
          </Chart>
          <fieldset className="db-checks">
            <legend className="sr-only">Choose which to draft requests for</legend>
            {rows.map((d) => (
              <label key={key(d)}><input type="checkbox" checked={selected.has(key(d))}
                onChange={(e) => setSelected((s) => { const n = new Set(s); if (e.target.checked) n.add(key(d)); else n.delete(key(d)); return n; })} />
                {d.type}, {d.reason_label.toLowerCase()} ({d.count})</label>
            ))}
          </fieldset>
          <Button variant="secondary" icon="mail" disabled={apps.size === 0} onClick={() => setDrafting(true)}>Draft requests for all selected</Button>
        </>
      )}
      {drafting && (
        <Dialog title="Draft requests" onClose={() => setDrafting(false)}>
          <p>Open each application to draft its request in Step 1. You read and approve every request yourself. Nothing is sent from here.</p>
          <ul className="db-draft-list">
            {[...apps].map(([id, ref]) => <li key={id}><span>{ref}</span><a className="button button-secondary" href={`#/applications/${id}?step=1`}>Open draft</a></li>)}
          </ul>
        </Dialog>
      )}
    </section>
  );
}

function WaitingCard({ data, href }: { data: Data; href: (f: string) => string }) {
  const [draft, setDraft] = useState<{ ref: string; text: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const w = data.waiting;
  return (
    <section className="db-card" aria-labelledby="db-waiting">
      <div className="db-card-head"><h2 id="db-waiting"><Icon name="mail" /> Waiting on applicant</h2><a href={href("waiting_applicant")}>{w.count}</a></div>
      {w.count === 0 ? <p className="db-muted">No requests are out.</p> : (
        <>
          <p className="db-muted">Longest wait: {w.oldest_days == null ? "—" : plural(w.oldest_days, "day")}</p>
          <ul className="db-wait-list">
            {w.longest.map((x) => (
              <li key={x.application_id}>
                <a href={x.deep_link}>{x.ref}</a><span>{x.days == null ? "—" : plural(x.days, "day")}</span>
                <button className="link-button" onClick={() => { setCopied(false); setDraft({ ref: x.ref, text: reminderText(x.ref, x.days) }); }}>Draft reminder</button>
              </li>
            ))}
          </ul>
        </>
      )}
      {draft && (
        <Dialog title={`Reminder for ${draft.ref}`} onClose={() => setDraft(null)}>
          <p className="db-muted">This draft is not sent from the app. Copy it into your email if you want to use it.</p>
          <textarea className="db-draft" value={draft.text} onChange={(e) => setDraft({ ...draft, text: e.target.value })} rows={9} aria-label="Reminder text" />
          <Button variant="secondary" icon="file" onClick={() => { void navigator.clipboard?.writeText(draft.text).then(() => setCopied(true)); }}>{copied ? "Copied" : "Copy"}</Button>
        </Dialog>
      )}
    </section>
  );
}

const STATUS_ROWS: { key: keyof Data["findings"]["by_status"]; label: string; icon: string; cls: string }[] = [
  { key: "met", label: "Met", icon: "check", cls: "st-met" },
  { key: "not_met", label: "Not met", icon: "close", cls: "st-notmet" },
  { key: "needs_evidence", label: "Needs evidence", icon: "file", cls: "st-needs" },
  { key: "unclear", label: "Unclear", icon: "question", cls: "st-unclear" },
  { key: "officer_judgement", label: "Officer judgement", icon: "user", cls: "st-judgement" },
];

function EligibilityCard({ data }: { data: Data }) {
  const f = data.findings;
  const total = STATUS_ROWS.reduce((n, s) => n + f.by_status[s.key], 0);
  return (
    <section className="db-card" aria-labelledby="db-elig">
      <div className="db-card-head"><h2 id="db-elig"><Icon name="shield" /> Eligibility at a glance</h2></div>
      <div className="db-pair">
        <div className="db-box">
          <h3>Confirmed by officers</h3>
          <p><b>{f.confirmed.not_met_apps}</b> {f.confirmed.not_met_apps === 1 ? "application" : "applications"} with a rule confirmed Not met</p>
          <p><b>{f.confirmed.all_met_apps}</b> with every rule confirmed Met</p>
        </div>
        <div className="db-box suggested">
          <h3>Suggested, not yet decided</h3>
          <p><b>{f.suggested_pending}</b> AI-suggested {f.suggested_pending === 1 ? "finding waits" : "findings wait"} for an officer decision</p>
        </div>
      </div>
      <h3 className="db-sub">Findings across all applications</h3>
      <small className="db-muted">Officer decisions, plus findings that wait for the officer's own judgement. Suggestions are not counted here.</small>
      <Chart title="Findings across all applications" summary={STATUS_ROWS.map((s) => `${s.label}: ${f.by_status[s.key]}`).join(". ")}
        table={<table className="db-table compact"><thead><tr><th>Status</th><th>Findings</th></tr></thead>
          <tbody>{STATUS_ROWS.map((s) => <tr key={s.key}><td>{s.label}</td><td>{f.by_status[s.key]}</td></tr>)}</tbody></table>}>
        <div className="db-stack" aria-hidden="true">{total > 0 && STATUS_ROWS.filter((s) => f.by_status[s.key] > 0).map((s) => (
          <i key={s.key} className={s.cls} style={{ flexGrow: f.by_status[s.key] }} />
        ))}</div>
      </Chart>
      <ul className="db-legend">
        {STATUS_ROWS.map((s) => <li key={s.key} className={s.cls}><Icon name={s.icon} size={14} />{s.label} <b>{f.by_status[s.key]}</b></li>)}
      </ul>
    </section>
  );
}

function SignalsCard({ data, href }: { data: Data; href: (f: string) => string }) {
  const s = data.signals;
  return (
    <section className="db-card" aria-labelledby="db-signals">
      <div className="db-card-head"><h2 id="db-signals"><Icon name="info" /> Signals waiting for you</h2></div>
      <ul className="db-counts">
        <li><a href={href("signal:consistency")}><span>Differences to check (Consistency)</span><b>{s.consistency_pending}</b></a></li>
        {s.ai_use_enabled && s.ai_use_pending != null && (
          <li><a href={href("signal:ai_use")}><span>AI-use signals{s.ai_use_preview ? " (demo preview)" : ""}</span><b>{s.ai_use_pending}</b></a></li>
        )}
      </ul>
      <small className="db-muted">Counts of applications. Signals never block a step and never change a result.</small>
    </section>
  );
}

function MeritCard({ data }: { data: Data }) {
  return (
    <section className="db-card" aria-labelledby="db-merit">
      <div className="db-card-head"><h2 id="db-merit"><Icon name="edit" /> Merit progress</h2></div>
      {data.merit_progress.length === 0 ? <p className="db-muted">This rule pack has no merit criteria.</p> : (
        <Chart title="Merit criteria marked" summary={data.merit_progress.map((m) => `${m.criterion}: marked ${m.marked} of ${m.total}`).join(". ")}
          table={<table className="db-table compact"><thead><tr><th>Criterion</th><th>Marked</th><th>Applications</th></tr></thead>
            <tbody>{data.merit_progress.map((m) => <tr key={m.criterion}><td>{m.criterion} {m.title}</td><td>{m.marked}</td><td>{m.total}</td></tr>)}</tbody></table>}>
          <ul className="db-merit">
            {data.merit_progress.map((m) => (
              <li key={m.criterion}>
                <span className="db-bar-label"><b>{m.criterion}</b> {m.title}</span>
                <span className="db-bar-track merit"><i style={{ width: `${m.total ? (m.marked / m.total) * 100 : 0}%` }} /></span>
                <small>Marked {m.marked} of {m.total} {m.total === 1 ? "application" : "applications"}</small>
              </li>
            ))}
          </ul>
        </Chart>
      )}
      <small className="db-muted">Marks are entered by officers. Nothing is added up or compared.</small>
    </section>
  );
}

// ------------------------------------------------------------------ outcomes and trust

function Outcomes({ data }: { data: Data }) {
  const o = data.outcomes;
  const items = [
    { label: "Decline letters drafted", sub: "Not released", n: o.declines_drafted },
    { label: "Next-steps letters drafted", sub: "Not released", n: o.next_steps_drafted },
    { label: "Requests for more information drafted", sub: "Not sent", n: o.requests_drafted },
    { label: "Letters released after sign-off", sub: "", n: o.released },
  ];
  return (
    <section className="db-card db-outcomes" aria-labelledby="db-outcomes">
      <div className="db-card-head"><h2 id="db-outcomes"><Icon name="mail" /> Outcomes and letters</h2></div>
      <div className="db-outcome-row">
        {items.map((i) => <div key={i.label} className="db-stat"><b>{i.n}</b><span>{i.label}</span>{i.sub && <small>{i.sub}</small>}</div>)}
        <div className="db-stat"><b>{o.avg_days_to_signoff == null ? "—" : o.avg_days_to_signoff}</b><span>Days from received to signed off, on average</span><small>How long the process takes. Not a measure of any applicant or officer.</small></div>
      </div>
      {o.signed_off_per_day.length > 0 && (
        <Chart title="Signed off per day" summary={o.signed_off_per_day.map((d) => `${formatDate(d.date)}: ${d.count}`).join(". ")}
          table={<table className="db-table compact"><thead><tr><th>Day</th><th>Signed off</th></tr></thead>
            <tbody>{o.signed_off_per_day.map((d) => <tr key={d.date}><td>{formatDate(d.date)}</td><td>{d.count}</td></tr>)}</tbody></table>}>
          <Bars rows={o.signed_off_per_day.map((d) => ({ key: d.date, label: formatDate(d.date), value: d.count }))} />
        </Chart>
      )}
    </section>
  );
}

function Trust({ data }: { data: Data }) {
  const t = data.trust;
  return (
    <details className="db-card db-trust">
      <summary><Icon name="robot" /> Show how the AI is doing <small>Counts only, by rule, never by person</small></summary>
      <div className="db-trust-grid">
        <div>
          <h3>Officer overrides by rule</h3>
          {t.overrides_by_rule.length === 0 ? <p className="db-muted">No overrides yet.</p> : (
            <Chart title="Officer overrides by rule" summary={t.overrides_by_rule.map((o) => `${o.rule}: ${o.count}`).join(". ")}
              table={<table className="db-table compact"><thead><tr><th>Rule</th><th>Overrides</th></tr></thead>
                <tbody>{t.overrides_by_rule.map((o) => <tr key={o.rule}><td>{o.rule}</td><td>{o.count}</td></tr>)}</tbody></table>}>
              <Bars rows={t.overrides_by_rule.map((o) => ({ key: o.rule, label: o.rule, value: o.count }))} />
            </Chart>
          )}
          <small className="db-muted">A high number may mean the rule or prompt needs fixing.</small>
        </div>
        <ul className="db-counts">
          <li><span>Quotes found in the application</span><b>{t.quotes_verified}</b></li>
          <li><span>Quotes not found (not shown to officers as a match)</span><b>{t.quotes_not_found}</b></li>
          <li><span>AI failures or timeouts (shown as Unclear)</span><b>{t.ai_failures}</b></li>
          <li><span>Applications with redaction approved</span><b>{t.redaction_approved}</b></li>
          <li><span>Leak scans that stopped the AI</span><b>{t.leak_stops}</b></li>
          <li><span>Findings marked "may need clarification"</span><b>{t.language_flags}</b></li>
        </ul>
      </div>
      <small className="db-muted">Language flags are never Not met. AI provider: {t.provider.offline ? "offline stub (no external calls)" : t.provider.name}
        {t.provider.demo && !t.provider.offline ? ". Demo mode uses a free tier, which may be rate limited; repeat reads come from the cache." : "."}</small>
      <p><a href="#/audit">Open audit log <Icon name="arrow" size={14} /></a></p>
    </details>
  );
}

// ------------------------------------------------------------------ states and dialog

function Skeleton() {
  return (
    <div className="db-skeleton" aria-label="Loading the overview">
      <div className="db-tiles">{TILES.map((t) => <span key={t.key} className="db-tile sk" />)}</div>
      <div className="db-pipeline">{Array.from({ length: 8 }, (_, i) => <span key={i} className="db-stage sk" />)}</div>
      <div className="db-grid"><span className="db-card sk tall" /><span className="db-card sk tall" /></div>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return (
    <div className="db-card db-error" role="alert">
      <Icon name="info" size={28} />
      <p>We couldn't load the overview. Try again.</p>
      <Button variant="secondary" onClick={onRetry}>Try again</Button>
    </div>
  );
}

function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>("button, a, textarea")?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && box.current) {
        const f = [...box.current.querySelectorAll<HTMLElement>("button, a[href], textarea, input")];
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); back?.focus(); };
  }, [onClose]);
  return createPortal(
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="db-dialog" role="dialog" aria-modal="true" aria-labelledby="db-dialog-title" ref={box}>
        <div className="db-dialog-head"><h2 id="db-dialog-title">{title}</h2><button className="db-icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" /></button></div>
        {children}
      </div>
    </div>,
    document.body,
  );
}
