import { api, type AIStatus } from "../api";
import { ErrorNotice, Icon, Loading, StatusChip, formatDate, humanise, useLoad } from "../ui";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 1000) / 10}%`);
const STATUSES = new Set(["Met", "Not met", "Needs evidence", "Unclear", "Evidence only"]);

export default function Evaluation() {
  const { data, error } = useLoad(api.evaluationLatest, []);

  if (error) return <main className="page wide-page"><ErrorNotice error={error} /></main>;
  if (!data) return <main className="page wide-page"><Loading /></main>;
  const run = data.evaluation_run;
  if (!run) {
    return (
      <main className="page wide-page">
        <div className="page-heading"><div><p className="eyebrow">Assurance</p><h1>Evaluation dashboard</h1></div></div>
        <section className="panel empty-state">No evaluation has been run yet. Run <code>python -m eval.run</code>.</section>
      </main>
    );
  }

  const s = run.summary;
  const provider = s.provider ?? run.model_name;
  const isStub = /stub/i.test(provider) || /stub/i.test(run.model_name);
  const injection = s.injection_tests ?? [];
  const injectionPass = injection.length > 0 && injection.every((t) => t.flagged && !t.obeyed);
  const metrics: [string, string, string, string, number | null][] = [
    [pct(s.accuracy), "Accuracy against answer key", "check", `${s.rule_checks} rule checks across ${s.cases} cases`, s.accuracy],
    [pct(s.quote_validity_rate), "Quoted passages verified", "check", `${s.quotes_checked} quoted findings checked by code`, s.quote_validity_rate],
    [pct(s.twin_consistency_rate), "Language-fairness (twins)", "shield", "Same facts, three writing styles", s.twin_consistency_rate],
    [injection.length ? (injectionPass ? "Pass" : "Fail") : "n/a", "Injection test", "shield",
      injection.length ? `${injection.length} case(s): flagged and not obeyed` : "No injection cases", injection.length ? (injectionPass ? 1 : 0) : null],
  ];

  // Twin groups: one card per family showing each style's suggestions.
  const families = new Map<string, NonNullable<typeof s.twin_groups>>();
  for (const g of s.twin_groups ?? []) families.set(g.family_id, [...(families.get(g.family_id) ?? []), g]);

  return (
    <main className="page wide-page">
      <div className="page-heading">
        <div><p className="eyebrow">Assurance</p><h1>Evaluation dashboard</h1><p>How often the assistant’s suggestions match a human answer key on synthetic test cases.</p></div>
        <span className="version-chip">{formatDate(run.started_at)} · {provider} · prompt {run.prompt_version}</span>
      </div>
      {isStub && (
        <div className="notice warn-notice" role="alert"><Icon name="info" />
          <span><strong>These figures come from the offline keyword stub, not a language model.</strong> They show the evaluation works end to end and that the plain-code checks match the answer key. They say nothing about real AI accuracy. Run <code>python -m eval.run</code> with a Gemini key for real figures.</span>
        </div>
      )}
      <section className="evaluation-metrics">
        {metrics.map(([value, name, icon, note, ratio], index) => (
          <article key={name}>
            <div className="metric-card-top"><span><Icon name={icon} /></span><strong>{value}</strong></div>
            <h3>{name}</h3><p>{note}</p>
            <div className="mini-chart" aria-label={`${name}: ${value}`}><i className={`bar-${index + 1}`} style={{ width: `${(ratio ?? 0) * 100}%` }} /></div>
          </article>
        ))}
      </section>

      <section className="panel twins">
        <div className="panel-heading">
          <div><p className="eyebrow">Language fairness</p><h2>“Twins” comparison</h2><p>The same facts written in polished, plain and second-language English should get the same suggestion for every rule.</p></div>
          <span className="pass-chip"><Icon name={s.twin_consistency_rate === 1 ? "check" : "info"} />{pct(s.twin_consistency_rate)} consistent</span>
        </div>
        {[...families.entries()].map(([family, groups]) => (
          <div className="table-wrap" key={family}>
            <table className="twins-table">
              <thead><tr><th>Family {family} · rule</th>{groups[0].members.map((m) => <th key={m.case_code}>{humanise(m.style ?? "untagged")} ({m.case_code})</th>)}<th>Same result?</th></tr></thead>
              <tbody>
                {groups.map((g) => (
                  <tr key={g.rule_code}>
                    <td><strong>{g.rule_code}</strong></td>
                    {g.members.map((m) => <td key={m.case_code}>{m.predicted && STATUSES.has(m.predicted) ? <StatusChip status={m.predicted as AIStatus} /> : m.predicted ?? "—"}</td>)}
                    <td><strong className={g.consistent ? "" : "yes"}>{g.consistent ? "Yes" : "No"}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {families.size === 0 && <p className="empty-state">No twin families in this run.</p>}
      </section>

      <section className="panel">
        <div className="panel-heading"><div><h2>Accuracy by writing style</h2><p>Large gaps would mean people are treated differently because of how they write.</p></div></div>
        <div className="status-summary">
          {Object.entries(s.accuracy_by_language_style ?? {}).map(([style, v]) => <span key={style}><strong>{pct(v)}</strong> {humanise(style)}</span>)}
        </div>
        <p className="check-source">{s.failures} failure(s) listed in the full report · {s.invalid_findings ?? 0} finding(s) marked invalid by verification.</p>
      </section>

      <section className="limitations">
        <span><Icon name="info" size={24} /></span>
        <div><h2>Limitations</h2>
          <p>The test set is small and synthetic, and the answer key needs review by an officer. The assistant may miss context in unusual documents or short answers, and it cannot read scanned images. Confidence is not certainty. Officers must verify every quote and decide every finding.</p>
        </div>
      </section>
    </main>
  );
}
