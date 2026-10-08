import { useState, type ReactNode } from "react";
import { api, type Decision, type Detail, type DocumentRow, type Finding } from "../api";
import type { Navigate } from "../App";
import {
  APP_STATUS_LABEL, Button, CHECK_SOURCE_LABEL, ErrorNotice, Icon, Loading, StatusChip, effectiveStatus, formatDate,
  humanise, isDecided, useLoad,
} from "../ui";

const DOC_LABEL: Record<string, string> = {
  coe: "Confirmation of Enrolment", visa: "Visa grant notice", travel_document: "Passport / travel document",
  passport: "Passport / travel document", other: "Other document",
};

// Attention first: undecided before decided; within those, the statuses that need a closer look.
const PRIORITY: Record<string, number> = { Unclear: 0, "Needs evidence": 1, "Not met": 2, "Evidence only": 3, Met: 4 };
function sortKey(f: Finding): number {
  const base = isDecided(f) ? 100 : 0;
  const invalid = !f.is_valid || f.error_flag ? -10 : 0;
  return base + invalid + (PRIORITY[f.ai_status] ?? 5);
}

// ---------------------------------------------------------------- documents

function DocumentCard({ doc, onOpen }: { doc: DocumentRow; onOpen: () => void }) {
  const title = DOC_LABEL[doc.declared_type] ?? humanise(doc.declared_type);
  const rightType = doc.type_matches;
  const detailsMatch = doc.type_matches === null ? null : !doc.needs_verification;
  const state = (v: boolean | null) => (v === null ? "warn" : v ? "ok" : "warn");
  return (
    <article className="document-card">
      <div className="doc-top">
        <span className="doc-icon"><Icon name="file" /></span>
        <div><strong>{title}</strong><small>Sample document · {doc.file_name}</small></div>
        <button aria-label={`Open ${title}`} className="icon-button" onClick={onOpen}><Icon name="external" /></button>
      </div>
      <div className="doc-checks">
        <span className={state(rightType)}><Icon name={rightType ? "check" : "question"} />
          {rightType === null ? "Not checked yet" : rightType ? "Right document" : `Looks like: ${DOC_LABEL[doc.detected_type ?? "other"]}`}</span>
        <span className={state(detailsMatch)}><Icon name={detailsMatch ? "check" : "question"} />
          {detailsMatch === null ? "Details not compared" : detailsMatch ? "Details match" : "Needs verification"}</span>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------- dialogs

type DialogState =
  | { kind: "override"; finding: Finding }
  | { kind: "decide"; finding: Finding }
  | { kind: "confirm-not-met"; finding: Finding }
  | { kind: "ask"; finding: Finding };

function DecisionDialog({ state, onDone, close }: { state: Exclude<DialogState, { kind: "ask" }>; onDone: () => void; close: () => void }) {
  const f = state.finding;
  const fixed = state.kind === "confirm-not-met";
  const [status, setStatus] = useState<Decision | "">(fixed ? "Not met" : "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const title = { override: "Override AI suggestion", decide: "Record your decision", "confirm-not-met": "Confirm “Not met”" }[state.kind];
  const intro = {
    override: "Choose your finding and explain why. Your reason will be saved in the audit trail.",
    decide: f.ai_status === "Evidence only"
      ? "This is a judgement rule. The AI only found passages to read; it made no suggestion. Record your decision and the reason."
      : "This finding could not be verified, so it cannot simply be confirmed. Record your own decision and the reason.",
    "confirm-not-met": "A finding of “Not met” always needs a typed reason. It will appear in the applicant’s letter.",
  }[state.kind];

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.review(f.id, { action: fixed ? "confirm" : "override", final_status: status as Decision, reason: reason.trim() });
      onDone();
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="decision-title">
        <div className="dialog-head">
          <div><p className="eyebrow">Rule {f.rule_code}</p><h2 id="decision-title">{title}</h2></div>
          <button className="icon-button" onClick={close} aria-label="Close dialog"><Icon name="close" /></button>
        </div>
        <p>{intro}</p>
        {!fixed && (
          <fieldset><legend>New status <span aria-hidden="true">*</span></legend>
            <div className="status-options">
              {(["Met", "Not met", "Needs evidence", "Unclear"] as Decision[]).map((item) => (
                <label key={item} className={status === item ? "selected" : ""}>
                  <input type="radio" name="newStatus" value={item} checked={status === item} onChange={() => setStatus(item)} />
                  <StatusChip status={item} />
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <label className="field">
          <span>Reason <b>*</b></span>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Explain what evidence supports your decision" rows={4} />
          <small>{status === "Not met" ? "A clear justification is required for a finding of Not met." : "This helps another officer understand your decision."}</small>
        </label>
        <ErrorNotice error={error} />
        <div className="dialog-actions">
          <Button variant="secondary" onClick={close}>Cancel</Button>
          <Button disabled={busy || !status || !reason.trim()} onClick={() => void save()}>Save decision</Button>
        </div>
      </div>
    </div>
  );
}

function defaultMessage(name: string, f: Finding): string {
  return `Hello ${name},\n\nWe are checking your application and need a little more information about this requirement:\n${f.rule_text}\n\nPlease reply with the details, or a document that shows this. You can write in your own words; spelling and grammar do not matter.\n\nIf you would prefer to talk to a person, reply and ask for a call.\n\nStudy NT Grants Team`;
}

function AskDialog({ detail, finding, onDone, close }: { detail: Detail; finding: Finding; onDone: () => void; close: () => void }) {
  const name = detail.application.applicant_name ?? "applicant";
  const [message, setMessage] = useState(defaultMessage(name, finding));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.review(finding.id, { action: "ask_applicant" });
      await api.clarify(detail.application.id, { finding_id: finding.id, message_text: message.trim(), send: true });
      onDone();
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop">
      <div className="dialog wide" role="dialog" aria-modal="true" aria-labelledby="ask-title">
        <div className="dialog-head">
          <div><p className="eyebrow">Request more information · Rule {finding.rule_code}</p><h2 id="ask-title">Ask the applicant</h2></div>
          <button className="icon-button" onClick={close} aria-label="Close dialog"><Icon name="close" /></button>
        </div>
        <div className="recipient"><span className="avatar">{name.slice(0, 2).toUpperCase()}</span><div><small>To</small><strong>{name}</strong></div></div>
        <label className="field"><span>Message</span><textarea rows={10} value={message} onChange={(e) => setMessage(e.target.value)} /></label>
        <div className="notice"><Icon name="info" />Sending sets the application to “Awaiting applicant”. This prototype does not send real email: the request is recorded only.</div>
        <ErrorNotice error={error} />
        <div className="dialog-actions">
          <Button variant="secondary" onClick={close}>Cancel</Button>
          <Button icon="send" disabled={busy || !message.trim()} onClick={() => void send()}>Send request</Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- rule card

function Quote({ text, verified }: { text: string; verified: boolean }) {
  return (
    <blockquote className={verified ? "" : "quote-unverified"}>
      <span className="quote-mark">“</span>{text}
      <footer><Icon name={verified ? "check" : "close"} size={15} />{verified ? "Verified in application" : "Not found in the application – do not rely on this quote"}</footer>
    </blockquote>
  );
}

function RuleCard({
  f, index, total, locked, onDialog, onConfirm, busy,
}: {
  f: Finding; index: number; total: number; locked: boolean; busy: boolean;
  onDialog: (d: DialogState) => void; onConfirm: (f: Finding) => void;
}) {
  const decided = isDecided(f);
  const asked = f.latest_review?.action === "ask_applicant";
  const [expanded, setExpanded] = useState(!decided);
  const judgement = f.ai_status === "Evidence only";
  const quote = f.evidence_quote_restored;

  let confirmLabel = `Confirm ${f.ai_status.toLowerCase()}`;
  let confirmAction = () => onConfirm(f);
  let confirmDisabled = false;
  let confirmTitle: string | undefined;
  if (judgement) {
    confirmLabel = "Record decision";
    confirmAction = () => onDialog({ kind: "decide", finding: f });
  } else if (!f.is_valid) {
    confirmDisabled = true;
    confirmTitle = "This finding failed verification and cannot be confirmed. Use Override to record your own decision.";
  } else if (f.ai_status === "Not met") {
    confirmAction = () => onDialog({ kind: "confirm-not-met", finding: f });
  }

  return (
    <article id={`rule-${f.rule_code}`} className={`rule-card ${expanded ? "expanded" : ""}`}>
      <button className="rule-head" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
        <span className="rule-number">{decided ? <Icon name="check" size={16} /> : index}</span>
        <span className="rule-title"><strong>{f.rule_text}</strong><small>Rule {f.rule_code} · {index} of {total}{asked ? " · Waiting for applicant" : ""}</small></span>
        <StatusChip status={effectiveStatus(f)} />
        <span className={expanded ? "rotate" : ""}><Icon name="chevron" /></span>
      </button>
      {expanded && (
        <div className="rule-body">
          {f.source_clause && <span className="source-link">{f.source_clause}</span>}
          {(!f.is_valid || f.error_flag) && (
            <div className="invalid-banner"><Icon name="info" />
              <span>Not valid – do not rely on this suggestion.{f.error_detail ? ` ${f.error_detail}.` : ""} Check the application yourself.</span>
            </div>
          )}
          {judgement && <div className="judgement-note"><Icon name="user" /><strong>Officer judgement required – no AI suggestion</strong></div>}

          {judgement ? (
            f.supporting_quotes_restored.length ? (
              f.supporting_quotes_restored.map((q, i) => <Quote key={i} text={q.quote} verified={!!q.verified} />)
            ) : <p className="explanation">No passages were found for this criterion. Read the full application.</p>
          ) : quote ? (
            <Quote text={quote} verified={f.quote_verified} />
          ) : null}

          <p className="explanation">
            {judgement ? "Read the applicant’s own words and make your judgement. This response is not scored by AI." : f.rationale ?? "No explanation was given."}
          </p>
          {judgement && <div className="notice"><Icon name="info" />AI does not score writing style or detect AI-written text.</div>}

          <div className="rule-meta">
            <span className="check-source">{CHECK_SOURCE_LABEL[f.check_source]}</span>
            {f.confidence && f.check_source === "llm" && <span><small>AI confidence</small><strong className={`confidence ${f.confidence}`}><i />{humanise(f.confidence)}</strong></span>}
            {f.language_flag && <span className="language-flag"><Icon name="info" />The wording may be the obstacle – consider asking the applicant, not “Not met”</span>}
            {!f.language_flag && f.needs_applicant_clarification && <span className="language-flag"><Icon name="info" />May need clarification from applicant</span>}
          </div>

          {f.latest_review && (
            <p className="decision-line">
              {f.latest_review.action === "ask_applicant"
                ? <>You asked the applicant for more information on {formatDate(f.latest_review.reviewed_at, true)}.</>
                : <>AI suggested <strong>{f.ai_status}</strong> · Officer decided <strong>{f.latest_review.final_status}</strong> on {formatDate(f.latest_review.reviewed_at, true)}{f.latest_review.reason ? <> – “{f.latest_review.reason}”</> : null}</>}
            </p>
          )}

          {!locked && (
            <div className="rule-actions">
              <Button icon="check" disabled={busy || confirmDisabled} onClick={confirmAction} title={confirmTitle}>{confirmLabel}</Button>
              {!judgement && <Button variant="secondary" icon="edit" onClick={() => onDialog({ kind: "override", finding: f })}>Override</Button>}
              <Button variant="quiet" icon="send" onClick={() => onDialog({ kind: "ask", finding: f })}>Ask applicant</Button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------- application text with highlights

const MARK: Record<string, string> = {
  Met: "green-mark", Unclear: "purple-mark", "Evidence only": "purple-mark", "Needs evidence": "amber-mark", "Not met": "amber-mark",
};

function highlight(text: string, findings: Finding[]): { nodes: ReactNode; matched: Finding[] } {
  const spans: { start: number; end: number; f: Finding }[] = [];
  const lower = text.toLowerCase();
  for (const f of findings) {
    const quotes = [
      ...(f.evidence_quote_restored && f.quote_verified ? [f.evidence_quote_restored] : []),
      ...f.supporting_quotes_restored.filter((q) => q.verified).map((q) => q.quote),
    ];
    for (const q of quotes) {
      const at = lower.indexOf(q.toLowerCase());
      if (at >= 0 && !spans.some((s) => at < s.end && at + q.length > s.start)) spans.push({ start: at, end: at + q.length, f });
    }
  }
  spans.sort((a, b) => a.start - b.start);
  const nodes: ReactNode[] = [];
  let pos = 0;
  spans.forEach((s, i) => {
    nodes.push(text.slice(pos, s.start));
    nodes.push(<mark key={i} className={MARK[effectiveStatus(s.f)]}>{text.slice(s.start, s.end)}</mark>);
    pos = s.end;
  });
  nodes.push(text.slice(pos));
  return { nodes, matched: [...new Set(spans.map((s) => s.f))] };
}

function ApplicationText({ detail, tab, setTab, openDoc }: { detail: Detail; tab: "form" | "documents"; setTab: (t: "form" | "documents") => void; openDoc: string | null }) {
  const { fields = {}, answers = {} } = detail.application.application_text;
  const entries = [...Object.entries(fields), ...Object.entries(answers)].filter(([, v]) => v !== null && v !== "");
  return (
    <aside className="application-text">
      <div className="column-header"><div><p className="eyebrow">Source</p><h2>Full application</h2></div></div>
      <div className="text-tabs">
        <button className={tab === "form" ? "active" : ""} onClick={() => setTab("form")}>Application form</button>
        <button className={tab === "documents" ? "active" : ""} onClick={() => setTab("documents")}>Documents</button>
      </div>
      {tab === "form" && entries.map(([key, value]) => {
        const { nodes, matched } = highlight(String(value), detail.findings);
        return (
          <div className={matched.length ? "form-answer active" : "form-answer"} key={key}>
            <small>{humanise(key)}</small>
            <p>{nodes}</p>
            {matched.map((f) => <a key={f.id} href={`#rule-${f.rule_code}`} onClick={(e) => { e.preventDefault(); document.getElementById(`rule-${f.rule_code}`)?.scrollIntoView({ behavior: "smooth" }); }}>Matches rule {f.rule_code}</a>)}
          </div>
        );
      })}
      {tab === "documents" && detail.documents.map((d) => (
        <div className={openDoc === d.id ? "form-answer active" : "form-answer"} key={d.id} id={`doc-${d.id}`}>
          <small>{DOC_LABEL[d.declared_type] ?? d.declared_type} · {d.file_name}</small>
          {d.verification_notes.length > 0 && (
            <p>{d.verification_notes.map((n) => <span key={n.field} className="check-source">{humanise(n.field)}: <strong>{n.label}</strong>. </span>)}</p>
          )}
          <pre className="doc-text">{d.extracted_text ?? "No text extracted."}</pre>
        </div>
      ))}
      {tab === "documents" && detail.documents.length === 0 && <p className="empty-state">No documents were uploaded.</p>}
      <div className="notice"><Icon name="info" />AI does not score writing style or detect AI-written text.</div>
    </aside>
  );
}

// ---------------------------------------------------------------- page

export default function Review({ id, navigate }: { id: string; navigate: Navigate }) {
  const { data: detail, error, reload } = useLoad(() => api.detail(id), [id]);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [showDecided, setShowDecided] = useState(false);
  const [tab, setTab] = useState<"form" | "documents">("form");
  const [openDoc, setOpenDoc] = useState<string | null>(null);

  if (error) return <main className="page"><ErrorNotice error={error} /></main>;
  if (!detail) return <main className="page"><Loading /></main>;

  const app = detail.application;
  const locked = app.status === "signed_off";
  const findings = [...detail.findings].sort((a, b) => sortKey(a) - sortKey(b));
  const open = findings.filter((f) => !isDecided(f));
  const decided = findings.filter(isDecided);
  const total = findings.length;
  const counts = (s: string) => findings.filter((f) => effectiveStatus(f) === s).length;
  const flags = detail.latest_run?.injection_flags ?? [];

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try { await fn(); reload(); } catch (e) { setActionError(e); } finally { setBusy(false); }
  };
  const confirm = (f: Finding) => void act(() => api.review(f.id, { action: "confirm" }));
  const indexOf = (f: Finding) => detail.findings.findIndex((x) => x.id === f.id) + 1;

  return (
    <main className="review-page">
      <div className="review-top">
        <div className="breadcrumb"><button onClick={() => navigate({ name: "queue" })}><Icon name="left" />Applications</button><span>/</span><span>{app.reference}</span></div>
        <div className="review-heading">
          <div>
            <p className="eyebrow">Application {app.reference} · {app.program_name}</p>
            <h1>{app.applicant_name ?? "Applicant"}</h1>
            <p>Received {formatDate(app.submitted_at)} · {APP_STATUS_LABEL[app.status] ?? app.status}{detail.latest_run ? ` · Rule pack ${detail.latest_run.rule_pack_version}` : ""}</p>
          </div>
          {total > 0 && (
            <div className="progress-block">
              <span><strong>{decided.length} of {total}</strong> rules decided</span>
              <div className="progress"><i style={{ width: `${(decided.length / total) * 100}%` }} /></div>
            </div>
          )}
          <Button onClick={() => navigate({ name: "signoff", id })}>{locked ? "View sign-off" : "Review sign-off"} <Icon name="arrow" /></Button>
        </div>
        {total > 0 && (
          <div className="status-summary" aria-label="Finding summary">
            {(["Met", "Not met", "Unclear", "Needs evidence"] as const).map((s) => <span key={s}><StatusChip status={s} /><strong>{counts(s)}</strong></span>)}
            {counts("Evidence only") > 0 && <span><StatusChip status="Evidence only" /><strong>{counts("Evidence only")}</strong></span>}
            <p>No overall score is calculated.</p>
          </div>
        )}
      </div>

      {flags.length > 0 && (
        <div className="notice warn-notice" role="alert"><Icon name="shield" />
          <span><strong>Instruction-like text was found in this application and was not followed.</strong>{" "}
            {flags.map((fl) => `${humanise(fl.source)}: “${fl.excerpt}”`).join(" · ")}. AI confidence has been lowered; read these answers yourself.</span>
        </div>
      )}
      <ErrorNotice error={actionError} />

      <div className="review-grid">
        <aside className="application-sidebar">
          <div className="section-title"><div><p className="eyebrow">Application</p><h2>Summary & documents</h2></div></div>
          <dl className="summary-list">
            {Object.entries(app.application_text.fields ?? {}).slice(0, 6).map(([k, v]) => <div key={k}><dt>{humanise(k)}</dt><dd>{String(v)}</dd></div>)}
          </dl>
          <div className="side-heading"><h3>Attached documents</h3><span>{detail.documents.length} files</span></div>
          {detail.documents.map((d) => (
            <DocumentCard key={d.id} doc={d} onOpen={() => { setTab("documents"); setOpenDoc(d.id); setTimeout(() => document.getElementById(`doc-${d.id}`)?.scrollIntoView({ behavior: "smooth" }), 50); }} />
          ))}
        </aside>

        <section className="rules-column">
          <div className="column-header"><div><p className="eyebrow">Assessment</p><h2>Review each rule</h2></div><span>Attention first</span></div>
          {app.manual_assessment_requested && (
            <div className="notice"><Icon name="user" />The applicant asked for a person to assess this application. AI assessment will not run.</div>
          )}
          {!detail.latest_run && !app.manual_assessment_requested && (
            <div className="panel empty-state">
              <p>This application has not been checked yet.</p>
              <Button icon="search" disabled={busy || locked} onClick={() => void act(() => api.assess(id))}>{busy ? "Checking…" : "Run AI check"}</Button>
            </div>
          )}
          {total > 0 && <div className="notice blue"><Icon name="info" />Unclear items, missing evidence and unverified findings appear first. Check the applicant’s words before you decide.</div>}
          {open.map((f) => (
            <RuleCard key={f.id} f={f} index={indexOf(f)} total={total} locked={locked} busy={busy} onDialog={setDialog} onConfirm={confirm} />
          ))}
          {decided.length > 0 && !showDecided && (
            <button className="show-more" onClick={() => setShowDecided(true)}>Show {decided.length} decided rule{decided.length === 1 ? "" : "s"} <Icon name="chevron" /></button>
          )}
          {showDecided && decided.map((f) => (
            <RuleCard key={f.id} f={f} index={indexOf(f)} total={total} locked={locked} busy={busy} onDialog={setDialog} onConfirm={confirm} />
          ))}
        </section>

        <ApplicationText detail={detail} tab={tab} setTab={setTab} openDoc={openDoc} />
      </div>

      {dialog && dialog.kind === "ask" && (
        <AskDialog detail={detail} finding={dialog.finding} close={() => setDialog(null)} onDone={() => { setDialog(null); reload(); }} />
      )}
      {dialog && dialog.kind !== "ask" && (
        <DecisionDialog state={dialog} close={() => setDialog(null)} onDone={() => { setDialog(null); reload(); }} />
      )}
    </main>
  );
}
