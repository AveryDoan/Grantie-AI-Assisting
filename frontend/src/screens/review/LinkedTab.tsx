// Linked applications: other applications that share something with this one.
// Shared items appear only as short masked references (keyed hashes); another applicant's details are never shown.
// A link is for the officer to check. It is not a finding against any applicant and changes no rule result.
import type { LinkedApplication } from "../../api";

export function LinkedTab({ rows, onOpen }: { rows: LinkedApplication[]; onOpen: (id: string) => void }) {
  return (
    <>
      {rows.length === 0 ? <p className="empty-state">No linked applications found.</p> : (
        <div className="rv-table-wrap" role="region" aria-label="Linked applications" tabIndex={-1}>
          <table className="rv-table">
            <thead><tr><th>Application ID</th><th>What is shared</th><th>Strength</th><th>Open</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.application_id}>
                  <td><strong>{r.reference}</strong></td>
                  <td>
                    <ul className="cx-list rv-shared">
                      {r.shared.map((s, i) => <li key={i}>{s.what} <code className="rv-ref">{s.ref}</code> <small className="rv-muted">{s.strength}</small></li>)}
                    </ul>
                  </td>
                  <td>{r.strength}</td>
                  <td>{r.can_open
                    ? <button className="link-button" onClick={() => onOpen(r.application_id)}>Open {r.reference}</button>
                    : <span className="rv-muted">No access</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
