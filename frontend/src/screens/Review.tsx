// Application review: a calm overview first. Four collapsible sections (Documents, Eligibility, Merit criteria,
// Does the story add up?), one row per item. Nothing is shown in detail until a row is opened; details open in a
// side panel so the tables stay visible. How an item was redacted and read by the AI is one more click away.
// No overall score, ranking or recommendation is shown anywhere on this screen.
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { api, type AIStatus, type Finding } from "../api";
import type { Navigate } from "../App";
import { APP_STATUS_LABEL, Button, ErrorNotice, Icon, Loading, StatusChip, effectiveStatus, formatDate, humanise, isDecided, useLoad } from "../ui";
import { AskDialog, DecisionDialog, type DialogState } from "./review/Dialogs";
import { MeritViewer } from "./review/MeritViewer";
import { ReviewPanel, type PanelItem } from "./review/Panel";
import {
  CHECKED_BY_LABEL, NEED_LABEL, TYPE_CHIP, TYPE_ORDER, buildSlots, buildStoryRows, byAttention, checkedBy, decisionText, detailsCheck,
  openFlagCount, plainRule, slotCheck, slotDecision, slotFindings, slotSort, slotNeed, slotStatus, storyRowOf, type Slot, type StoryRow,
} from "./review/model";

const SECTIONS = [
  { id: "documents", title: "Documents" },
  { id: "eligibility", title: "Eligibility" },
  { id: "merit", title: "Merit criteria" },
  { id: "story", title: "Does the story add up?" },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];

const STRIP: { status: AIStatus; label: string }[] = [
  { status: "Met", label: "Met" }, { status: "Not met", label: "Not met" }, { status: "Unclear", label: "Unclear" },
  { status: "Needs evidence", label: "Needs evidence" }, { status: "Evidence only", label: "Officer judgement" },
];

// ---------------------------------------------------------------- small pieces

function Badge({ n, label }: { n: number; label: string }) {
  return <span className="rv-badge" aria-label={`${n} ${label}`}>{n}</span>;
}

function DecisionCell({ text }: { text: string }) {
  const done = text === "Confirmed" || text === "Overridden" || text === "Recorded";
  return <span className={done ? "rv-decision done" : "rv-decision"}><Icon name={done ? "check" : "clock"} size={14} />{text}</span>;
}

/** One row in any table: focusable, opens the side panel on click or Enter. */
function Row({ rowKey, selected, onOpen, label, children, className }: {
  rowKey: string; selected: boolean; onOpen: () => void; label: string; children: ReactNode; className?: string;
}) {
  const onKey = (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onOpen(); }
  };
  return (
    <tr data-row data-row-key={rowKey} tabIndex={0} aria-selected={selected} aria-label={label} className={`rv-row ${selected ? "selected" : ""} ${className ?? ""}`}
      onClick={onOpen} onKeyDown={onKey}>{children}</tr>
  );
}

function Block({ id, title, count, countLabel, open, onToggle, aside, children }: {
  id: SectionId; title: string; count: number; countLabel: string; open: boolean; onToggle: () => void; aside?: ReactNode; children: ReactNode;
}) {
  return (
    <section id={`sec-${id}`} className="rv-section" aria-labelledby={`h-${id}`}>
      <div className="rv-section-head">
        <button className="rv-toggle" onClick={onToggle} aria-expanded={open} aria-controls={`body-${id}`}>
          <span className={open ? "rotate" : ""}><Icon name="chevron" /></span>
          <h2 id={`h-${id}`}>{title}</h2>
          <Badge n={count} label={countLabel} />
        </button>
        {aside}
      </div>
      {open && <div id={`body-${id}`} className="rv-section-body">{children}</div>}
    </section>
  );
}

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

function RuleRows({ list, selectedKey, onOpen, merit }: { list: Finding[]; selectedKey: string | null; onOpen: (f: Finding) => void; merit?: boolean }) {
  return (
    <>
      {list.map((f) => {
        const noted = Boolean(f.latest_review?.reason);
        return (
          <Row key={f.id} rowKey={`rule:${f.rule_code}`} selected={selectedKey === `rule:${f.rule_code}`} onOpen={() => onOpen(f)} label={`Rule ${f.rule_code}. ${plainRule(f)}. Open details`}>
            {merit ? (
              <>
                <td><strong>{criterionName(f)}</strong><small className="rv-sub">Rule {f.rule_code}</small></td>
                <td><StatusChip status={f.ai_status === "Evidence only" ? "Evidence only" : effectiveStatus(f)} /></td>
                <td>{noted ? "Recorded" : "Not recorded"}</td>
                <td><DecisionCell text={decisionText(f)} /></td>
              </>
            ) : (
              <>
                <td className="rv-id-cell">{f.rule_code}</td>
                <td className="rv-oneline">{plainRule(f)}</td>
                <td><StatusChip status={effectiveStatus(f)} /></td>
                <td className="col-low"><span className="rv-tag">{CHECKED_BY_LABEL[checkedBy(f)]}</span></td>
                <td><DecisionCell text={decisionText(f)} /></td>
              </>
            )}
          </Row>
        );
      })}
    </>
  );
}

const CRITERIA: Record<string, string> = { M1: "Academic Merit", M2: "Supporting Evidence", M3: "Leadership", M4: "Community Engagement", M5: "Short answer" };
const criterionName = (f: Finding) => CRITERIA[f.rule_code] ?? plainRule(f);

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

function MeritTable({ list, selectedKey, onOpen }: { list: Finding[]; selectedKey: string | null; onOpen: (f: Finding) => void }) {
  return (
    <>
      <p className="rv-judgement-line"><Icon name="eye" size={16} />Officer judgement required. No AI score or suggestion.</p>
      <div className="rv-table-wrap" role="region" aria-label="Merit criteria" tabIndex={-1}>
        <table className="rv-table">
          <thead><tr><th>Criterion</th><th>Status</th><th>Officer note</th><th>Decision</th></tr></thead>
          <tbody><RuleRows list={list} selectedKey={selectedKey} onOpen={onOpen} merit /></tbody>
        </table>
      </div>
    </>
  );
}

function StoryTable({ rows, selectedKey, onOpen }: { rows: StoryRow[]; selectedKey: string | null; onOpen: (r: StoryRow) => void }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (id: string) => setExpanded((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const line = (r: StoryRow, child?: boolean) => (
    <Row key={r.id} rowKey={`story:${r.id}`} selected={selectedKey === `story:${r.id}`} onOpen={() => onOpen(r)} className={child ? "rv-child" : ""}
      label={`${r.check}. ${r.status}. Open details`}>
      <td>{child ? "" : <strong>{r.check}</strong>}{!child && <small className="rv-sub">{TYPE_CHIP[r.type]}</small>}</td>
      <td className="rv-oneline">
        {r.text}
        {r.flags.length > 1 && !child && (
          <button className="rv-expander" aria-expanded={expanded.has(r.id)} onClick={(e) => { e.stopPropagation(); toggle(r.id); }}>
            {r.flags.length} items <span className={expanded.has(r.id) ? "rotate" : ""}><Icon name="chevron" size={14} /></span>
          </button>
        )}
      </td>
      <td className="col-low">{r.linked ?? "–"}</td>
      <td>{r.strength}</td>
      <td><span className="rv-tag">{r.status}</span></td>
    </Row>
  );
  return (
    <div className="rv-table-wrap" role="region" aria-label="Story flags" tabIndex={-1}>
      <table className="rv-table">
        <thead><tr><th>Check</th><th>What doesn’t fit</th><th className="col-low">Linked to</th><th>Signal</th><th>Status</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <FragmentRows key={r.id}>
              {line(r)}
              {r.flags.length > 1 && expanded.has(r.id) && r.flags.map((f) => line(storyRowOf(f), true))}
            </FragmentRows>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function FragmentRows({ children }: { children: ReactNode }) { return <>{children}</>; }

// ---------------------------------------------------------------- page

export default function Review({ id, navigate }: { id: string; navigate: Navigate }) {
  const { data: detail, error, loading, reload } = useLoad(() => api.detail(id), [id]);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [item, setItem] = useState<PanelItem | null>(null);
  const [filter, setFilter] = useState<AIStatus | null>(null);
  const [open, setOpen] = useState<Record<SectionId, boolean>>({ documents: true, eligibility: true, merit: true, story: false });
  const lastRow = useRef<string | null>(null);

  const closePanel = useCallback(() => {
    setItem(null);
    const key = lastRow.current;
    if (key) setTimeout(() => document.querySelector<HTMLElement>(`[data-row-key="${CSS.escape(key)}"]`)?.focus(), 0);
  }, []);

  // Keyboard: arrows move between rows, Esc closes the panel (a dialog handles its own Esc first).
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
    const slots = buildSlots(detail);
    const flags = detail.consistency?.flags ?? [];
    const story = buildStoryRows(flags);
    const storyAll = story.flatMap((r) => (r.flags.length > 1 ? [r, ...r.flags.map(storyRowOf)] : [r]));
    return {
      findings, slots, story, storyAll,
      eligibility: findings.filter((f) => f.section === "eligibility" || f.section === null),
      merit: findings.filter((f) => f.section === "merit").sort((a, b) => a.rule_code.localeCompare(b.rule_code, undefined, { numeric: true })),
      documentRules: findings.filter((f) => f.section === "documents"),
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
  const flagsToCheck = openFlagCount(detail.consistency?.flags ?? []);
  const injection = detail.latest_run?.injection_flags ?? [];
  const consistencyOn = detail.consistency?.enabled;

  const locking = busy || loading;
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setActionError(null);
    try { await fn(); reload(); } catch (e) { setActionError(e); } finally { setBusy(false); }
  };
  const confirm = (f: Finding) => void act(() => api.review(f.id, { action: "confirm" }));

  const openItem = (next: PanelItem, rowKey: string) => { lastRow.current = rowKey; setItem(next); };
  const meritItem = item?.kind === "rule" ? detail.findings.find((x) => x.rule_code === item.code && x.section === "merit") ?? null : null;
  const selectedKey = item ? (item.kind === "rule" ? `rule:${item.code}` : item.kind === "doc" ? `doc:${item.key}` : `story:${item.id}`) : null;
  const jump = (sec: SectionId) => {
    setOpen((o) => ({ ...o, [sec]: true }));
    setTimeout(() => document.getElementById(`sec-${sec}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  };
  const scopedTrace = (target: string) => navigate({ name: "trace", id, item: target });

  const slotsShown = [...model.slots]
    .filter((s) => !filter || slotFindings(s, detail.findings).some((f) => effectiveStatus(f) === filter))
    .sort((a, b) => slotSort(a, b, detail.findings));
  const meritShown = model.merit.filter((f) => !filter || effectiveStatus(f) === filter);
  const hasDocuments = model.documentRules.length > 0 || detail.documents.length > 0;
  const undecided = (list: Finding[]) => list.filter((f) => !isDecided(f)).length;
  const storyOpenable = model.story.length;
  const notChecked = !detail.latest_run && !app.manual_assessment_requested;

  return (
    <main className={`review-page rv-page ${item && !meritItem ? "has-panel" : ""}`}>
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
              <Button disabled={!locked && !allDecided} onClick={() => navigate({ name: "signoff", id })}
                title={!locked && !allDecided ? `Decide every rule first: ${total - decided} still to decide.` : undefined}>
                {locked ? "View sign-off" : "Review sign-off"} <Icon name="arrow" />
              </Button>
              {!locked && !allDecided && total > 0 && <small className="rv-why" id="signoff-why">Unlocks when all {total} rules are decided ({total - decided} to go).</small>}
            </span>
          </div>
        </div>
        {total > 0 && (
          <div className="rv-strip" aria-label="Rule status counts. Select one to filter the tables.">
            {STRIP.map(({ status, label }) => {
              const n = count(status);
              const on = filter === status;
              return (
                <button key={status} className={`rv-count ${on ? "on" : ""}`} aria-pressed={on} onClick={() => setFilter(on ? null : status)}
                  title={on ? "Show all" : `Show only: ${label}`}>
                  {status === "Evidence only" ? <span className="judgement-chip"><Icon name="eye" size={15} />{label}</span> : <StatusChip status={status} />}
                  <strong>{n}</strong>
                </button>
              );
            })}
            {consistencyOn && (
              <button className="rv-flags-chip" onClick={() => jump("story")} title="Story flags are separate from the rule counts">
                <Icon name="info" size={14} />Story flags to check: <strong>{flagsToCheck}</strong>
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

        <nav className="rv-nav" aria-label="Sections">
          {SECTIONS.map((s) => {
            const n = s.id === "documents" ? undecided(model.documentRules) : s.id === "eligibility" ? undecided(model.eligibility) : s.id === "merit" ? undecided(model.merit) : flagsToCheck;
            return (
              <button key={s.id} onClick={() => jump(s.id)}><span>{s.title}</span><Badge n={n} label={s.id === "story" ? "flags to check" : "not decided"} /></button>
            );
          })}
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

          <Block id="documents" title="Documents" count={undecided(model.documentRules)} countLabel="not decided" open={open.documents} onToggle={() => setOpen({ ...open, documents: !open.documents })}
            aside={<span className="muted rv-hint">Check these first: right document, in the right place.</span>}>
            {!hasDocuments ? <p className="empty-state">This grant does not ask for documents.</p>
              : slotsShown.length === 0 ? <p className="empty-state">No documents match this filter.</p>
              : <DocumentsTable slots={slotsShown} findings={detail.findings} selectedKey={selectedKey} onOpen={(s) => openItem({ kind: "doc", key: s.key }, `doc:${s.key}`)} />}
          </Block>

          <Block id="eligibility" title="Eligibility" count={undecided(model.eligibility)} countLabel="not decided" open={open.eligibility} onToggle={() => setOpen({ ...open, eligibility: !open.eligibility })}
            aside={<span className="muted rv-hint">Unclear and missing evidence first.</span>}>
            <div className="rv-filters" role="group" aria-label="Filter eligibility rules">
              {(["Unclear", "Needs evidence", "Not met", "Met"] as AIStatus[]).map((s) => {
                const n = model.eligibility.filter((f) => effectiveStatus(f) === s).length;
                return <button key={s} className={`rv-filter ${filter === s ? "on" : ""}`} aria-pressed={filter === s} onClick={() => setFilter(filter === s ? null : s)}>{s} <strong>{n}</strong></button>;
              })}
            </div>
            {model.eligibility.length === 0
              ? <p className="empty-state">{notChecked ? "Not checked yet. Run the AI check to see the rules." : "No eligibility rules."}</p>
              : <EligibilityTable list={model.eligibility} filter={filter} selectedKey={selectedKey} onOpen={(f) => openItem({ kind: "rule", code: f.rule_code }, `rule:${f.rule_code}`)} />}
          </Block>

          <Block id="merit" title="Merit criteria" count={undecided(model.merit)} countLabel="not decided" open={open.merit} onToggle={() => setOpen({ ...open, merit: !open.merit })}>
            {model.merit.length === 0 ? <p className="empty-state">{notChecked ? "Not checked yet." : "This grant has no merit criteria."}</p>
              : meritShown.length === 0 ? <p className="empty-state">No criteria match this filter.</p>
              : <MeritTable list={meritShown} selectedKey={selectedKey} onOpen={(f) => openItem({ kind: "rule", code: f.rule_code }, `rule:${f.rule_code}`)} />}
          </Block>

          <Block id="story" title="Does the story add up?" count={flagsToCheck} countLabel="flags to check" open={open.story} onToggle={() => setOpen({ ...open, story: !open.story })}
            aside={
              <span className="rv-story-aside">
                <strong>{flagsToCheck} {flagsToCheck === 1 ? "flag" : "flags"} to check</strong>
                <span className="rv-tip-wrap">
                  <button className="icon-button rv-info" aria-label="About story flags" aria-describedby="rv-tip-story"><Icon name="info" size={16} /></button>
                  <span role="tooltip" id="rv-tip-story" className="rv-tip">Signals, not findings. A flag never changes a rule result and never blocks sign-off.</span>
                </span>
              </span>
            }>
            {consistencyOn === false ? <p className="empty-state">The story check is switched off for this site.</p>
              : detail.consistency?.unavailable ? <p className="empty-state">Could not load the story flags. Please review manually.</p>
              : (
                <>
                  <div className="rv-typechips" aria-label="Flags by type">
                    {TYPE_ORDER.map((t) => {
                      const n = (detail.consistency?.flags ?? []).filter((f) => f.check_type === t).length;
                      return <span key={t} className={`rv-typechip ${n ? "" : "zero"}`}>{TYPE_CHIP[t]} <strong>{n}</strong></span>;
                    })}
                  </div>
                  {storyOpenable === 0 ? <p className="empty-state">{notChecked ? "Not checked yet." : "No flags for this application."}</p>
                    : <StoryTable rows={model.story} selectedKey={selectedKey} onOpen={(r) => openItem({ kind: "story", id: r.id }, `story:${r.id}`)} />}
                </>
              )}
          </Block>
        </div>

        {item && !meritItem && (
          <ReviewPanel item={item} slots={model.slots} story={model.storyAll} detail={detail} locked={locked} busy={locking} onClose={closePanel}
            onDialog={setDialog} onConfirm={confirm} onChanged={reload} onTrace={scopedTrace} />
        )}
      </div>

      {meritItem && (
        <MeritViewer f={meritItem} detail={detail} locked={locked} busy={locking} onClose={closePanel} onChanged={reload} onTrace={scopedTrace} onDialog={setDialog} onConfirm={confirm} />
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
