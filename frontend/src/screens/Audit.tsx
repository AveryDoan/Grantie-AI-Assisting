import { useMemo, useState } from "react";
import { api, type AIStatus, type AuditRow } from "../api";
import { Button, ErrorNotice, Icon, Loading, StatusChip, formatDate, humanise, useLoad } from "../ui";

const STATUSES = new Set(["Met", "Not met", "Needs evidence", "Unclear", "Evidence only"]);

function Cell({ value }: { value: string | null }) {
  if (!value) return <>—</>;
  if (STATUSES.has(value)) return <StatusChip status={value as AIStatus} />;
  return <>{humanise(value)}</>;
}

export default function Audit() {
  const { data, error } = useLoad(() => api.audit({ limit: "2000" }), []);
  const [search, setSearch] = useState("");
  const [officer, setOfficer] = useState("all");
  const [decisions, setDecisions] = useState("all");
  const [since, setSince] = useState("");
  const [failure, setFailure] = useState<unknown>(null);

  const officers = useMemo(() => [...new Set((data ?? []).map((r) => r.actor_name ?? "System"))].sort(), [data]);
  const rows = useMemo(() => (data ?? []).filter((r: AuditRow) => {
    const q = search.trim().toLowerCase();
    const text = [r.application_reference, r.rule_code, r.rule_text, r.reason, r.action, r.actor_name].join(" ").toLowerCase();
    return (!q || text.includes(q))
      && (officer === "all" || (r.actor_name ?? "System") === officer)
      && (decisions === "all" || (decisions === "overridden" ? r.overridden : decisions === "reviews" ? r.action === "finding.review" : true))
      && (!since || r.occurred_at >= since);
  }), [data, search, officer, decisions, since]);

  const exportCsv = async () => {
    setFailure(null);
    try {
      const csv = await api.auditCsv({ limit: "5000", ...(since ? { since } : {}) });
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "audit_log.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setFailure(e);
    }
  };

  return (
    <main className="page wide-page">
      <div className="page-heading">
        <div><p className="eyebrow">Governance</p><h1>Audit trail</h1><p>A complete, append-only record of AI suggestions and officer decisions. Records cannot be edited or deleted.</p></div>
        <Button variant="secondary" icon="download" onClick={() => void exportCsv()}>Export CSV</Button>
      </div>
      <ErrorNotice error={error ?? failure} />
      <section className="panel">
        <div className="toolbar">
          <label className="search"><Icon name="search" /><input placeholder="Search audit records" aria-label="Search audit records" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <div className="filter-row">
            <select aria-label="Filter by officer" value={officer} onChange={(e) => setOfficer(e.target.value)}>
              <option value="all">All officers</option>
              {officers.map((o) => <option key={o}>{o}</option>)}
            </select>
            <select aria-label="Filter by decision" value={decisions} onChange={(e) => setDecisions(e.target.value)}>
              <option value="all">All actions</option>
              <option value="reviews">Rule decisions only</option>
              <option value="overridden">Overridden only</option>
            </select>
            <input type="date" aria-label="From date" value={since} onChange={(e) => setSince(e.target.value)} />
          </div>
        </div>
        {!data && !error && <Loading />}
        {data && (
          <div className="table-wrap">
            <table className="audit-table">
              <thead><tr>{["Time", "Officer", "Application", "Action / rule", "AI suggestion", "Officer decision", "Overridden", "Reason", "Rule pack"].map((x) => <th key={x}>{x}</th>)}</tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{formatDate(r.occurred_at, true)}</td>
                    <td>{r.actor_name ?? "System"}</td>
                    <td>{r.application_reference ?? "—"}</td>
                    <td>{r.rule_code ? <><strong>{r.rule_code}</strong> {r.rule_text}</> : humanise(r.action.replace(".", " "))}</td>
                    <td><Cell value={r.ai_suggestion} /></td>
                    <td><Cell value={r.officer_decision} /></td>
                    <td><strong className={r.overridden ? "yes" : ""}>{r.overridden ? "Yes" : "No"}</strong></td>
                    <td>{r.reason ?? "—"}</td>
                    <td>{r.rule_pack_version ?? "—"}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={9} className="empty-state">No audit records match.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <div className="table-footer">Showing {rows.length} of {data?.length ?? 0} records</div>
      </section>
    </main>
  );
}
