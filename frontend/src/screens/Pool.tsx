// Linked applications: groups of applications that share a contact, a referee or document wording.
// Each group names the shared ATTRIBUTE ("a referee's phone number"), never its value. Sharing is only ever a reason
// to look: families, schools and organisations legitimately share addresses, email domains and letter templates.
import type { Navigate } from "../App";
import { api } from "../api";
import { ErrorNotice, Icon, Loading, useLoad } from "../ui";

export default function Pool({ navigate }: { navigate: Navigate }) {
  const { data, error } = useLoad(api.linkedGroups, []);
  return (
    <main className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Across applications · signals, not findings</p>
          <h1>Linked applications</h1>
          <p>Applications from different applicants that share something. Open each one and decide whether it needs follow-up.</p>
        </div>
      </div>
      <div className="notice"><Icon name="info" />
        Applications are linked by comparing keyed hashes of contacts and referees, and the wording of documents. The shared values are never stored or shown.
        Sharing a school email domain or an official letter template is normal, so a weak signal alone proves nothing.
      </div>
      <ErrorNotice error={error} />
      {!data && !error && <Loading />}
      {data && data.length === 0 && <section className="panel"><p className="empty-state">No applications are linked yet. Links appear as applications are checked.</p></section>}
      {data?.map((g) => (
        <section className="panel link-group" key={g.id}>
          <div className="panel-heading">
            <div><p className="eyebrow">Group of {g.applications.length}</p><h2>{g.attributes.map((a) => a.label).slice(0, 2).join(" · ")}{g.attributes.length > 2 ? ` · +${g.attributes.length - 2} more` : ""}</h2></div>
            <span className="cx-summary"><strong>{g.open_flags}</strong><span>{g.open_flags === 1 ? "flag" : "flags"} to check</span></span>
          </div>
          <h3>What they share</h3>
          <ul className="cx-list">
            {g.attributes.map((a) => (
              <li key={a.check_id}>{a.label} <span className={`cx-strength ${a.strength}`}>{a.strength === "weak" ? "Weak signal" : "To check"}</span></li>
            ))}
          </ul>
          <h3>Applications</h3>
          <div className="link-members">
            {g.applications.map((m) => (
              <button key={m.id} className="link-member" onClick={() => navigate({ name: "review", id: m.id })}>
                <strong>{m.reference}</strong><span>{m.applicant_name ?? "Applicant"}</span><Icon name="arrow" size={15} />
              </button>
            ))}
          </div>
        </section>
      ))}
    </main>
  );
}
