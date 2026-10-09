// Redaction & AI trace: what the AI was allowed to see, what it quoted, and
// how each quote maps back to the applicant's own words.
//  1. Redaction: status, leak scan, tokens (never values), per-text view of the
//     redacted copy; the original is shown only on request (audited).
//  2. AI extraction: facts and finding quotes exactly as the AI produced them
//     (with tokens), next to the restored words, with the code's verification.
import { useMemo, useState, type ReactNode } from "react";
import { api, type Detail, type OriginalView } from "../api";
import type { Navigate } from "../App";
import { Button, ErrorNotice, Icon, Loading, StatusChip, formatDate, humanise, useLoad } from "../ui";

const AI_STATUS: Record<string, { label: string; tone: "ok" | "warn" | "bad" }> = {
  ready: { label: "Redacted and leak-scanned · AI may read it", tone: "ok" },
  not_redacted: { label: "Not redacted yet · AI cannot read it", tone: "warn" },
  blocked_redaction_leak: { label: "Blocked · personal details were still visible", tone: "bad" },
  blocked_low_confidence: { label: "Held for review · uncertain detections", tone: "warn" },
};
const LOCATION: Record<string, string> = {
  outside_australia: "Lives outside Australia", nt_australia: "Lives in the NT", australia_outside_nt: "Lives in Australia, outside the NT", unknown: "Location unclear",
};
const TOKEN_RE = /(\[(?:[A-Z]+_\d+|LOCATION: [^\]]+)\])/g;

function Tokens({ text }: { text: string }) {
  const parts = text.split(TOKEN_RE);
  return <>{parts.map((p, i) => (i % 2 ? <mark key={i} className="token-mark">{p}</mark> : p))}</>;
}

function sourceLabel(source: string | null | undefined, detail: Detail): string {
  if (!source || source === "application_text") return "Application form";
  const id = source.replace(/^document:/, "");
  const doc = detail.documents.find((d) => d.id === id);
  return doc ? humanise(doc.declared_type) : humanise(source);
}

function Verified({ ok, method }: { ok: boolean | undefined; method?: string }) {
  return ok
    ? <span className="trace-verified ok"><Icon name="check" size={14} />Found in the text{method && method !== "exact" ? ` (${method})` : ""}</span>
    : <span className="trace-verified bad"><Icon name="close" size={14} />Not found in the text</span>;
}

function Section({ eyebrow, title, intro, aside, children }: { eyebrow: string; title: string; intro?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return <section className="panel trace-section">
    <div className="panel-heading"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2>{intro && <p>{intro}</p>}</div>{aside}</div>
    {children}
  </section>;
}

export default function AiTrace({ id, navigate }: { id: string; navigate: Navigate }) {
  const detailLoad = useLoad(() => api.detail(id), [id]);
  const reportLoad = useLoad(() => api.redactionReport(id), [id]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [original, setOriginal] = useState<OriginalView | null>(null);
  const [textKey, setTextKey] = useState("application_text");

  const detail = detailLoad.data;
  const report = reportLoad.data;
  const reload = () => { detailLoad.reload(); reportLoad.reload(); };
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setActionError(null);
    try { await fn(); reload(); } catch (e) { setActionError(e); } finally { setBusy(false); }
  };

  const texts = useMemo(() => {
    if (!detail) return [] as { key: string; label: string; redacted: string; status?: string | null; review?: boolean }[];
    return [
      { key: "application_text", label: "Application form", redacted: detail.application.redacted_text ?? "" },
      ...detail.documents.map((d) => ({
        key: `document:${d.id}`, label: `${humanise(d.declared_type)} · ${d.file_name}`, redacted: d.redacted_text ?? "",
        status: d.extraction_status, review: d.needs_manual_review,
      })),
    ];
  }, [detail]);

  if (detailLoad.error) return <main className="page"><ErrorNotice error={detailLoad.error} /></main>;
  if (!detail || !report) return <main className="page"><Loading /></main>;

  const app = detail.application;
  const status = AI_STATUS[report.ai_status] ?? AI_STATUS.not_redacted;
  const current = texts.find((t) => t.key === textKey) ?? texts[0];
  const originalText = !original ? null : textKey === "application_text" ? original.application_text
    : original.documents.find((d) => `document:${d.document_id}` === textKey)?.text ?? null;
  const quotes = detail.findings.flatMap((f) => [
    ...(f.evidence_quote ? [{ f, quote: f.evidence_quote, restored: f.evidence_quote_restored, verified: f.quote_verified, method: undefined as string | undefined, source: "application_text" }] : []),
    ...(f.supporting_quotes ?? []).map((q, i) => ({ f, quote: q.quote, restored: f.supporting_quotes_restored[i]?.quote ?? null, verified: q.verified, method: q.method, source: q.source ?? "application_text" })),
  ]);
  const run = report.run;
  const leaks = run ? Object.values(run.leak_scan ?? {}).reduce((a, b) => a + b, 0) : 0;

  return <main className="page wide-page trace-page">
    <div className="breadcrumb"><button onClick={() => navigate({ name: "queue" })}><Icon name="left" />Applications</button><span>/</span>
      <button onClick={() => navigate({ name: "review", id })}>{app.reference}</button><span>/</span><span>Redaction & AI trace</span></div>
    <div className="page-heading">
      <div><p className="eyebrow">Application {app.reference} · {app.applicant_name}</p><h1>Redaction & AI trace</h1>
        <p>What the AI was allowed to read, what it quoted, and how each quote maps back to the applicant’s own words.</p></div>
      <div className="trace-actions">
        <Button variant="secondary" icon="shield" disabled={busy} onClick={() => void act(() => api.redact(id, Boolean(run)))}>{run ? "Run redaction again" : "Run redaction"}</Button>
        <Button icon="search" disabled={busy || app.manual_assessment_requested || report.ai_status !== "ready"} onClick={() => void act(() => api.assess(id))}
          title={report.ai_status !== "ready" ? "Redact first. The AI only reads leak-scanned text." : undefined}>{busy ? "Working…" : detail.latest_run ? "Run AI check again" : "Run AI check"}</Button>
      </div>
    </div>
    <ErrorNotice error={actionError} />
    {app.manual_assessment_requested && <div className="notice"><Icon name="user" />The applicant asked for a person-only assessment. The AI will not be run on this application.</div>}

    <section className="evaluation-metrics trace-metrics">
      <article><div className="metric-card-top"><span><Icon name="shield" /></span><small>Redaction</small></div><strong className={`trace-tone ${run?.status === "needs_manual_review" ? "warn" : status.tone}`}>{run ? humanise(run.status) : "Not run"}</strong><h3>{status.label}</h3><p>{run ? `Last run ${formatDate(run.finished_at ?? run.started_at)}` : "Run redaction before the AI check."}</p></article>
      <article><div className="metric-card-top"><span><Icon name="user" /></span><small>Replaced</small></div><strong>{run ? Object.values(run.counts ?? {}).reduce((a, b) => a + b, 0) : "–"}</strong><h3>Personal details replaced</h3><p>{run ? Object.entries(run.counts ?? {}).map(([k, v]) => `${humanise(k)} ${v}`).join(" · ") : "No run yet"}</p></article>
      <article><div className="metric-card-top"><span><Icon name="search" /></span><small>Leak scan</small></div><strong className={`trace-tone ${leaks ? "bad" : "ok"}`}>{run ? (leaks ? `${leaks} found` : "Clean") : "–"}</strong><h3>Checked again before the AI</h3><p>Emails, phones, long numbers and every known value.</p></article>
      <article><div className="metric-card-top"><span><Icon name="file" /></span><small>Documents</small></div><strong>{report.documents.filter((d) => d.included_in_ai_input).length}/{report.documents.length}</strong><h3>Documents the AI can read</h3><p>{report.documents.filter((d) => d.needs_manual_review).length} need a person to read them.</p></article>
      <article><div className="metric-card-top"><span><Icon name="info" /></span><small>Location</small></div><strong className="trace-small">{LOCATION[report.location_class ?? ""] ?? "–"}</strong><h3>Worked out before redaction</h3><p>The AI sees this class, not the address.</p></article>
    </section>

    <Section eyebrow="Step 1 · Redaction" title="What the AI is allowed to read"
      intro={<>Placeholders such as <mark className="token-mark">[PERSON_1]</mark> replace personal details. The original is only shown when you ask, and each view is recorded in the audit trail.</>}
      aside={<Button variant="secondary" icon={original ? "close" : "user"} disabled={!run || busy}
        onClick={() => original ? setOriginal(null) : void act(async () => setOriginal(await api.originalView(id)))}>{original ? "Hide original" : "Show original (audited)"}</Button>}>
      <div className="trace-text-tabs" role="tablist">
        {texts.map((t) => <button key={t.key} role="tab" aria-selected={t.key === current?.key} className={t.key === current?.key ? "active" : ""} onClick={() => setTextKey(t.key)}>
          {t.label}{t.review && <span className="trace-pill warn">Person to read</span>}</button>)}
      </div>
      {current && <div className={original ? "trace-compare" : "trace-compare single"}>
        <div><h3><Icon name="shield" />Redacted copy (sent to the AI)</h3>
          {current.redacted ? <pre className="doc-text trace-text"><Tokens text={current.redacted} /></pre>
            : <p className="empty-state">{current.status && current.status !== "ok" ? "No readable text (scan, photo or unsupported file). This is never sent to the AI; a person reads it." : "Not redacted yet. Run redaction."}</p>}
        </div>
        {original && <div><h3><Icon name="user" />Original (officer only)</h3>
          {originalText ? <pre className="doc-text trace-text">{originalText}</pre> : <p className="empty-state">No original text for this file.</p>}
          {textKey === "application_text" && <small className={original.matches_stored_original ? "check-source" : "check-source bad"}>{original.matches_stored_original ? "Restored text matches the stored application exactly." : "Restored text differs from the stored application. Run redaction again."}</small>}
        </div>}
      </div>}
      {report.tokens.length > 0 && <details className="trace-tokens"><summary>{report.tokens.length} placeholders used</summary>
        <table className="data-table"><thead><tr><th>Placeholder</th><th>Type</th><th>Times</th><th>Where</th></tr></thead>
          <tbody>{report.tokens.map((t) => <tr key={t.token}><td><mark className="token-mark">{t.token}</mark></td><td>{humanise(t.entity_type)}</td><td>{t.occurrences}</td><td>{t.sources.map((s) => sourceLabel(s, detail)).join(", ")}</td></tr>)}</tbody></table>
      </details>}
    </Section>

    <Section eyebrow="Step 2 · AI extraction" title="Facts the AI extracted"
      intro="Each fact comes with the passage the AI quoted. Code then checks the quote is really in the redacted text before anything relies on it.">
      {!detail.latest_run ? <p className="empty-state">The AI check has not run yet.</p> : detail.facts.length === 0 ? <p className="empty-state">No facts were extracted in this run.</p> :
        <table className="data-table trace-table"><thead><tr><th>Fact</th><th>Value</th><th>Quote as the AI saw it</th><th>Check</th></tr></thead>
          <tbody>{detail.facts.map((f) => <tr key={f.id}><td>{humanise(f.fact_key)}</td><td><strong>{f.fact_value}</strong></td>
            <td>{f.source_quote ? <q><Tokens text={f.source_quote} /></q> : <em>No quote</em>}<small>{sourceLabel(f.source, detail)}</small></td>
            <td>{f.source_quote ? <Verified ok={f.quote_verified} /> : "–"}</td></tr>)}</tbody></table>}
    </Section>

    <Section eyebrow="Step 3 · Quotes behind each finding" title="What the AI quoted, and the applicant’s own words"
      intro="Left: the quote exactly as the AI returned it (placeholders included). Right: the same passage restored for you from the encrypted map. Findings with an unverified quote are not valid until an officer decides.">
      {!detail.latest_run ? <p className="empty-state">The AI check has not run yet.</p> : quotes.length === 0 ? <p className="empty-state">No quotes in this run.</p> :
        <div className="trace-quotes">{quotes.map((q, i) => <article key={`${q.f.id}-${i}`} className={q.verified ? "trace-quote" : "trace-quote unverified"}>
          <header><span className="rule-number">{q.f.rule_code}</span><span className="trace-rule">{q.f.rule_text}</span><StatusChip status={q.f.ai_status} /></header>
          <div className="trace-compare">
            <div><small>AI quote ({sourceLabel(q.source, detail)})</small><blockquote><Tokens text={q.quote} /></blockquote></div>
            <div><small>Applicant’s words (restored)</small><blockquote>{q.restored ?? <em>Could not be restored</em>}</blockquote></div>
          </div>
          <footer><Verified ok={q.verified} method={q.method} />{q.f.check_source !== "llm" && <span className="check-source">Checked by {q.f.check_source === "code" ? "code" : "an officer"}</span>}
            <button className="link-button" onClick={() => navigate({ name: "review", id })}>Review rule <Icon name="arrow" size={14} /></button></footer>
        </article>)}</div>}
    </Section>
  </main>;
}
