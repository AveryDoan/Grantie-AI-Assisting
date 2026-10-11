// Work list: "Which application do I open next?" Tiles filter the table. Oldest first, never ranked by applicant.
// No score, rank or comparison column. "Open items" is a count of things still waiting for the officer.
import { useMemo, useState } from "react";
import { api, type QueueItem } from "../api";
import type { Navigate } from "../App";
import { STAGES, stageCounts, stageOf, type Stage } from "../stages";
import { APP_STATUS_LABEL, AppStatus, Button, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../ui";

function statusLabel(item: QueueItem): string {
  if (item.status !== "signed_off" && item.attention.manual_assessment_requested) return "Manual assessment";
  if (item.status === "submitted" && !item.attention.not_yet_assessed) return "In review";
  return APP_STATUS_LABEL[item.status] ?? item.status;
}
const daysSince = (iso: string) => Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86400000));

export default function Queue({ navigate, initialSearch = "" }: { navigate: Navigate; initialSearch?: string }) {
  const { data, error } = useLoad(api.queue, []);
  const [search, setSearch] = useState(initialSearch);
  const [stage, setStage] = useState<Stage | "all">("all");
  const filtered = search !== "" || stage !== "all";

  const all = data ?? [];
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return all.filter((i) => (stage === "all" || stageOf(i) === stage) &&
      (!q || i.reference.toLowerCase().includes(q) || (i.applicant_name ?? "").toLowerCase().includes(q)));
  }, [all, search, stage]);
  const counts = stageCounts(all);
  const stageLabel = stage === "all" ? "All applications" : stage === "waiting" ? "Waiting for applicant" : STAGES.find((x) => x.id === stage)!.label;
  const open = (id: string, step?: number) => navigate({ name: "review", id, step });

  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <h1>Applications</h1>
        </div>
        <div className="date-panel"><Icon name="clock" /><span><small>Today</small><strong>{formatDate(new Date().toISOString())}</strong></span></div>
      </div>
      <nav className="track" aria-label="Applications by stage">
        {[{ id: "all" as const, label: "All", n: all.length }, ...STAGES.map((st, i) => ({ id: st.id, label: `${i + 1}. ${st.label}`, n: counts[st.id] })),
          { id: "waiting" as const, label: "Waiting", n: counts.waiting }].map((t) => (
          <button key={t.id} className={`track-seg ${stage === t.id ? "on" : ""}`} aria-pressed={stage === t.id} title={t.id === "waiting" ? "Waiting for applicant" : undefined} onClick={() => setStage(stage === t.id ? "all" : t.id)}>
            <span>{t.label}</span><b>{data ? t.n : "–"}</b>
          </button>
        ))}
      </nav>
      <ErrorNotice error={error} />
      <section className="panel">
        <div className="toolbar">
          <h2 className="toolbar-title">{stageLabel} <span>{rows.length}</span></h2>
          <label className="search"><Icon name="search" /><input aria-label="Search applications" placeholder="Search ID or applicant" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <Button variant="quiet" disabled={!filtered} onClick={() => { setSearch(""); setStage("all"); }}>Reset filters</Button>
        </div>
        {!data && !error && <Loading />}
        {data && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Application ID</th><th>Applicant</th><th>Date received</th><th>Current step</th><th>Status</th><th>Open items</th><th>Next action</th></tr></thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} onClick={() => open(row.id, row.current_step)} className="clickable">
                    <td><button className="table-link" onClick={(e) => { e.stopPropagation(); open(row.id, row.current_step); }}>{row.reference}</button></td>
                    <td><strong>{row.applicant_name ?? "—"}</strong><br /><small className="check-source">{row.program_name}</small></td>
                    <td>{formatDate(row.submitted_at)}</td>
                    <td><span className={`step-chip ${["", "sec-docs", "sec-redaction", "sec-elig", "sec-outcome"][row.current_step]}`}><i />{row.current_step}. {row.current_step_title}</span></td>
                    <td><AppStatus status={statusLabel(row)} />
                      {row.phase === "waiting" && row.waiting_since && <small className="check-source"><br />Waiting {daysSince(row.waiting_since)} {daysSince(row.waiting_since) === 1 ? "day" : "days"}</small>}</td>
                    <td><span className="pill">{row.attention.not_yet_assessed && row.status !== "signed_off" ? "—" : row.open_items}</span></td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <Button variant={row.phase === "mine" || row.phase === "outcome" ? "secondary" : "quiet"} onClick={() => open(row.id, row.current_step)} title={row.next_action}>{row.phase === "done" ? "View" : row.phase === "waiting" ? "Open" : "Continue"}</Button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={7} className="empty-state">{all.length === 0 ? "No applications have been submitted yet." : "No applications match. Try Reset filters."}</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
