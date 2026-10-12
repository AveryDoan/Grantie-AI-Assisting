// Outcomes: one page for the scores and the panel's decisions, the ineligible letters, letters to release and released letters.
// Scores: each applicant's weighted total is plain arithmetic from the officer's own marks and the rule pack's weights, shown here
// only, to people with can_view_round_scores. It is never in letters, emails, the Overview or any AI prompt. The tool does not select,
// rank or suggest a cut-off: the default order is by reference, any other order is a click, totals carry no label or colour, and
// "places available" is a reference line only. Applicant names are hidden unless asked for (that is logged).
import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError, api, type OutcomeLists, type RoundDecision, type ScoreBreakdown, type ScoreRow, type ScoreTable } from "../api";
import { useAuth } from "../auth";
import { usePersisted } from "../persisted";
import { decisionLabel, filterRows, nextSort, placesLine, sortRows, type Filters, type Sort, type SortKey } from "../scoresTable";
import { Button, ErrorNotice, Icon, Loading, formatDate } from "../ui";
import { BatchRelease } from "./ReleaseParts";

type Tab = "scores" | "ineligible" | "release" | "released";
const TABS: { key: Tab; label: string }[] = [
  { key: "scores", label: "Scores and decisions" }, { key: "ineligible", label: "Ineligible" },
  { key: "release", label: "Letters to release" }, { key: "released", label: "Released" },
];
const MIN_NOTE = 20;
const today = () => new Date().toISOString().slice(0, 10);

export default function Outcomes() {
  const [tab, setTab] = usePersisted<Tab>("outcomes.tab", "scores");
  const [names, setNames] = usePersisted("outcomes.names", false);
  const [scores, setScores] = useState<ScoreTable | null>(null);
  const [denied, setDenied] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [lists, setLists] = useState<OutcomeLists | null>(null);
  const load = () => {
    api.outcomeScores(names).then((s) => { setScores(s); setDenied(false); }, (e) => (e instanceof ApiError && e.status === 403 ? setDenied(true) : setError(e)));
    api.outcomeLists().then(setLists, setError);
  };
  useEffect(load, [names]); // eslint-disable-line react-hooks/exhaustive-deps

  const rows = scores?.rows ?? [];
  const decided = rows.filter((r) => r.decision).length;
  const strip: [string, string | number][] = [
    ["Scored and ready", denied ? "–" : rows.filter((r) => r.ready).length],
    ["Waiting for marks", denied ? "–" : rows.filter((r) => r.status !== "complete" && !r.decision).length],
    ["Decided", denied ? "–" : decided],
    ["Ineligible", lists?.ineligible.length ?? "–"],
    ["Letters to release", lists?.held.length ?? "–"],
  ];
  return (
    <main className="page oc-wide">
      <div className="page-heading"><div><h1>Outcomes</h1>
        <p>Scores, the panel’s decisions and the letters that follow. People decide; this page records it.</p></div></div>
      <div className="oc-strip" role="list" aria-label="Summary">
        {strip.map(([label, n]) => <div key={label} role="listitem"><b>{n}</b><span>{label}</span></div>)}
      </div>
      <div className="oc-tabs" role="tablist" aria-label="Outcomes">
        {TABS.map((t) => <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>{t.label}</button>)}
      </div>
      <ErrorNotice error={error} />
      {tab === "scores" && (denied ? <NoPermission /> : !scores ? <Loading /> : <Scores data={scores} names={names} setNames={setNames} onChanged={load} />)}
      {tab === "ineligible" && <Ineligible lists={lists} />}
      {tab === "release" && <BatchRelease held={lists?.held ?? null} onDone={load} />}
      {tab === "released" && <Released lists={lists} />}
    </main>
  );
}

function NoPermission() {
  return (
    <section className="panel oc-pad"><p className="notice"><Icon name="lock" /><span><b>Scores are not shown to you.</b> Weighted totals can be seen by the assessment panel and the lead officer
      (the permission “can view round scores”). You can still use the other tabs. Ask the lead officer if you need access.</span></p></section>
  );
}

// ------------------------------------------------------------------ tab 1: scores and decisions

function Scores({ data, names, setNames, onChanged }: { data: ScoreTable; names: boolean; setNames: (v: boolean) => void; onChanged: () => void }) {
  const [help, setHelp] = usePersisted("outcomes.howto", true);
  const [sort, setSort] = useState<Sort | null>(null);
  const [filters, setFilters] = useState<Filters>({ search: "", decision: "all", marks: "all" });
  const [hidden, setHidden] = usePersisted<string[]>("outcomes.hidden", []);
  const [dense, setDense] = usePersisted("outcomes.dense", false);
  const [picked, setPicked] = usePersisted<string[]>("outcomes.picked", []);
  const [open, setOpen] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<string[] | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const rows = useMemo(() => sortRows(filterRows(data.rows, filters), sort), [data.rows, filters, sort]);
  const ready = rows.filter((r) => r.ready);
  const chosen = data.rows.filter((r) => picked.includes(r.application_id) && r.ready);
  const line = placesLine(sort, data.places_available, rows.length);
  const crit = data.criteria.filter((c) => !hidden.includes(c.code));
  const toggleSort = (key: SortKey) => setSort(nextSort(sort, key));
  const arrow = (key: SortKey) => (sort?.key === key ? (sort.dir === "asc" ? "▲" : "▼") : "");
  const ariaSort = (key: SortKey) => (sort?.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none");
  const cols = 5 + crit.length + 2;

  const csv = async () => {
    try {
      const text = await api.outcomeCsv();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = "round-scores.csv"; a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { setError(e); }
  };
  const navList = rows.map((r) => r.application_id);

  return (
    <div className={`od-page ${open ? "open" : ""}`}>
      <div className="od-main">
        <section className="oc-how">
          <button className="link-button" aria-expanded={help} onClick={() => setHelp(!help)}>{help ? "Hide" : "Show"} how this works</button>
          {help && <ol className="oc-steps"><li><b>1</b> Review the scores</li><li><b>2</b> The panel decides</li><li><b>3</b> Record the decision</li><li><b>4</b> Check and release letters</li></ol>}
        </section>
        {data.weights_error && <p className="notice error-notice" role="alert"><Icon name="info" /><span><b>Totals cannot be calculated.</b> {data.weights_error}. Fix the weights in the rule pack.</span></p>}
        {data.normalised && !data.weights_error && <p className="notice"><Icon name="info" />The weights add up to {data.weight_sum}, not 100, so each total is divided by {data.weight_sum} (normalised).</p>}
        {saved && <p className="notice ok-notice" role="status"><Icon name="check" />{saved}</p>}
        <ErrorNotice error={error} />

        <section className="panel oc-table-card" aria-labelledby="oc-scores">
          <div className="oc-toolbar">
            <h2 id="oc-scores">Scores <span className="pill">{rows.length}</span></h2>
            <label className="search"><Icon name="search" /><input aria-label="Search by reference" placeholder="Search reference" value={filters.search} onChange={(e) => setFilters({ ...filters, search: e.target.value })} /></label>
            <label className="oc-select"><span>Decision</span><select value={filters.decision} onChange={(e) => setFilters({ ...filters, decision: e.target.value as Filters["decision"] })}>
              <option value="all">All</option><option value="none">Not decided</option><option value="selected">Selected</option><option value="not_selected_competitive">Not selected (competitive)</option></select></label>
            <label className="oc-select"><span>Marks</span><select value={filters.marks} onChange={(e) => setFilters({ ...filters, marks: e.target.value as Filters["marks"] })}>
              <option value="all">All</option><option value="complete">Complete</option><option value="waiting">Waiting for marks</option></select></label>
            <details className="oc-menu"><summary>Columns</summary><div>
              {data.criteria.map((c) => <label key={c.code}><input type="checkbox" checked={!hidden.includes(c.code)} onChange={(e) => setHidden(e.target.checked ? hidden.filter((h) => h !== c.code) : [...hidden, c.code])} />{c.code} {c.title}</label>)}
            </div></details>
            <button className="button button-quiet" aria-pressed={dense} onClick={() => setDense(!dense)}>{dense ? "Comfortable" : "Compact"}</button>
            <Button variant="quiet" icon="eye" onClick={() => setNames(!names)} title="Names are hidden to keep a competitive decision blind. Showing them is logged.">{names ? "Hide applicant names" : "Show applicant names"}</Button>
            <Button variant="secondary" icon="download" onClick={() => void csv()}>Export CSV</Button>
            <Button variant="secondary" icon="file" onClick={() => window.print()}>Print for the panel</Button>
            <Formula data={data} />
          </div>
          <p className="oc-note">Rows are in reference order. Sorting is your choice (click a heading). Totals are plain numbers: they select nothing and rank nothing.{names ? " Names are showing (logged)." : " Applicant names are hidden."}</p>

          <div className={`oc-scroll ${dense ? "dense" : ""}`}>
            <table className="oc-table">
              <thead><tr>
                <th className="c-pick"><span className="sr-only">Choose</span></th>
                <th className="c-ref" aria-sort={ariaSort("reference")}><button onClick={() => toggleSort("reference")}>Reference {arrow("reference")}</button></th>
                {crit.map((c) => (
                  <th key={c.code} aria-sort={ariaSort(`m:${c.code}`)}><button onClick={() => toggleSort(`m:${c.code}`)}>
                    <b>{c.code}</b> {c.title} · {c.weight ? `${c.weight}%` : "not scored"} {arrow(`m:${c.code}`)}</button></th>
                ))}
                <th aria-sort={ariaSort("total")}><button onClick={() => toggleSort("total")}>Weighted total (0 to 100) {arrow("total")}</button></th>
                <th aria-sort={ariaSort("status")}><button onClick={() => toggleSort("status")}>Marks {arrow("status")}</button></th>
                <th aria-sort={ariaSort("decision")}><button onClick={() => toggleSort("decision")}>Decision {arrow("decision")}</button></th>
                <th><span className="sr-only">Open breakdown</span></th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <Fragment key={r.application_id}>
                    {line === i && <tr className="oc-places" aria-label={`${data.places_available} places available (reference line only)`}><td colSpan={cols}><span>{data.places_available} places available</span></td></tr>}
                    <tr className={`oc-row ${open === r.application_id ? "on" : ""}`} onClick={() => setOpen(r.application_id)}>
                      <td className="c-pick" onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" className="oc-check" aria-label={`Choose ${r.reference}`} disabled={!r.ready} title={r.ready ? undefined : r.why_not_ready ?? undefined}
                          checked={picked.includes(r.application_id)} onChange={(e) => setPicked(e.target.checked ? [...picked, r.application_id] : picked.filter((p) => p !== r.application_id))} />
                      </td>
                      <td className="c-ref" data-label="Reference"><button className="table-link" onClick={(e) => { e.stopPropagation(); setOpen(r.application_id); }}>{r.reference}</button>{r.name && <small>{r.name}</small>}</td>
                      {crit.map((c) => <td key={c.code} data-label={c.code} className="num">{r.marks[c.code] === "NA" ? "Not assessed" : r.marks[c.code] ?? "–"}</td>)}
                      <td data-label="Weighted total" className="num total">{r.total ? <><b>{r.total}</b><i className="oc-bar" aria-hidden="true" style={{ width: `${Math.min(100, Number(r.total))}%` }} /></>
                        : <span className="muted">{r.status === "cannot" ? "Cannot calculate" : "Not yet"}</span>}</td>
                      <td data-label="Marks"><span className={`oc-st ${r.status}`}><Icon name={r.status === "complete" ? "check" : "clock"} size={14} />{r.status === "complete" ? "Complete" : r.status === "cannot" ? "Cannot calculate" : `Waiting for marks (${r.marked} of ${r.of} marked)`}</span></td>
                      <td data-label="Decision"><span className={`oc-st ${r.decision ? "decided" : ""}`}><Icon name={r.decision ? "check" : "clock"} size={14} />{decisionLabel(r.decision)}</span></td>
                      <td><button className="icon-button" aria-label={`Open the breakdown for ${r.reference}`} onClick={(e) => { e.stopPropagation(); setOpen(r.application_id); }}><Icon name="chevron" /></button></td>
                    </tr>
                  </Fragment>
                ))}
                {rows.length === 0 && <tr><td colSpan={cols} className="empty-state">No applications match. Eligible applications appear here once every eligibility rule is confirmed Met.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="oc-pickbar-inline">
            <Button variant="quiet" disabled={!ready.length} onClick={() => setPicked([...new Set([...picked, ...ready.map((r) => r.application_id)])])}>Select visible ({ready.length} ready)</Button>
            <small className="muted">There is no “select all”. Saving more than one decision at a time needs the count typed.</small>
          </div>
        </section>
      </div>

      {open && <Drawer id={open} names={names} data={data} nav={navList} setOpen={setOpen} onDecide={(id) => setDeciding([id])} onChanged={onChanged} />}
      {chosen.length > 0 && (
        <div className="oc-pickbar" role="region" aria-label="Selected applications">
          <span><b>{chosen.length}</b> selected</span>
          <Button variant="quiet" onClick={() => setPicked([])}>Clear</Button>
          <Button onClick={() => setDeciding(chosen.map((r) => r.application_id))} disabled={!data.can_record} title={data.can_record ? undefined : "You do not have permission to record decisions"}>Record decision</Button>
        </div>
      )}
      {deciding && <DecisionDialog data={data} ids={deciding} onClose={() => setDeciding(null)}
        onDone={(m) => { setDeciding(null); setPicked([]); setSaved(m); onChanged(); }} />}
    </div>
  );
}

function Formula({ data }: { data: ScoreTable }) {
  return (
    <details className="oc-menu"><summary>How is this calculated?</summary>
      <div className="oc-formula">
        <p><b>{data.formula}</b></p>
        <p>Marks are 0 to 100, entered by officers with a reason. Weights come from the approved rule pack: {data.criteria.map((c) => `${c.code} ${c.weight ? c.weight + "%" : "not scored"}`).join(", ")}.</p>
        <p>Criterion left unassessed: <b>{data.policy === "block" ? "no total is calculated" : data.policy === "zero" ? "counts as 0" : "left out and the other weights are re-scaled"}</b>. Totals show {data.decimals} decimal place; the exact value is kept.</p>
        <p className="muted">Open an applicant to see their own worked example. Nothing else feeds the total: no AI output, no finding, no signal.</p>
      </div>
    </details>
  );
}

// ------------------------------------------------------------------ the breakdown drawer (below the header, wide, Previous / Next)

function Drawer({ id, names, data, nav, setOpen, onDecide, onChanged }: {
  id: string; names: boolean; data: ScoreTable; nav: string[]; setOpen: (id: string | null) => void; onDecide: (id: string) => void; onChanged: () => void;
}) {
  const [b, setB] = useState<ScoreBreakdown | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [wide, setWide] = usePersisted("outcomes.drawerwide", false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [reconsidering, setReconsidering] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const at = nav.indexOf(id);
  const load = () => { setB(null); api.outcomeBreakdown(id, names).then(setB, setError); };
  useEffect(load, [id, names]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key === "Escape") setOpen(null);
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (e.key === "j" && nav[at + 1]) setOpen(nav[at + 1]);
      if (e.key === "k" && nav[at - 1]) setOpen(nav[at - 1]);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [at, nav, setOpen]);
  const start = async () => {
    setBusy(true); setError(null);
    try { await api.reconsider(id, reason.trim()); setReconsidering(false); setReason(""); load(); onChanged(); } catch (e) { setError(e); }
    setBusy(false);
  };
  return (
    <aside className={`od ${wide ? "wide" : ""}`} aria-label={`Breakdown for ${b?.reference ?? ""}`}>
      <div className="od-top">
        <div><p className="eyebrow">Breakdown</p><h2>{b?.reference ?? "…"}{b?.name ? ` · ${b.name}` : ""}</h2></div>
        <span className="od-nav">
          <Button variant="secondary" disabled={!nav[at - 1]} onClick={() => setOpen(nav[at - 1])}>Previous <kbd>K</kbd></Button>
          <span aria-live="polite">{at + 1} of {nav.length}</span>
          <Button variant="secondary" disabled={!nav[at + 1]} onClick={() => setOpen(nav[at + 1])}>Next <kbd>J</kbd></Button>
          <button className="icon-button" onClick={() => setWide(!wide)} aria-pressed={wide} aria-label={wide ? "Make the panel narrower" : "Expand the panel"}><Icon name={wide ? "collapse" : "external"} /></button>
          <button className="icon-button" onClick={() => setOpen(null)} aria-label="Close (Esc)"><Icon name="close" /></button>
        </span>
      </div>
      <div className="od-body">
        <ErrorNotice error={error} />
        {!b ? <Loading /> : (
          <>
            <div className="od-head">
              <div><small>Weighted total</small><b className="od-total">{b.total ?? (b.status === "cannot" ? "Cannot calculate" : "Not yet")}</b>{b.total && <small> out of 100</small>}</div>
              <span className={`oc-st ${b.status}`}><Icon name={b.status === "complete" ? "check" : "clock"} size={14} />{b.status === "complete" ? "Marks complete" : b.reason ?? "Waiting for marks"}</span>
              <span className={`oc-st ${b.decision?.stands ? "decided" : ""}`}><Icon name={b.decision?.stands ? "check" : "clock"} size={14} />{b.decision?.stands ? decisionLabel(b.decision.decision) : "Not decided"}</span>
            </div>
            <table className="od-table">
              <thead><tr><th>Criterion</th><th className="num">Mark (0 to 100)</th><th className="num">Weight</th><th className="num">Points</th></tr></thead>
              <tbody>
                {b.rows.map((r) => (
                  <Fragment key={r.code}>
                    <tr className="od-crit" tabIndex={0} aria-expanded={expanded === r.code} onClick={() => setExpanded(expanded === r.code ? null : r.code)}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded(expanded === r.code ? null : r.code); } }}>
                      <td><b>{r.code}</b> {r.title}</td><td className="num">{r.not_assessed ? "Not assessed" : r.mark ?? "–"}</td>
                      <td className="num">{r.weight ? `${r.weight}%` : "not scored"}</td><td className="num">{r.points ?? "–"}</td>
                    </tr>
                    {expanded === r.code && (
                      <tr className="od-why"><td colSpan={4}>
                        <p><b>Officer’s reason:</b> {r.reason ?? "No reason recorded."}</p>
                        <p className="muted">{r.by ? `Marked by ${r.by}` : ""}{r.at ? `, ${formatDate(r.at, true)}` : ""}</p>
                        <a href={r.evidence}>View evidence</a>
                      </td></tr>
                    )}
                  </Fragment>
                ))}
                <tr className="od-sum"><td colSpan={3}><b>Weighted total</b></td><td className="num"><b>{b.total ?? "–"}</b></td></tr>
              </tbody>
            </table>
            {b.worked_example && <p className="od-example"><b>Worked example</b> {b.worked_example}</p>}
            <section><h3>Eligibility</h3><p><Icon name="check" size={14} /> {b.eligibility}. <a href={b.deep_link.replace("sub=merit", "sub=eligibility")}>See the findings</a></p></section>
            <section><h3>History of marks</h3>
              {b.history.length === 0 ? <p className="muted">No mark changes yet.</p> : (
                <ul className="od-history">{b.history.map((h, i) => (
                  <li key={i}><b>{h.rule_code}</b> {h.not_assessed ? "set to Not assessed" : `${h.old_mark ?? "–"} → ${h.new_mark}`} · {h.by} · {formatDate(h.at, true)}{h.reason ? <small>{h.reason}</small> : null}</li>
                ))}</ul>
              )}
            </section>
            <section><h3>Decision</h3>
              {!b.decision ? <p>Not decided.</p> : (
                <dl className="rv-facts">
                  <div><dt>Decision</dt><dd>{decisionLabel(b.decision.decision)}{b.decision.stands ? "" : " (being reconsidered)"}</dd></div>
                  <div><dt>Decided by</dt><dd>{b.decision.decided_by}, {b.decision.authority}</dd></div>
                  <div><dt>Where recorded</dt><dd>{b.decision.reference}</dd></div>
                  {b.decision.reason_note && <div><dt>Officer’s note</dt><dd>{b.decision.reason_note}</dd></div>}
                  {b.decision.score_snapshot && <div><dt>Total when decided</dt><dd>{b.decision.score_snapshot.total_shown ?? "–"} (rule pack {b.decision.score_snapshot.rule_pack_version}). This record does not change.</dd></div>}
                </dl>
              )}
              {b.decision?.stands && b.can_record && (reconsidering ? (
                <div className="gf-inline"><label className="field"><span>Why is this being reconsidered? <b>*</b></span><textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
                  <Button disabled={busy || !reason.trim()} onClick={() => void start()}>Start the reconsideration</Button><Button variant="quiet" onClick={() => setReconsidering(false)}>Cancel</Button></div>
              ) : <p className="notice"><Icon name="lock" /><span>Marks are locked while a decision stands. <button className="link-button" onClick={() => setReconsidering(true)}>Start a reconsideration</button> (needs a reason, logged).</span></p>)}
            </section>
            <p className="muted"><b>How is this calculated?</b> {data.formula}. Weights come from the rule pack. Nothing else feeds the total.</p>
          </>
        )}
      </div>
      <div className="od-actions">
        <a className="button button-secondary" href={b?.deep_link ?? "#"}>Open full application</a>
        <Button disabled={!b || !data.can_record || !data.rows.find((r) => r.application_id === id)?.ready} onClick={() => onDecide(id)}
          title={data.rows.find((r) => r.application_id === id)?.ready ? undefined : data.rows.find((r) => r.application_id === id)?.why_not_ready ?? undefined}>Record decision</Button>
      </div>
    </aside>
  );
}

// ------------------------------------------------------------------ recording a decision (a dialog, opened from the selection bar)

function DecisionDialog({ data, ids, onClose, onDone }: { data: ScoreTable; ids: string[]; onClose: () => void; onDone: (message: string) => void }) {
  const { me } = useAuth();
  const [decision, setDecision] = useState<RoundDecision["decision"] | "">("");
  const [by, setBy] = useState(me?.display_name ?? "");
  const [authority, setAuthority] = useState("Assessment panel");
  const [date, setDate] = useState(today());
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);       // messages appear only after a try to save
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const picked: ScoreRow[] = data.rows.filter((r) => ids.includes(r.application_id));
  const problems = [!decision && "Choose the decision", !by.trim() && "Say who decided", !authority.trim() && "Say under what authority", !ref.trim() && "Give the panel minute or meeting reference",
    decision === "not_selected_competitive" && note.trim().length < MIN_NOTE && `Write a reason note (at least ${MIN_NOTE} characters)`,
    ids.length > 1 && typed.trim() !== String(ids.length) && `Type ${ids.length} to confirm`].filter(Boolean) as string[];
  const save = async () => {
    setTried(true);
    if (problems.length) return;
    setBusy(true); setError(null);
    try {
      await api.recordRoundDecisions({ application_ids: ids, decision: decision as RoundDecision["decision"], decided_by: by.trim(), authority: authority.trim(), decided_at: date,
        reference: ref.trim(), reason_note: note.trim() || null, typed_count: ids.length > 1 ? Number(typed) : null });
      onDone(`${ids.length} ${ids.length === 1 ? "decision" : "decisions"} recorded. Letter drafts are ready in each Outcome step. Nothing was sent.`);
    } catch (e) { setError(e); setBusy(false); }
  };
  useEffect(() => {
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => { document.body.style.overflow = ""; document.removeEventListener("keydown", onKey); };
  }, [onClose]);
  const err = (ok: boolean, text: string): ReactNode => (tried && !ok ? <small className="oc-err" role="alert"><Icon name="info" size={14} />{text}</small> : null);
  return createPortal(
    <div className="modal-backdrop">
      <div className="db-dialog od-dialog" role="dialog" aria-modal="true" aria-labelledby="od-title">
        <div className="db-dialog-head"><h2 id="od-title">Record the decision</h2><button className="icon-button" onClick={onClose} aria-label="Close"><Icon name="close" /></button></div>
        <fieldset className="od-seg"><legend>Decision</legend>
          <label className={decision === "selected" ? "on" : ""}><input type="radio" name="od-d" checked={decision === "selected"} onChange={() => setDecision("selected")} /><span>Selected</span></label>
          <label className={decision === "not_selected_competitive" ? "on" : ""}><input type="radio" name="od-d" checked={decision === "not_selected_competitive"} onChange={() => setDecision("not_selected_competitive")} /><span>Not selected (competitive)</span></label>
        </fieldset>
        {err(Boolean(decision), "Choose the decision")}
        <div className="rd-fields">
          <label className="field"><span>Decided by <b>*</b></span><input value={by} onChange={(e) => setBy(e.target.value)} />{err(Boolean(by.trim()), "Say who decided")}</label>
          <label className="field"><span>Authority (panel or role) <b>*</b></span><input value={authority} onChange={(e) => setAuthority(e.target.value)} />{err(Boolean(authority.trim()), "Say under what authority")}</label>
          <label className="field"><span>Date of the decision <b>*</b></span><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
          <label className="field"><span>Where it was decided (minute or meeting reference) <b>*</b></span><input value={ref} placeholder="For example: Panel minute 2026/7" onChange={(e) => setRef(e.target.value)} />{err(Boolean(ref.trim()), "Give the panel minute or meeting reference")}</label>
        </div>
        <label className="field"><span>Reason note {decision === "not_selected_competitive" && <b>*</b>} <small>(for Not selected: at least {MIN_NOTE} characters)</small></span>
          <textarea rows={3} value={note} placeholder="For example: Funding was limited to the places available; the panel recommended other applications." onChange={(e) => setNote(e.target.value)} />
          {err(decision !== "not_selected_competitive" || note.trim().length >= MIN_NOTE, `Write a reason note (at least ${MIN_NOTE} characters)`)}</label>
        <p><b>{picked.length}</b> {picked.length === 1 ? "application" : "applications"} <small className="muted">(totals are shown for information only)</small></p>
        <ul className="rd-refs od-refs">{picked.map((r) => <li key={r.application_id}>{r.reference} <small>{r.total ?? "Not yet"}</small></li>)}</ul>
        {ids.length > 1 && <label className="field"><span>Type the number of applications ({ids.length}) to confirm</span><input inputMode="numeric" value={typed} onChange={(e) => setTyped(e.target.value)} />
          {err(typed.trim() === String(ids.length), `Type ${ids.length} to confirm`)}</label>}
        <ErrorNotice error={error} />
        <div className="rd-dialog-actions"><Button disabled={busy} onClick={() => void save()}>Save decision</Button><Button variant="quiet" onClick={onClose}>Cancel</Button></div>
        <small className="muted">Saving releases nothing. It drafts letters and moves the applications to Outcome. The marks, weights and total are stored with the decision.</small>
      </div>
    </div>,
    document.body,
  );
}

// ------------------------------------------------------------------ the other tabs

function Ineligible({ lists }: { lists: OutcomeLists | null }) {
  if (!lists) return <Loading />;
  return (
    <section className="panel oc-pad"><h2>Ineligible <span className="pill">{lists.ineligible.length}</span></h2>
      <p className="muted">An officer confirmed a rule as Not met. Open each to review the letter and sign off.</p>
      {lists.ineligible.length === 0 ? <p className="empty-state">No application has been confirmed as Not met.</p> : (
        <div className="rv-table-wrap"><table className="rv-table"><thead><tr><th>Reference</th><th>Letter</th><th>Sign-off</th><th /></tr></thead>
          <tbody>{lists.ineligible.map((r) => (
            <tr key={r.application_id}><td><strong>{r.reference}</strong></td>
              <td><span className="oc-st"><Icon name={r.letter?.released ? "check" : "clock"} size={14} />{!r.letter ? "Not drafted" : r.letter.released ? "Released" : r.letter.viewed ? "Read, not released" : "Not read yet"}</span></td>
              <td><span className="oc-st"><Icon name={r.signed_off ? "check" : "clock"} size={14} />{r.signed_off ? "Signed off" : "To sign off"}</span></td>
              <td><a className="button button-quiet" href={r.deep_link}>Open Outcome</a></td></tr>))}</tbody></table></div>
      )}
    </section>
  );
}

function Released({ lists }: { lists: OutcomeLists | null }) {
  if (!lists) return <Loading />;
  const kind = (k: string) => (k === "not_selected" ? "Not selected" : k === "decline" ? "Not eligible" : "Next steps");
  return (
    <section className="panel oc-pad"><h2>Released <span className="pill">{lists.released.length}</span></h2>
      <p className="muted">Read-only. No email is connected, so a released letter is marked ready to send.</p>
      {lists.released.length === 0 ? <p className="empty-state">No letters have been released yet.</p> : (
        <div className="rv-table-wrap"><table className="rv-table"><thead><tr><th>Reference</th><th>Letter</th><th>Released</th><th>Delivery</th></tr></thead>
          <tbody>{lists.released.map((r, i) => <tr key={i}><td><a href={r.deep_link}><strong>{r.reference}</strong></a></td><td>{kind(r.kind)}</td><td>{formatDate(r.released_at, true)}</td><td>{r.delivery}</td></tr>)}</tbody></table></div>
      )}
    </section>
  );
}
