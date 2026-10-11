// Step 3, Assessment. Two parts, switched at the top:
//   3a Eligibility and documents rules: one table sorted by need; a row opens the evidence drawer.
//   3b Merit and consistency: five merit cards (each opens the grading workspace) beside two short lists of signals.
// Counts come from one place (src/counts.ts), so the header, the switch, the cards and the drawer always agree.
// No overall score, ranking or recommendation is shown. Merit marks belong to the officer and are never totalled.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type AIStatus, type Detail, type Finding, type OverviewRow } from "../api";
import type { Navigate } from "../App";
import { isRule, type Counts } from "../counts";
import { Button, ErrorNotice, Icon, StatusChip, effectiveStatus, isDecided } from "../ui";
import { ConsistencyPanel, ResultChip } from "./review/ConsistencyTab";
import { AskDialog, DecisionDialog, type DialogState } from "./review/Dialogs";
import { CRITERIA, MeritViewer } from "./review/MeritViewer";
import { Badge, DecisionCell, Row } from "./review/parts";
import { ReviewPanel } from "./review/Panel";
import { CHECKED_BY_LABEL, byAttention, checkedBy, decisionText, plainRule } from "./review/model";

export type Part = "3a" | "3b";
type Chip = "all" | "needs" | "decided";
type Open = { kind: "rule"; code: string } | { kind: "referee" } | { kind: "cons"; id: string };

const STRIP: { status: AIStatus; label: string }[] = [
  { status: "Needs evidence", label: "Needs evidence" }, { status: "Unclear", label: "Unclear" }, { status: "Not met", label: "Not met" },
  { status: "Met", label: "Met" }, { status: "Evidence only", label: "Officer judgement" },
];
const remember = {
  get: (k: string, d: string) => { try { return localStorage.getItem(`grantie.${k}`) ?? d; } catch { return d; } },
  set: (k: string, v: string) => { try { localStorage.setItem(`grantie.${k}`, v); } catch { /* a remembered choice is only a convenience */ } },
};
const oneLine = (f: Finding) => f.rationale ?? plainRule(f);
const criterionName = (f: Finding) => CRITERIA[f.rule_code] ?? plainRule(f);
const codeOrder = (a: Finding, b: Finding) => a.rule_code.localeCompare(b.rule_code, undefined, { numeric: true });

// ---------------------------------------------------------------- 3a table

function RuleRow({ f, selected, picked, onPick, onOpen, locked }: {
  f: Finding; selected: boolean; picked: boolean; onPick: () => void; onOpen: () => void; locked: boolean;
}) {
  return (
    <Row rowKey={`rule:${f.rule_code}`} selected={selected} onOpen={onOpen} label={`Rule ${f.rule_code}. ${plainRule(f)}. ${effectiveStatus(f)}. ${decisionText(f)}. Open details`}>
      <td className="rv-pick" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
        <input type="checkbox" checked={picked} disabled={locked} onChange={onPick} aria-label={`Select rule ${f.rule_code} to ask the applicant`} />
      </td>
      <td className="rv-id-cell">{f.rule_code}</td>
      <td><StatusChip status={effectiveStatus(f)} /></td>
      <td className="col-low"><span className="rv-tag">{CHECKED_BY_LABEL[checkedBy(f)]}</span></td>
      <td className="rv-oneline">{oneLine(f)}</td>
      <td><DecisionCell text={decisionText(f)} /></td>
    </Row>
  );
}

function EligibilityTable({ list, chip, status, picked, setPicked, selectedKey, onOpen, locked }: {
  list: Finding[]; chip: Chip; status: AIStatus | null; picked: Set<string>; setPicked: (s: Set<string>) => void;
  selectedKey: string | null; onOpen: (f: Finding) => void; locked: boolean;
}) {
  const [showMet, setShowMet] = useState(false);
  const shown = list.filter((f) => (chip === "all" || (chip === "needs" ? !isDecided(f) : isDecided(f))) && (!status || effectiveStatus(f) === status));
  const toggle = (id: string) => { const n = new Set(picked); if (n.has(id)) n.delete(id); else n.add(id); setPicked(n); };
  if (!shown.length) return <p className="empty-state">No rules match this filter.</p>;
  const rows = (items: Finding[]) => items.map((f) => (
    <RuleRow key={f.id} f={f} locked={locked} selected={selectedKey === `rule:${f.rule_code}`} picked={picked.has(f.id)} onPick={() => toggle(f.id)} onOpen={() => onOpen(f)} />
  ));
  const met = shown.filter((f) => effectiveStatus(f) === "Met");
  const need = shown.filter((f) => effectiveStatus(f) !== "Met");
  const group = (title: string, items: Finding[]) => items.length ? (
    <>
      <tr className="rv-group-row"><th colSpan={6} scope="colgroup">{title}</th></tr>
      {rows(items)}
    </>
  ) : null;
  const isDoc = (f: Finding) => f.section === "documents";
  const metOpen = showMet || status === "Met";
  return (
    <div className="rv-table-wrap" role="region" aria-label="Eligibility and documents rules" tabIndex={-1}>
      <table className="rv-table">
        <thead><tr><th><span className="sr-only">Select</span></th><th>Rule</th><th>Status</th><th className="col-low">Checked by</th><th>Finding in one line</th><th>Officer decision</th></tr></thead>
        <tbody>
          {group("Eligibility", need.filter((f) => !isDoc(f)).sort(byAttention))}
          {group("Documents", need.filter(isDoc).sort(byAttention))}
          {met.length > 0 && (
            <tr className="rv-group-row"><td colSpan={6}>
              <button className="rv-group-toggle" aria-expanded={metOpen} onClick={() => setShowMet(!showMet)} disabled={status === "Met"}>
                <span className={metOpen ? "rotate" : ""}><Icon name="chevron" size={16} /></span>{met.length} Met. Scan and confirm one by one.
              </button>
            </td></tr>
          )}
          {metOpen && rows([...met].sort(codeOrder))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- 3b

function MeritCard({ f, selected, onOpen }: { f: Finding; selected: boolean; onOpen: () => void }) {
  const m = f.merit_mark;
  const bullets = (f.ai_summaries_restored ?? []).filter((s) => s.linked && s.text).slice(0, 2);
  const state = m ? (m.not_assessed ? "Not assessed" : `Marked ${m.mark} out of 100`) : "Not marked";
  return (
    <article className={`rv-mcard ${selected ? "selected" : ""}`}>
      <header><h3>{criterionName(f)}</h3><DecisionCell text={state} /></header>
      {bullets.length > 0
        ? <ul>{bullets.map((s, i) => <li key={i}><small>AI summary, check the original</small>{s.text}</li>)}</ul>
        : <p className="muted">No AI summary with a verified source. Open the document.</p>}
      <Button variant="secondary" onClick={onOpen}>Open grading workspace</Button>
    </article>
  );
}

function SignalList({ rows, selectedKey, onOpen }: { rows: OverviewRow[]; selectedKey: string | null; onOpen: (r: OverviewRow) => void }) {
  const order = { Differs: 0, "Cannot compare": 1, "Needs evidence": 1, Consistent: 2 } as Record<string, number>;
  const sorted = [...rows].sort((a, b) => (order[a.result] ?? 2) - (order[b.result] ?? 2));
  if (!sorted.length) return <p className="empty-state">No comparisons to show for this application.</p>;
  return (
    <ul className="rv-signals">
      {sorted.map((r) => (
        <li key={r.id}>
          <button className={selectedKey === `cons:${r.id}` ? "selected" : ""} onClick={() => onOpen(r)}
            aria-label={`${r.check}: ${r.compared}. ${r.result}. ${r.decision}. Open details`}>
            <span><strong>{r.check}</strong><small>{r.compared}</small></span>
            <span className="rv-signal-r"><ResultChip result={r.result} /><small>{r.decision}</small></span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- page

export default function Review({ detail, reload, part, setPart, counts, navigate }: {
  detail: Detail; reload: () => void; part: Part; setPart: (p: Part) => void; counts: Counts; navigate: Navigate;
}) {
  const id = detail.application.id;
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [item, setItem] = useState<Open | null>(null);
  const [chip, setChip] = useState<Chip>(remember.get("assess.chip", "all") as Chip);
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const lastRow = useRef<string | null>(null);
  const scrollY = useRef(0);

  useEffect(() => remember.set("assess.chip", chip), [chip]);

  const model = useMemo(() => {
    const findings = detail.findings;
    return {
      rules: findings.filter(isRule),
      merit: findings.filter((f) => !isRule(f)).sort(codeOrder),
      overview: (detail.consistency?.overview ?? []) as OverviewRow[],
      linked: detail.linked_applications ?? [],
    };
  }, [detail]);

  // The order the officer moves through: items that need a decision first (by need), then the rest.
  const order = useMemo(() => [...model.rules].sort(byAttention), [model.rules]);
  const locked = detail.application.status === "signed_off";
  const count = (s: AIStatus) => model.rules.filter((f) => effectiveStatus(f) === s).length;

  const openRule = (code: string) => { scrollY.current = window.scrollY; lastRow.current = `rule:${code}`; setItem({ kind: "rule", code }); };
  const closePanel = useCallback(() => {
    setItem(null);
    const key = lastRow.current;
    setTimeout(() => {
      window.scrollTo({ top: scrollY.current });
      if (key) document.querySelector<HTMLElement>(`[data-row-key="${CSS.escape(key)}"]`)?.focus();
    }, 0);
  }, []);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setActionError(null);
    try { await fn(); reload(); } catch (e) { setActionError(e); } finally { setBusy(false); }
  };
  const confirm = (f: Finding) => void act(() => api.review(f.id, { action: "confirm" }));

  const pos = item?.kind === "rule" ? order.findIndex((f) => f.rule_code === item.code) : -1;
  const go = (delta: number) => { const f = order[pos + delta]; if (f) openRule(f.rule_code); };
  const nextUndecided = (from: number) => order.find((f, i) => i > from && !isDecided(f)) ?? order.find((f) => !isDecided(f)) ?? null;
  const reviewNext = () => { const f = nextUndecided(pos); if (f) { if (part !== "3a") setPart("3a"); openRule(f.rule_code); } };

  // Keyboard: arrows move between rows, Enter opens, Esc closes, J / K = next / previous item, C = confirm the shown result.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.tagName === "SELECT" || t.isContentEditable);
      if (e.key === "Escape" && item && !dialog) { e.preventDefault(); closePanel(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (item?.kind === "rule" && !dialog) {
        const k = e.key.toLowerCase();
        if (k === "j") { e.preventDefault(); go(1); return; }
        if (k === "k") { e.preventDefault(); go(-1); return; }
        if (k === "c" && !locked && !busy) {
          const f = order[pos];
          if (f && f.ai_status !== "Evidence only" && !isDecided(f)) { e.preventDefault(); confirm(f); }
          return;
        }
      }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      if (!t?.hasAttribute("data-row")) return;
      const rows = [...document.querySelectorAll<HTMLElement>("[data-row]")];
      const next = rows[rows.indexOf(t) + (e.key === "ArrowDown" ? 1 : -1)];
      if (next) { e.preventDefault(); next.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });   // re-bound each render so the handler always sees the current item and order

  const injection = detail.latest_run?.injection_flags ?? [];
  const notChecked = !detail.latest_run && !detail.application.manual_assessment_requested;
  const selectedKey = !item ? null : item.kind === "rule" ? `rule:${item.code}` : item.kind === "referee" ? "referee" : `cons:${item.id}`;
  const scopedTrace = (target: string) => navigate({ name: "trace", id, item: target });
  const viewer = item?.kind === "referee" ? detail.findings.find((x) => x.rule_code === "M2") ?? null
    : item?.kind === "rule" ? detail.findings.find((x) => x.rule_code === item.code && x.section === "merit") ?? null : null;
  const sideItem = item?.kind === "rule" && !viewer ? item : null;
  const consRow = item?.kind === "cons" ? model.overview.find((r) => r.id === item.id) ?? null : null;
  const sideOpen = Boolean(sideItem || consRow);
  const pickedFindings = model.rules.filter((f) => picked.has(f.id));

  return (
    <div className={`rv-page ${sideOpen ? "has-panel" : ""}`}>
      <div className="rv-nav">
        <div role="tablist" className="rv-tablist" aria-label="Assessment parts">
          <button role="tab" aria-selected={part === "3a"} className={part === "3a" ? "active" : ""} onClick={() => { setItem(null); setPart("3a"); }}>
            <span>3a Eligibility and documents</span>{counts.eligibility.undecided ? <Badge n={counts.eligibility.undecided} label="to decide" /> : null}
          </button>
          <button role="tab" aria-selected={part === "3b"} className={part === "3b" ? "active" : ""} onClick={() => { setItem(null); setPart("3b"); }}>
            <span>3b Merit and consistency</span>{counts.merit.unmarked + counts.consistency.toCheck ? <Badge n={counts.merit.unmarked + counts.consistency.toCheck} label="to do" /> : null}
          </button>
        </div>
        <small className="rv-hints" aria-label="Keyboard shortcuts"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>Enter</kbd> open · <kbd>J</kbd><kbd>K</kbd> next · <kbd>C</kbd> confirm · <kbd>Esc</kbd> close</small>
      </div>

      <div className="rv-layout">
        <div className="rv-main">
          {injection.length > 0 && (
            <div className="notice warn-notice" role="alert"><Icon name="shield" />
              <span><strong>Instruction-like text was found in this application and was not followed.</strong> AI confidence has been lowered; read these answers yourself.</span>
            </div>
          )}
          <ErrorNotice error={actionError} />
          {detail.application.manual_assessment_requested && <div className="notice"><Icon name="user" />The applicant asked for a person to assess this application. AI assessment will not run.</div>}
          {notChecked && (
            <div className="panel empty-state">
              <p>This application has not been checked yet. The check redacts personal details first, then asks the AI.</p>
              <Button icon="search" disabled={busy || locked} onClick={() => void act(() => api.assess(id))}>{busy ? "Checking…" : "Run AI check"}</Button>
            </div>
          )}

          {part === "3a" && (
            <section className="rv-section" aria-label="Eligibility and documents">
              <div className="rv-section-body">
                <div className="rv-toolrow">
                  <div className="rv-filters" role="group" aria-label="Filter rules">
                    {([["all", "All", model.rules.length], ["needs", "Needs me", counts.eligibility.undecided], ["decided", "Decided", counts.eligibility.decided]] as [Chip, string, number][]).map(([k, label, n]) => (
                      <button key={k} className={`rv-filter ${chip === k ? "on" : ""}`} aria-pressed={chip === k} onClick={() => setChip(k)}>{label} <strong>{n}</strong></button>
                    ))}
                    <span className="rv-filter-sep" aria-hidden="true" />
                    {STRIP.filter(({ status: st }) => count(st) > 0).map(({ status: st, label }) => (
                      <button key={st} className={`rv-filter ${status === st ? "on" : ""}`} aria-pressed={status === st} onClick={() => setStatus(status === st ? null : st)}>{label} <strong>{count(st)}</strong></button>
                    ))}
                  </div>
                  <div className="rv-toolbtns">
                    <Button variant="secondary" icon="arrow" disabled={counts.eligibility.undecided === 0} onClick={reviewNext}>Review next</Button>
                    <Button variant="secondary" icon="send" disabled={locked || pickedFindings.length === 0}
                      onClick={() => pickedFindings[0] && setDialog({ kind: "ask", finding: pickedFindings[0], more: pickedFindings })}>
                      Ask applicant for more{pickedFindings.length ? ` (${pickedFindings.length})` : ""}
                    </Button>
                  </div>
                </div>
                {model.rules.length === 0
                  ? <p className="empty-state">{notChecked ? "Not checked yet. Run the AI check to see the rules." : "No eligibility rules."}</p>
                  : <EligibilityTable list={model.rules} chip={chip} status={status} picked={picked} setPicked={setPicked} selectedKey={selectedKey} locked={locked}
                      onOpen={(f) => openRule(f.rule_code)} />}
              </div>
            </section>
          )}

          {part === "3b" && (
            <section className="rv-section rv-two" aria-label="Merit and consistency">
              <div className="rv-section-body">
                <h2 className="rv-h2">Merit criteria</h2>
                {model.merit.length === 0 ? <p className="empty-state">{notChecked ? "Not checked yet." : "This grant has no merit criteria."}</p> : (
                  <div className="rv-mcards">
                    {model.merit.flatMap((f) => {
                      const cards = [<MeritCard key={f.id} f={f} selected={selectedKey === `rule:${f.rule_code}`} onOpen={() => openRule(f.rule_code)} />];
                      if (f.rule_code === "M2") {
                        cards.push(
                          <article key="ref" className={`rv-mcard ${selectedKey === "referee" ? "selected" : ""}`}>
                            <header><h3>Referee check</h3><span className="rv-decision">Check only. Not marked</span></header>
                            <p className="muted">Is the referee identified and contactable?</p>
                            <Button variant="secondary" onClick={() => { scrollY.current = window.scrollY; lastRow.current = null; setItem({ kind: "referee" }); }}>View detail</Button>
                          </article>,
                        );
                      }
                      return cards;
                    })}
                  </div>
                )}
              </div>
              <aside className="rv-side-lists" aria-label="Signals to check">
                <section className="rv-sidecard sec-consistency">
                <h2>Consistency of information</h2>
                {detail.consistency?.enabled === false ? <p className="empty-state">The consistency check is switched off for this site.</p>
                  : detail.consistency?.unavailable ? <p className="empty-state">Could not load the consistency results. Please review manually.</p>
                  : <SignalList rows={model.overview} selectedKey={selectedKey}
                      onOpen={(r) => { scrollY.current = window.scrollY; lastRow.current = null; setItem({ kind: "cons", id: r.id }); }} />}
                </section>
                <section className="rv-sidecard sec-linked">
                <h2>Linked applications</h2>
                {model.linked.length === 0 ? <p className="empty-state">No linked applications found.</p> : (
                  <ul className="rv-signals">
                    {model.linked.map((l) => (
                      <li key={l.application_id}>
                        <button onClick={() => l.can_open && navigate({ name: "review", id: l.application_id })} disabled={!l.can_open}
                          aria-label={`${l.reference}. ${l.strength}. ${l.can_open ? "Open" : "No access"}`}>
                          <span><strong>{l.reference}</strong><small>{l.shared.map((s) => s.what).join(", ")}</small></span>
                          <span className="rv-signal-r"><small>{l.strength}</small></span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                </section>
              </aside>
            </section>
          )}
        </div>

        {sideItem && (
          <ReviewPanel item={sideItem} slots={[]} detail={detail} locked={locked} busy={busy} onClose={closePanel}
            onDialog={setDialog} onConfirm={confirm} onChanged={reload} onTrace={scopedTrace}
            nav={{ position: pos + 1, total: order.length, onPrev: pos > 0 ? () => go(-1) : undefined, onNext: pos < order.length - 1 ? () => go(1) : undefined, onNextUndecided: reviewNext }} />
        )}
        {consRow && (
          <ConsistencyPanel row={consRow} detail={detail} flags={detail.consistency?.flags ?? []} locked={locked} onChanged={reload} onClose={closePanel} onTrace={scopedTrace} />
        )}
      </div>

      {viewer && <MeritViewer f={viewer} detail={detail} mode={item?.kind === "referee" ? "referee" : "criterion"} locked={locked} onClose={closePanel} onChanged={reload} onTrace={scopedTrace} />}

      {dialog && dialog.kind === "ask" && (
        <AskDialog detail={detail} findings={dialog.more ?? [dialog.finding]} close={() => setDialog(null)} onDone={() => { setDialog(null); setPicked(new Set()); reload(); }} />
      )}
      {dialog && dialog.kind !== "ask" && <DecisionDialog state={dialog} close={() => setDialog(null)} onDone={() => { setDialog(null); reload(); }} />}
    </div>
  );
}
