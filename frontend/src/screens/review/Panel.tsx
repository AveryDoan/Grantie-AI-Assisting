// Side panel for one item: a rule, a document, or a story flag. The tables stay visible beside it.
// Order: A title and source, B what the application says, C flags and language notes, D actions, E the trace link.
// The redaction detail is one more click away (E), never shown here.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { Detail, DocumentRow, Finding } from "../../api";
import { Button, Icon, StatusChip, effectiveStatus, formatDate, humanise } from "../../ui";
import { FlagDetail } from "../Consistency";
import type { DialogState } from "./Dialogs";
import { DOCUMENT_FIELD_LABELS, fieldLabel } from "./labels";
import { compareValues, resolveTarget, searchedSections, sectionAround, toBlocks, type Match } from "./highlight";
import { TextView } from "./TextView";
import { AssessForm } from "./MeritViewer";
import {
  CHECKED_BY_LABEL, DOC_LABEL, SECTION_LABEL, checkedBy, detailsCheck, plainRule, slotCheck, slotFindings, slotStatus,
  type Slot, type StoryRow, TYPE_CHIP,
} from "./model";

export type PanelItem = { kind: "rule"; code: string } | { kind: "doc"; key: string } | { kind: "story"; id: string };

interface Common {
  detail: Detail;
  locked: boolean;
  busy: boolean;
  onDialog: (d: DialogState) => void;
  onConfirm: (f: Finding) => void;
  onChanged: () => void;
  onTrace: (item: string) => void;
}

// ---------------------------------------------------------------- pieces

function Quote({ text, verified, fromDocument }: { text: string; verified: boolean; fromDocument?: boolean }) {
  const where = fromDocument ? "referee letter" : "application";
  return (
    <blockquote className={verified ? "rv-quote" : "rv-quote quote-unverified"}>
      <span className="quote-mark">“</span>{text}
      <footer><Icon name={verified ? "check" : "close"} size={15} />{verified ? `Verified in ${where}` : `Quote not found in the ${where}. Do not rely on it.`}</footer>
    </blockquote>
  );
}

function Tag({ f }: { f: Finding }) {
  return <span className="rv-tag">{CHECKED_BY_LABEL[checkedBy(f)]}</span>;
}

function Section({ letter, title, children }: { letter: string; title: string; children: ReactNode }) {
  return <section className="rv-psec" aria-label={`${letter}. ${title}`}><h3>{title}</h3>{children}</section>;
}

function TraceLink({ item, onTrace }: { item: string; onTrace: (item: string) => void }) {
  return (
    <div className="rv-pfoot">
      <button className="link-button" onClick={() => onTrace(item)}>See how this was redacted and how the AI read it <Icon name="arrow" size={14} /></button>
    </div>
  );
}

// ---------------------------------------------------------------- decisions

function RuleActions({ f, locked, busy, onDialog, onConfirm }: { f: Finding; locked: boolean; busy: boolean; onDialog: (d: DialogState) => void; onConfirm: (f: Finding) => void }) {
  if (locked) return <p className="muted">This application is signed off. Decisions are locked.</p>;
  let confirmLabel = `Confirm ${f.ai_status.toLowerCase()}`;
  let confirmAction = () => onConfirm(f);
  let disabled = false;
  let title: string | undefined;
  if (!f.is_valid || f.error_flag) {
    disabled = true;
    title = "This finding could not be checked, so it cannot be confirmed. Use Override to record your own decision.";
  } else if (f.ai_status === "Not met") {
    confirmAction = () => onDialog({ kind: "confirm-not-met", finding: f });
  }
  return (
    <div className="rule-actions">
      <Button icon="check" disabled={busy || disabled} onClick={confirmAction} title={title}>{confirmLabel}</Button>
      <Button variant="secondary" icon="edit" disabled={busy} onClick={() => onDialog({ kind: "override", finding: f })}>Override</Button>
      <Button variant="quiet" icon="send" disabled={busy} onClick={() => onDialog({ kind: "ask", finding: f })}>Ask applicant</Button>
    </div>
  );
}

function DecisionLine({ f }: { f: Finding }) {
  const r = f.latest_review;
  if (!r) return null;
  return (
    <p className="decision-line">
      {r.action === "ask_applicant"
        ? <>You asked the applicant for more information on {formatDate(r.reviewed_at, true)}.</>
        : <>{f.ai_status === "Evidence only" ? "Officer decided" : <>AI suggested <strong>{f.ai_status}</strong> · Officer decided</>} <strong>{r.final_status}</strong> on {formatDate(r.reviewed_at, true)}{r.reason ? <> – “{r.reason}”</> : null}</>}
    </p>
  );
}

function Notes({ f }: { f: Finding }) {
  const items: string[] = [];
  if (!f.is_valid || f.error_flag) items.push(`Could not assess this item. Please review manually.${f.error_detail ? ` (${f.error_detail.replace(/_/g, " ")})` : ""}`);
  if (f.language_flag) items.push("The wording may be the obstacle. Consider asking the applicant before deciding “Not met”.");
  else if (f.needs_applicant_clarification) items.push("May need clarification from applicant.");
  if (f.confidence && f.check_source === "llm") items.push(`AI confidence: ${humanise(f.confidence)}.`);
  if (!items.length) return <p className="muted">No flags or language notes for this item.</p>;
  return <ul className="cx-list">{items.map((t) => <li key={t}>{t}</li>)}</ul>;
}

// ---------------------------------------------------------------- rule panel

/** "Why this result": the short reason, up front, when the finding has no quote to show. */
function WhyCallout({ f, fromDocuments }: { f: Finding; fromDocuments: boolean }) {
  const judgement = f.ai_status === "Evidence only";
  const reason = judgement ? "Officer judgement required. The AI gives no result for this rule."
    : f.rationale ?? (f.error_flag ? "Could not assess this item. Please review manually." : "No explanation was given.");
  const unaddressed = /does not address/i.test(reason);
  return (
    <section className="rv-why-card" aria-label="Why this result">
      <span className="rv-why-icon" aria-hidden="true"><Icon name="info" size={22} /></span>
      <div>
        <h3>Why this result</h3>
        <p className="rv-why-body">{reason}</p>
        <span className="rv-why-chip">{judgement ? "Officer judgement" : fromDocuments ? "Worked out from documents" : "Worked out from your answers"}</span>
        {unaddressed && <p className="rv-why-extra">Nothing in the form or documents covers this. The officer can ask the applicant for it.</p>}
      </div>
    </section>
  );
}

interface SourceRow { key: string; label: string; typed: string | null; doc: string | null; docName: string | null; match: Match }

function sourceRows(f: Finding, detail: Detail): SourceRow[] {
  const typedAll = { ...(detail.application.application_text.fields ?? {}), ...(detail.application.application_text.answers ?? {}) } as Record<string, string>;
  return (f.rule_sources ?? []).map((r, i) => {
    const typed = r.typed ? (typedAll[r.typed] ?? null) : null;
    const doc = r.document_type ? detail.documents.find((d) => d.declared_type === r.document_type || d.detected_type === r.document_type) : undefined;
    const docValue = doc && r.document_field ? doc.extracted_fields?.[r.document_field] ?? null : null;
    return {
      key: `${r.typed ?? r.document_field}-${i}`, label: r.typed ? fieldLabel(r.typed) : DOCUMENT_FIELD_LABELS[r.document_field ?? ""] ?? fieldLabel(r.document_field ?? ""),
      typed: typed && typed !== "" ? typed : null, doc: docValue, docName: doc ? DOC_LABEL[doc.declared_type] ?? humanise(doc.declared_type) : null,
      match: compareValues(typed, docValue),
    };
  });
}

function SourceTable({ rows }: { rows: SourceRow[] }) {
  if (!rows.length) return null;
  return (
    <div className="rv-src">
      <h4>Where this came from</h4>
      <table className="data-table rv-compare">
        <thead><tr><th>Field name</th><th>What the applicant typed</th><th>What the document says</th><th>Document</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className={r.match === "differs" ? "rv-differs" : ""}>
              <td><strong>{r.label}</strong></td>
              <td>{r.typed ?? <span className="rv-muted">Not provided</span>}</td>
              <td>{r.docName ? (r.doc ?? <span className="rv-muted">Not provided</span>) : <span className="rv-muted">No document needed</span>}
                {r.match === "differs" && <small className="rv-note-differs"><Icon name="question" size={13} />These do not match</small>}</td>
              <td>{r.docName ?? <span className="rv-muted">–</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Side drawer: the part of the form or the document the result came from, with the exact words highlighted. */
function SourceDrawer({ f, detail, onBack }: { f: Finding; detail: Detail; onBack: () => void }) {
  const target = resolveTarget(f, detail);
  const text = target ? detail.source_texts?.[target.source] : null;
  const isForm = target?.source === "application_text";
  const blocks = useMemo(() => (target && text ? (isForm ? sectionAround(toBlocks(text.text), target.start, target.end, 2) : undefined) : undefined), [target, text, isForm]);
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => { back.current?.focus(); }, []);
  return createPortal(
    <div className="rv-drawer-wrap">
      <div className="rv-backdrop" onClick={onBack} aria-hidden="true" />
      <aside className="rv-drawer" role="dialog" aria-modal="true" aria-label="Application text">
        <header>
          <button ref={back} className="link-button" onClick={onBack}><Icon name="left" size={16} />Back to finding</button>
          <p className="eyebrow">{target && text ? text.label : "Application text"}</p>
          <h2>{plainRule(f)}</h2>
        </header>
        <div className="rv-drawer-body">
          {target && text ? (
            <>
              <p className="muted">{isForm ? "The part of the form around this answer. The highlighted words are the source." : "The document. The highlighted words are the source."}</p>
              <TextView text={text.text} only={blocks} highlights={[{ id: "focus", start: target.start, end: target.end, kind: "focus" }]} focusId="focus" />
            </>
          ) : (
            <div className="rv-nomatch">
              <h3>No matching text found</h3>
              <p>Nothing in the application covers this rule. These were searched:</p>
              <ul className="cx-list">{searchedSections(detail).map((l) => <li key={l}>{l}</li>)}</ul>
            </div>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}

function RulePanel({ f, c }: { f: Finding; c: Common }) {
  const [drawer, setDrawer] = useState(false);
  const rows = sourceRows(f, c.detail);
  const quote = f.evidence_quote_restored;
  const fromDocuments = (f.rule_sources ?? []).some((r) => r.document_type);
  return (
    <>
      {!quote && <WhyCallout f={f} fromDocuments={fromDocuments} />}
      <Section letter="A" title="Rule">
        <p className="rv-id">Rule {f.rule_code} · {SECTION_LABEL[f.section ?? "eligibility"]}</p>
        <p className="rv-ptitle">{plainRule(f)}</p>
        <div className="rv-chips"><StatusChip status={effectiveStatus(f)} /><Tag f={f} /></div>
        {f.source_clause && <p className="muted">Source: <button className="link-button" onClick={() => setDrawer(true)}>{f.source_clause}</button></p>}
      </Section>
      <Section letter="B" title="What the application says">
        <SourceTable rows={rows} />
        {quote && (
          <div className="rv-src">
            <h4>Verified quote</h4>
            <Quote text={quote} verified={f.quote_verified} />
            {f.rationale && <p className="rv-reason"><strong>Short reason:</strong> {f.rationale}</p>}
          </div>
        )}
        <button className="button button-secondary rv-showtext" onClick={() => setDrawer(true)}><Icon name="file" size={16} />Show the application text</button>
      </Section>
      <Section letter="C" title="Flags and notes"><Notes f={f} /></Section>
      <Section letter="D" title="Your decision">
        <DecisionLine f={f} />
        {f.ai_status === "Evidence only"
          ? <AssessForm f={f} locked={c.locked} onChanged={c.onChanged} />
          : <RuleActions f={f} locked={c.locked} busy={c.busy} onDialog={c.onDialog} onConfirm={c.onConfirm} />}
      </Section>
      <TraceLink item={`rule:${f.rule_code}`} onTrace={c.onTrace} />
      {drawer && <SourceDrawer f={f} detail={c.detail} onBack={() => setDrawer(false)} />}
    </>
  );
}

// ---------------------------------------------------------------- document panel

function typedVsDocument(detail: Detail, doc: DocumentRow) {
  const typed = new Map(detail.facts.filter((x) => (x.source ?? "application_text") === "application_text").map((x) => [x.fact_key, x.fact_value]));
  return detail.facts.filter((x) => x.source === `document:${doc.id}`).map((x) => {
    const t = typed.get(x.fact_key);
    const same = t != null && t.trim().toLowerCase() === x.fact_value.trim().toLowerCase();
    return { key: x.fact_key, typed: t ?? null, doc: x.fact_value, same };
  });
}

function DocPanel({ slot, c }: { slot: Slot; c: Common }) {
  const d = slot.doc;
  const rules = slotFindings(slot, c.detail.findings);
  const check = slotCheck(slot);
  const rows = d ? typedVsDocument(c.detail, d) : [];
  return (
    <>
      <Section letter="A" title="Document">
        <p className="rv-id">Document slot</p>
        <p className="rv-ptitle">{slot.label}</p>
        <div className="rv-chips"><StatusChip status={slotStatus(slot)} /><span className="rv-tag">Calculated by code</span></div>
        <p className="muted">Source: {d ? <>Uploaded file <strong>{d.file_name}</strong></> : "no file for this slot"}</p>
      </Section>
      <Section letter="B" title="What the application says">
        <dl className="rv-facts">
          <div><dt>In the right slot?</dt><dd>{check}</dd></div>
          <div><dt>Declared type</dt><dd>{d ? DOC_LABEL[d.declared_type] ?? humanise(d.declared_type) : "–"}</dd></div>
          <div><dt>Looks like</dt><dd>{d?.detected_type ? DOC_LABEL[d.detected_type] ?? humanise(d.detected_type) : "–"}</dd></div>
          <div><dt>Details match the form?</dt><dd>{detailsCheck(slot)}</dd></div>
        </dl>
        {slot.commentInstead && <p className="muted">The form has a typed biography, but no headshot file was uploaded.</p>}
        {d && d.verification_notes.length > 0 && (
          <ul className="cx-list">{d.verification_notes.map((n) => <li key={n.field}>{humanise(n.field)}: <strong>{n.label}</strong></li>)}</ul>
        )}
        {rows.length > 0 && (
          <table className="data-table rv-compare">
            <thead><tr><th>Item</th><th>Typed on the form</th><th>In the document</th><th>Match</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.key}><td>{humanise(r.key)}</td><td>{r.typed ?? "–"}</td><td>{r.doc}</td>
                <td>{r.typed == null ? "Not compared" : r.same ? <span className="rv-ok"><Icon name="check" size={14} />Matches</span> : <span className="rv-warn"><Icon name="question" size={14} />Differs</span>}</td></tr>
            ))}</tbody>
          </table>
        )}
        {d && (
          <details className="rv-preview"><summary>Text preview</summary>
            <pre className="doc-text">{d.extracted_text ? d.extracted_text.slice(0, 1500) + (d.extracted_text.length > 1500 ? "…" : "") : "No text could be read from this file. A person needs to read it."}</pre>
          </details>
        )}
        {slot.extraDocs && slot.extraDocs.length > 0 && <p className="muted">{slot.extraDocs.length} more file{slot.extraDocs.length === 1 ? "" : "s"} of this kind: {slot.extraDocs.map((x) => x.file_name).join(", ")}.</p>}
      </Section>
      <Section letter="C" title="Flags and notes">
        {rules.some((f) => f.language_flag || f.needs_applicant_clarification || !f.is_valid || f.error_flag)
          ? rules.map((f) => <Notes key={f.id} f={f} />)
          : <p className="muted">No flags or language notes for this item.</p>}
      </Section>
      <Section letter="D" title="Your decision">
        {rules.length === 0 && <p className="muted">No rule reads this document.</p>}
        {rules.map((f) => (
          <div className="rv-rulebox" key={f.id}>
            <div className="rv-rulebox-head"><strong>Rule {f.rule_code}</strong><StatusChip status={effectiveStatus(f)} /></div>
            <p>{plainRule(f)}</p>
            <p className="rv-reason"><strong>Short reason:</strong> {f.rationale ?? "No explanation was given."}</p>
            <Tag f={f} />
            <DecisionLine f={f} />
            <RuleActions f={f} locked={c.locked} busy={c.busy} onDialog={c.onDialog} onConfirm={c.onConfirm} />
          </div>
        ))}
      </Section>
      {d ? <TraceLink item={`doc:${d.id}`} onTrace={c.onTrace} /> : rules[0] && <TraceLink item={`rule:${rules[0].rule_code}`} onTrace={c.onTrace} />}
    </>
  );
}

// ---------------------------------------------------------------- story panel

function StoryPanel({ row, c }: { row: StoryRow; c: Common }) {
  return (
    <>
      <Section letter="A" title="Signal">
        <p className="rv-id">{TYPE_CHIP[row.type]}{row.linked ? ` · linked to ${row.linked}` : ""}</p>
        <p className="rv-ptitle">{row.check}</p>
        <div className="rv-chips"><span className="rv-tag">{row.strength} signal</span><span className="rv-tag">{row.status}</span></div>
        <p className="muted">A signal, not a finding. It does not change any rule result and does not stop sign-off.</p>
      </Section>
      <Section letter="B" title="What doesn’t fit">
        {row.flags.map((fl, i) => (
          <div key={fl.id} className="rv-rulebox">
            {row.flags.length > 1 && <div className="rv-rulebox-head"><strong>Item {i + 1} of {row.flags.length}</strong><span className="rv-tag">{fl.status === "open" ? "To check" : fl.status === "confirmed" ? "Needs follow-up" : "Dismissed"}</span></div>}
            <FlagDetail flag={fl} onChanged={c.onChanged} locked={c.locked} />
          </div>
        ))}
      </Section>
      <Section letter="C" title="Notes">
        <p className="muted">{row.type === "document_integrity" ? "Scans, re-saves and edits all change file details, so this is a weak signal on its own." : row.type === "cross_application" ? "Families, schools and organisations can legitimately share contacts, addresses and letter templates." : "Check the quotes against the application before you decide."}</p>
      </Section>
      <TraceLink item={`flag:${row.flags[0].id}`} onTrace={c.onTrace} />
    </>
  );
}

// ---------------------------------------------------------------- panel shell

export function ReviewPanel({ item, slots, story, onClose, ...c }: Common & { item: PanelItem; slots: Slot[]; story: StoryRow[]; onClose: () => void }) {
  const head = useRef<HTMLDivElement>(null);
  const key = item.kind === "rule" ? item.code : item.kind === "doc" ? item.key : item.id;
  useEffect(() => { head.current?.focus(); }, [key]);
  let body: ReactNode = null;
  let title = "";
  if (item.kind === "rule") {
    const f = c.detail.findings.find((x) => x.rule_code === item.code);
    title = f ? `Rule ${f.rule_code}` : "Item";
    body = f ? <RulePanel f={f} c={c} /> : <p className="muted">This item is no longer available.</p>;
  } else if (item.kind === "doc") {
    const s = slots.find((x) => x.key === item.key);
    title = s?.label ?? "Document";
    body = s ? <DocPanel slot={s} c={c} /> : <p className="muted">This item is no longer available.</p>;
  } else {
    const r = story.find((x) => x.id === item.id);
    title = r?.check ?? "Story flag";
    body = r ? <StoryPanel row={r} c={c} /> : <p className="muted">This item is no longer available.</p>;
  }
  return (
    <aside className="rv-panel" aria-label={`Details: ${title}`}>
      <div className="rv-panel-head">
        <div ref={head} tabIndex={-1}><p className="eyebrow">Details</p><h2>{title}</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="Close details (Esc)"><Icon name="close" /></button>
      </div>
      <div className="rv-panel-body">{body}</div>
    </aside>
  );
}
