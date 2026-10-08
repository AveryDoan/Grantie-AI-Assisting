import { useMemo, useState } from "react";
import { api, type QueueItem } from "../api";
import type { Navigate } from "../App";
import { APP_STATUS_LABEL, AppStatus, ErrorNotice, Icon, Loading, formatDate, useLoad } from "../ui";

function statusLabel(item: QueueItem): string {
  if (item.status !== "signed_off" && item.attention.manual_assessment_requested) return "Manual assessment";
  if (item.status === "submitted" && !item.attention.not_yet_assessed) return "In review";
  return APP_STATUS_LABEL[item.status] ?? item.status;
}

/** Rules still waiting for an officer decision (the officer's workload, not a score). */
function rulesNeedingAttention(item: QueueItem): string {
  if (item.status === "signed_off") return "0";
  if (item.attention.not_yet_assessed) return "—";
  return String(item.attention.unreviewed_findings);
}

export default function Queue({ navigate }: { navigate: Navigate }) {
  const { data, error } = useLoad(api.queue, []);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [sort, setSort] = useState<"attention" | "newest">("attention");

  const rows = useMemo(() => {
    let items = (data ?? []).filter((i) => {
      const q = search.trim().toLowerCase();
      const matches = !q || i.reference.toLowerCase().includes(q) || (i.applicant_name ?? "").toLowerCase().includes(q);
      return matches && (status === "all" || statusLabel(i) === status);
    });
    if (sort === "newest") items = [...items].sort((a, b) => (b.submitted_at ?? "").localeCompare(a.submitted_at ?? ""));
    return items; // default: server order = most open work first
  }, [data, search, status, sort]);

  const all = data ?? [];
  const metrics = [
    { n: all.filter((i) => i.status !== "signed_off" && i.open_items > 0).length, label: "Need attention", icon: "file", tone: "amber" },
    { n: all.filter((i) => statusLabel(i) === "In review").length, label: "In review", icon: "clock", tone: "blue" },
    { n: all.filter((i) => i.status === "awaiting_applicant").length, label: "Awaiting applicant", icon: "user", tone: "purple" },
    { n: all.filter((i) => i.status === "signed_off").length, label: "Signed off", icon: "check", tone: "green" },
  ];
  const statuses = ["Not started", "In review", "Awaiting applicant", "Manual assessment", "Signed off"];

  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Grant assessment</p>
          <h1>Applications to review</h1>
          <p>Review AI suggestions and make a decision on each rule.</p>
        </div>
        <div className="date-panel"><Icon name="clock" /><span><small>Today</small><strong>{formatDate(new Date().toISOString())}</strong></span></div>
      </div>
      <section className="metrics">
        {metrics.map((m) => (
          <article key={m.label}><span className={`metric-icon ${m.tone}`}><Icon name={m.icon} /></span><div><strong>{data ? m.n : "–"}</strong><span>{m.label}</span></div></article>
        ))}
      </section>
      <ErrorNotice error={error} />
      <section className="panel">
        <div className="toolbar">
          <label className="search"><Icon name="search" /><input aria-label="Search applications" placeholder="Search ID or applicant" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <div className="filter-row">
            <label><span className="sr-only">Filter by status</span>
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="all">All statuses</option>
                {statuses.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <label><span className="sr-only">Sort applications</span>
              <select value={sort} onChange={(e) => setSort(e.target.value as "attention" | "newest")}>
                <option value="attention">Most attention first</option>
                <option value="newest">Newest first</option>
              </select>
            </label>
          </div>
        </div>
        {!data && !error && <Loading />}
        {data && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Application ID</th><th>Applicant</th><th>Date received</th><th>Rules needing attention</th><th>Status</th><th><span className="sr-only">Open</span></th></tr></thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} onClick={() => navigate({ name: "review", id: row.id })} className="clickable">
                    <td><button className="table-link" onClick={(e) => { e.stopPropagation(); navigate({ name: "review", id: row.id }); }}>{row.reference}</button></td>
                    <td><strong>{row.applicant_name ?? "—"}</strong><br /><small className="check-source">{row.program_name}</small></td>
                    <td>{formatDate(row.submitted_at)}</td>
                    <td><span className={rulesNeedingAttention(row) === "0" ? "attention zero" : "attention"}>{rulesNeedingAttention(row)}</span></td>
                    <td><AppStatus status={statusLabel(row)} /></td>
                    <td><Icon name="chevron" /></td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={6} className="empty-state">No applications match.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <div className="table-footer">Showing {rows.length} of {all.length} applications. Ordered by open work items, not by applicant.</div>
      </section>
    </main>
  );
}
