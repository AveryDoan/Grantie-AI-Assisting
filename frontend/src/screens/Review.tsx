// Application review: a tab bar (Overview, Documents, Eligibility, Merit, Consistency of information, Linked applications) and
// one row per item. Nothing is shown in detail until a row is opened; details open in a side panel so the table stays visible.
// How an item was redacted and read by the AI is one more click (the trace, scoped to that item).
// No overall score, ranking or recommendation is shown anywhere. The merit marks belong to the officer and are never totalled.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type AIStatus, type Finding, type OverviewRow } from "../api";
import type { Navigate } from "../App";
import { APP_STATUS_LABEL, Button, ErrorNotice, Icon, Loading, StatusChip, effectiveStatus, formatDate, humanise, isDecided, useLoad } from "../ui";
import { ConsistencyPanel, ConsistencyTable } from "./review/ConsistencyTab";
import { AskDialog, DecisionDialog, type DialogState } from "./review/Dialogs";
import { LinkedTab } from "./review/LinkedTab";
import { CRITERIA, MeritViewer } from "./review/MeritViewer";
import { Badge, DecisionCell, Row } from "./review/parts";
import { ReviewPanel } from "./review/Panel";
import {
  CHECKED_BY_LABEL, NEED_LABEL, buildSlots, byAttention, checkedBy, decisionText, detailsCheck, plainRule, slotCheck, slotDecision, slotFindings,
  slotNeed, slotSort, slotStatus, type Slot,
} from "./review/model";

const TABS = [
  { id: "overview", title: "Overview" },
  { id: "documents", title: "Documents" },
  { id: "eligibility", title: "Eligibility" },
  { id: "merit", title: "Merit" },
  { id: "consistency", title: "Consistency of information" },
  { id: "linked", title: "Linked applications" },
] as const;
type TabId = (typeof TABS)[number]["id"];

const STRIP: { status: AIStatus; label: string }[] = [
  { status: "Met", label: "Met" }, { status: "Not met", label: "Not met" }, { status: "Unclear", label: "Unclear" },
  { status: "Needs evidence", label: "Needs evidence" }, { status: "Evidence only", label: "Officer judgement" },
];

// ---------------------------------------------------------------- tables

function DocumentsTable({ slots, findings, selectedKey, onOpen }: { slots: Slot[]; findings: Finding[]; selectedKey: string | null; onOpen: (s: Slot) => void }) {
  const attention = slots.filter((s) => slotNeed(s).level === "attention").length;
  return (
    <>
      <p className={`rv-counter ${attention ? "on" : ""}`} role="status">
        <Icon name={attention ? "info" : "check"} size={16} />
        {attention === 0 ? "No documents need attention" : `${attention} ${attention === 1 ? "document needs" : "documents need"} attention`}
      </p>
      <div className="rv-table-wrap" role="region" aria-label="Documents" tabIndex={-1}>
        <table className="rv-table">
          <thead><tr><th>Document</th><th className="col-low">File name</th><th>In the right slot?</th><th className="col-low">Details match the form?</th><th>Need</th><th>Status</th><th>Decision</th></tr></thead>
          <tbody>
            {slots.map((s) => {
              const check = slotCheck(s);
              const details = detailsCheck(s);
              const need = slotNeed(s);
              const bad = check !== "Right document" && check !== "None uploaded";
              return (
                <Row key={s.key} rowKey={`doc:${s.key}`} selected={selectedKey === `doc:${s.key}`} onOpen={() => onOpen(s)} className={`need-${need.level}`}
                  label={`${s.label}. ${NEED_LABEL[need.level]}${need.reason ? `. ${need.reason}` : ""}. Open details`}>
                  <td><strong>{s.label}</strong>{need.reason && <small className="rv-reasonline">{need.reason}</small>}</td>
                  <td className="col-low rv-file">{s.doc ? s.doc.file_name : "–"}</td>
                  <td><span className={bad ? "rv-warn" : "rv-ok"}><Icon name={bad ? "question" : "check"} size={14} />{check}</span></td>
                  <td className="col-low">{details}</td>
                  <td><span className={`rv-need lvl-${need.level}`}><Icon name={need.level === "ok" ? "check" : need.level === "check" ? "search" : "info"} size={14} />{NEED_LABEL[need.level]}</span></td>
                  <td><StatusChip status={slotStatus(s)} /></td>
                  <td><DecisionCell text={slotDecision(s, findings)} /></td>
                </Row>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function RuleRows({ list, selectedKey, onOpen }: { list: Finding[]; selectedKey: string | null; onOpen: (f: Finding) => void }) {
  return (
    <>
      {list.map((f) => (
        <Row key={f.id} rowKey={`rule:${f.rule_code}`} selected={selectedKey === `rule:${f.rule_code}`} onOpen={() => onOpen(f)} label={`Rule ${f.rule_code}. ${plainRule(f)}. Open details`}>
          <td className="rv-id-cell">{f.rule_code}</td>
          <td className="rv-oneline">{plainRule(f)}</td>
          <td><StatusChip status={effectiveStatus(f)} /></td>
          <td className="col-low"><span className="rv-tag">{CHECKED_BY_LABEL[checkedBy(f)]}</span></td>
          <td><DecisionCell text={decisionText(f)} /></td>
        </Row>
      ))}
    </>
  );
}

function EligibilityTable({ list, filter, selectedKey, onOpen }: { list: Finding[]; filter: AIStatus | null; selectedKey: string | null; onOpen: (f: Finding) => void }) {
  const [showMet, setShowMet] = useState(false);
  const shown = filter ? list.filter((f) => effectiveStatus(f) === filter) : list;
  const attention = shown.filter((f) => effectiveStatus(f) !== "Met").sort(byAttention);
  const met = shown.filter((f) => effectiveStatus(f) === "Met").sort(byAttention);
  const metOpen = showMet || filter === "Met";
  if (!shown.length) return <p className="empty-state">No rules match this filter.</p>;
  return (
    <div className="rv-table-wrap" role="region" aria-label="Eligibility rules" tabIndex={-1}>
      <table className="rv-table">
        <thead><tr><th>Rule</th><th>Rule in plain words</th><th>Status</th><th className="col-low">Checked by</th><th>Decision</th></tr></thead>
        <tbody>
          <RuleRows list={attention} selectedKey={selectedKey} onOpen={onOpen} />
          {met.length > 0 && (
            <tr className="rv-group-row"><td colSpan={5}>
              <button className="rv-group-toggle" aria-expanded={metOpen} onClick={() => setShowMet(!showMet)} disabled={filter === "Met"}>
                <span className={metOpen ? "rotate" : ""}><Icon name="chevron" size={16} /></span>{met.length} Met
              </button>
            </td></tr>
          )}
          {metOpen && <RuleRows list={met} selectedKey={selectedKey} onOpen={onOpen} />}
        </tbody>
      </table>
    </div>
  );
}

const criterionName = (f: Finding) => CRITERIA[f.rule_code] ?? plainRule(f);

function MeritRow({ f, selected, onOpen }: { f: Finding; selected: boolean; onOpen: () => void }) {
  const m = f.merit_mark;
  const state = m ? (m.not_assessed ? "Not assessed" : `Marked ${m.mark} out of 100`) : "Not marked";
  return (
    <Row rowKey={`rule:${f.rule_code}`} selected={selected} onOpen={onOpen} label={`${criterionName(f)}. View detail`}>
      <td><strong>{criterionName(f)}</strong></td>
      <td><DecisionCell text={state} /></td>
      <td className="rv-muted">{m?.reason ?? "–"}</td>
      <td><span className="link-button">View detail <Icon name="arrow" size={14} /></span></td>
    </Row>
  );
}

/** The officer's own marks. They are set in the detail window; nothing is suggested, totalled or compared. */
function MeritTable({ list, selectedKey, onOpen }: {
  list: Finding[]; selectedKey: string | null; onOpen: (f: Finding, referee?: boolean) => void;
}) {
  return (
    <>
      <p className="rv-judgement-line"><Icon name="eye" size={16} />Officer judgement required. No AI mark or suggestion. Marks are not added up or compared.</p>
      <div className="rv-table-wrap" role="region" aria-label="Merit criteria" tabIndex={-1}>
        <table className="rv-table rv-merit">
          <thead><tr><th>Criterion</th><th>Officer decision</th><th>Reason for mark</th><th><span className="sr-only">Action</span></th></tr></thead>
          <tbody>
            {list.flatMap((f) => {
              const rows = [<MeritRow key={f.id} f={f} selected={selectedKey === `rule:${f.rule_code}`} onOpen={() => onOpen(f)} />];
              if (f.rule_code === "M2") {
                rows.push(
                  <Row key={`${f.id}-ref`} rowKey="referee" selected={selectedKey === "referee"} onOpen={() => onOpen(f, true)} label="Referee check. View detail">
                    <td><strong>Referee check</strong><small className="rv-sub">Is the referee identified and contactable?</small></td>
                    <td><span className="rv-decision">Check only. Not marked</span></td>
                    <td className="rv-muted">–</td>
                    <td><span className="link-button">View detail <Icon name="arrow" size={14} /></span></td>
                  </Row>,
                );
              }
              return rows;
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- page

type Open = { kind: "rule"; code: string } | { kind: "doc"; key: string } | { kind: "referee" } | { kind: "cons"; id: string };

function OverviewCard({ title, lines, onGo, badge }: { title: string; lines: string[]; onGo: () => void; badge?: number | null }) {
  return (
    <button className="rv-ocard" onClick={onGo}>
      <span className="rv-ocard-head"><strong>{title}</strong>{badge ? <Badge n={badge} label="to look at" /> : null}</span>
      {lines.map((l) => <span key={l} className="rv-ocard-line">{l}</span>)}
      <span className="rv-ocard-go">Open <Icon name="arrow" size={14} /></span>
    </button>
  );
}

export interface Embedded { onNext: () => void; onChanged: () => void; nextReady: boolean }

export default function Review({ id, navigate, embedded }: { id: string; navigate: Navigate; embedded?: Embedded }) {
  const { data: detail, error, loading, reload: reloadDetail } = useLoad(() => api.detail(id), [id]);
  const reload = () => { reloadDetail(); embedded?.onChanged(); };
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [item, setItem] = useState<Open | null>(null);
  const [filter, setFilter] = useState<AIStatus | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  const lastRow = useRef<string | null>(null);

  const closePanel = useCallback(() => {
    setItem(null);
    const key = lastRow.current;
    if (key) setTimeout(() => document.querySelector<HTMLElement>(`[data-row-key="${CSS.escape(key)}"]`)?.focus(), 0);
  }, []);

  // Keyboard: arrows move between rows, Esc closes the panel (a dialog or the source drawer handles its own Esc first).
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && item && !dialog) { e.preventDefault(); closePanel(); return; }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const el = document.activeElement as HTMLElement | null;
      if (!el?.hasAttribute("data-row")) return;
      const rows = [...document.querySelectorAll<HTMLElement>("[data-row]")];
      const next = rows[rows.indexOf(el) + (e.key === "ArrowDown" ? 1 : -1)];
      if (next) { e.preventDefault(); next.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [item, dialog, closePanel]);

  const model = useMemo(() => {
    if (!detail) return null;
    const findings = detail.findings;
    return {
      slots: buildSlots(detail),
      eligibility: findings.filter((f) => f.section === "eligibility" || f.section === null),
      merit: findings.filter((f) => f.section === "merit").sort((a, b) => a.rule_code.localeCompare(b.rule_code, undefined, { numeric: true })),
      documentRules: findings.filter((f) => f.section === "documents"),
      overview: (detail.consistency?.overview ?? []) as OverviewRow[],
    };
  }, [detail]);

  if (error) return <main className="page"><ErrorNotice error={error} /></main>;
  if (!detail || !model) return <main className="page"><Loading /></main>;

  const app = detail.application;
  const locked = app.status === "signed_off";
  const total = detail.findings.length;
  const decided = detail.findings.filter(isDecided).length;
  const allDecided = total > 0 && decided === total;
  const count = (s: AIStatus) => detail.findings.filter((f) => effectiveStatus(f) === s).length;
  const injection = detail.latest_run?.injection_flags ?? [];
  const consistencyOn = detail.consistency?.enabled;
  const differs = model.overview.filter((r) => r.result === "Differs");
  const toCheck = differs.filter((r) => r.decision === "Not decided").length;
  const linked = detail.linked_applications ?? [];
  const undecided = (list: Finding[]) => list.filter((f) => !isDecided(f)).length;
  const attention = model.slots.filter((s) => slotNeed(s).level === "attention").length;
  const marked = model.merit.filter((f) => f.merit_mark).length;
  const notChecked = !detail.latest_run && !app.manual_assessment_requested;

  const locking = busy || loading;
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setActionError(null);
    try { await fn(); reload(); } catch (e) { setActionError(e); } finally { setBusy(false); }
  };
  const confirm = (f: Finding) => void act(() => api.review(f.id, { action: "confirm" }));

  const openItem = (next: Open, rowKey: string) => { lastRow.current = rowKey; setItem(next); };
  const selectedKey = !item ? null : item.kind === "rule" ? `rule:${item.code}` : item.kind === "doc" ? `doc:${item.key}` : item.kind === "referee" ? "referee" : `cons:${item.id}`;
  const goTab = (t: TabId) => { setItem(null); setTab(t); };
  const scopedTrace = (target: string) => navigate({ name: "trace", id, item: target });

  const merited = item?.kind === "rule" ? detail.findings.find((x) => x.rule_code === item.code && x.section === "merit") ?? null : null;
  const viewer = item?.kind === "referee" ? detail.findings.find((x) => x.rule_code === "M2") ?? null : merited;
  const sideItem = item && !viewer && item.kind !== "cons" && item.kind !== "referee" ? item : null;
  const consRow = item?.kind === "cons" ? model.overview.find((r) => r.id === item.id) ?? null : null;
  const sideOpen = Boolean(sideItem || consRow);

  const slotsShown = [...model.slots]
    .filter((s) => !filter || slotFindings(s, detail.findings).some((f) => effectiveStatus(f) === filter))
    .sort((a, b) => slotSort(a, b, detail.findings));
  const hasDocuments = model.documentRules.length > 0 || detail.documents.length > 0;

  const badge = (t: TabId): number | null =>
    t === "documents" ? attention : t === "eligibility" ? undecided(model.eligibility) : t === "merit" ? model.merit.length - marked
      : t === "consistency" ? toCheck : t === "linked" ? (linked.length || null) : null;
  const badgeLabel: Record<TabId, string> = { overview: "", documents: "need attention", eligibility: "not decided", merit: "not marked", consistency: "to check", linked: "linked applications" };

  return (
    <main className={`review-page rv-page ${sideOpen ? "has-panel" : ""}`}>
      <div className="review-top rv-top">
        <div className="breadcrumb"><button onClick={() => navigate({ name: "queue" })}><Icon name="left" />Applications</button><span>/</span><span>{app.reference}</span></div>
        <div className="review-heading rv-heading">
          <div>
            <p className="eyebrow">Application {app.reference} · {app.program_name}</p>
            <h1>{app.applicant_name ?? "Applicant"}</h1>
            <p>Received {formatDate(app.submitted_at)} · {APP_STATUS_LABEL[app.status] ?? app.status}{detail.latest_run ? ` · Rule pack ${detail.latest_run.rule_pack_version}` : ""}</p>
          </div>
          {total > 0 && (
            <div className="progress-block">
              <span><strong>{decided} of {total}</strong> rules decided</span>
              <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={decided} aria-label="Rules decided"><i style={{ width: `${(decided / total) * 100}%` }} /></div>
            </div>
          )}
          <div className="review-heading-actions">
            <Button variant="secondary" icon="shield" onClick={() => navigate({ name: "trace", id })}>Redaction & AI trace</Button>
            <span className="rv-tip-wrap">
              <Button disabled={!locked && !allDecided} onClick={() => (embedded ? embedded.onNext() : navigate({ name: "signoff", id }))}
                title={!locked && !allDecided ? `Decide every rule and mark every merit criterion first: ${total - decided} still to do.` : undefined}>
                {locked ? "View outcome" : embedded ? "Next step: Outcome" : "Review sign-off"} <Icon name="arrow" />
              </Button>
              {!locked && !allDecided && total > 0 && <small className="rv-why" id="signoff-why">Unlocks when all {total} items are decided or marked ({total - decided} to go).</small>}
            </span>
          </div>
        </div>
        {total > 0 && (
          <div className="rv-strip" aria-label="Rule status counts. Select one to filter the tables.">
            {STRIP.map(({ status, label }) => {
              const on = filter === status;
              return (
                <button key={status} className={`rv-count ${on ? "on" : ""}`} aria-pressed={on} onClick={() => { setFilter(on ? null : status); if (!on) goTab("eligibility"); }}
                  title={on ? "Show all" : `Show only: ${label}`}>
                  {status === "Evidence only" ? <span className="judgement-chip"><Icon name="eye" size={15} />{label}</span> : <StatusChip status={status} />}
                  <strong>{count(status)}</strong>
                </button>
              );
            })}
            {consistencyOn && (
              <button className="rv-flags-chip" onClick={() => goTab("consistency")} title="Consistency items are separate from the rule counts">
                <Icon name="info" size={14} />Consistency items to check: <strong>{toCheck}</strong>
              </button>
            )}
            <p>No overall score is calculated.</p>
          </div>
        )}
        {filter && (
          <p className="rv-filterbar" role="status">Showing only <strong>{filter === "Evidence only" ? "Officer judgement" : filter}</strong>.{" "}
            <button className="link-button" onClick={() => setFilter(null)}>Show all</button></p>
        )}
      </div>
      <nav className="rv-nav" aria-label="Application sections">
        <div role="tablist" className="rv-tablist">
          {TABS.map((t) => {
            const n = badge(t.id);
            return (
              <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""} onClick={() => goTab(t.id)}>
                <span>{t.title}</span>{n ? <Badge n={n} label={badgeLabel[t.id]} /> : null}
              </button>
            );
          })}
        </div>
        <small className="rv-hints" aria-label="Keyboard shortcuts"><kbd>↑</kbd><kbd>↓</kbd> move between rows · <kbd>Enter</kbd> open · <kbd>Esc</kbd> close</small>
      </nav>

      <div className="rv-layout">
        <div className="rv-main">
          {injection.length > 0 && (
            <div className="notice warn-notice" role="alert"><Icon name="shield" />
              <span><strong>Instruction-like text was found in this application and was not followed.</strong>{" "}
                {injection.map((fl) => `${humanise(fl.source)}: “${fl.excerpt}”`).join(" · ")}. AI confidence has been lowered; read these answers yourself.</span>
            </div>
          )}
          <ErrorNotice error={actionError} />
          {app.manual_assessment_requested && <div className="notice"><Icon name="user" />The applicant asked for a person to assess this application. AI assessment will not run.</div>}
          {notChecked && (
            <div className="panel empty-state">
              <p>This application has not been checked yet. The check redacts personal details first, then asks the AI.</p>
              <Button icon="search" disabled={busy || locked} onClick={() => void act(() => api.assess(id))}>{busy ? "Checking…" : "Run AI check"}</Button>
              <Button variant="quiet" icon="shield" onClick={() => navigate({ name: "trace", id })}>See the redacted text first</Button>
            </div>
          )}

          {tab === "overview" && (
            <section className="rv-section" aria-label="Overview">
              <div className="rv-ocards">
                <OverviewCard title="Documents" badge={attention} onGo={() => goTab("documents")}
                  lines={[attention ? `${attention} ${attention === 1 ? "document needs" : "documents need"} attention` : "No documents need attention"]} />
                <OverviewCard title="Eligibility" badge={undecided(model.eligibility)} onGo={() => goTab("eligibility")}
                  lines={[`${count("Unclear")} unclear · ${count("Needs evidence")} need evidence · ${count("Not met")} not met`]} />
                <OverviewCard title="Merit" badge={model.merit.length - marked} onGo={() => goTab("merit")}
                  lines={[`${marked} of ${model.merit.length} criteria marked or set to Not assessed`, "Marks are yours. Nothing is added up or compared."]} />
                <OverviewCard title="Consistency of information" badge={toCheck} onGo={() => goTab("consistency")}
                  lines={consistencyOn ? [`${differs.length} ${differs.length === 1 ? "difference" : "differences"} to check · ${model.overview.filter((r) => r.result === "Cannot compare").length} could not be compared`] : ["Switched off for this site"]} />
                <OverviewCard title="Linked applications" badge={linked.length} onGo={() => goTab("linked")}
                  lines={[linked.length ? `${linked.length} ${linked.length === 1 ? "application shares" : "applications share"} something with this one` : "No linked applications found."]} />
              </div>
            </section>
          )}

          {tab === "documents" && (
            <section className="rv-section" aria-label="Documents">
              <div className="rv-section-body">
                <p className="muted rv-hint">Check these first: right document, in the right place.</p>
                {!hasDocuments ? <p className="empty-state">This grant does not ask for documents.</p>
                  : slotsShown.length === 0 ? <p className="empty-state">No documents match this filter.</p>
                  : <DocumentsTable slots={slotsShown} findings={detail.findings} selectedKey={selectedKey} onOpen={(s) => openItem({ kind: "doc", key: s.key }, `doc:${s.key}`)} />}
              </div>
            </section>
          )}

          {tab === "eligibility" && (
            <section className="rv-section" aria-label="Eligibility">
              <div className="rv-section-body">
                <div className="rv-filters" role="group" aria-label="Filter eligibility rules">
                  {(["Unclear", "Needs evidence", "Not met", "Met"] as AIStatus[]).map((s) => {
                    const n = model.eligibility.filter((f) => effectiveStatus(f) === s).length;
                    return <button key={s} className={`rv-filter ${filter === s ? "on" : ""}`} aria-pressed={filter === s} onClick={() => setFilter(filter === s ? null : s)}>{s} <strong>{n}</strong></button>;
                  })}
                </div>
                {model.eligibility.length === 0
                  ? <p className="empty-state">{notChecked ? "Not checked yet. Run the AI check to see the rules." : "No eligibility rules."}</p>
                  : <EligibilityTable list={model.eligibility} filter={filter} selectedKey={selectedKey} onOpen={(f) => openItem({ kind: "rule", code: f.rule_code }, `rule:${f.rule_code}`)} />}
              </div>
            </section>
          )}

          {tab === "merit" && (
            <section className="rv-section" aria-label="Merit">
              <div className="rv-section-body">
                {model.merit.length === 0 ? <p className="empty-state">{notChecked ? "Not checked yet." : "This grant has no merit criteria."}</p>
                  : <MeritTable list={model.merit} selectedKey={selectedKey}
                      onOpen={(f, referee) => openItem(referee ? { kind: "referee" } : { kind: "rule", code: f.rule_code }, referee ? "referee" : `rule:${f.rule_code}`)} />}
              </div>
            </section>
          )}

          {tab === "consistency" && (
            <section className="rv-section" aria-label="Consistency of information">
              <div className="rv-section-body">
                <p className="rv-subtitle">Checks whether the details match across the form, the documents and the timeline.
                  <span className="rv-tip-wrap">
                    <button className="icon-button rv-info" aria-label="About consistency results" aria-describedby="rv-tip-cons"><Icon name="info" size={16} /></button>
                    <span role="tooltip" id="rv-tip-cons" className="rv-tip">A difference is something to check, not a conclusion. Results never change a rule result and never block sign-off.</span>
                  </span>
                </p>
                {consistencyOn === false ? <p className="empty-state">The consistency check is switched off for this site.</p>
                  : detail.consistency?.unavailable ? <p className="empty-state">Could not load the consistency results. Please review manually.</p>
                  : model.overview.length === 0 ? <p className="empty-state">{notChecked ? "Not checked yet." : "No comparisons to show for this application."}</p>
                  : <ConsistencyTable rows={model.overview} selectedKey={selectedKey} onOpen={(r) => openItem({ kind: "cons", id: r.id }, `cons:${r.id}`)} />}
              </div>
            </section>
          )}

          {tab === "linked" && (
            <section className="rv-section" aria-label="Linked applications">
              <div className="rv-section-body"><LinkedTab rows={linked} onOpen={(other) => navigate({ name: "review", id: other })} /></div>
            </section>
          )}
        </div>

        {sideItem && (sideItem.kind === "rule" || sideItem.kind === "doc") && (
          <ReviewPanel item={sideItem} slots={model.slots} detail={detail} locked={locked} busy={locking} onClose={closePanel}
            onDialog={setDialog} onConfirm={confirm} onChanged={reload} onTrace={scopedTrace} />
        )}
        {consRow && (
          <ConsistencyPanel row={consRow} detail={detail} flags={detail.consistency?.flags ?? []} locked={locked} onChanged={reload} onClose={closePanel} onTrace={scopedTrace} />
        )}
      </div>

      {viewer && (
        <MeritViewer f={viewer} detail={detail} mode={item?.kind === "referee" ? "referee" : "criterion"} locked={locked} onClose={closePanel} onChanged={reload} onTrace={scopedTrace} />
      )}

      {dialog && dialog.kind === "ask" && (
        <AskDialog detail={detail} finding={dialog.finding} close={() => setDialog(null)} onDone={() => { setDialog(null); reload(); }} />
      )}
      {dialog && dialog.kind !== "ask" && (
        <DecisionDialog state={dialog} close={() => setDialog(null)} onDone={() => { setDialog(null); reload(); }} />
      )}
    </main>
  );
}
