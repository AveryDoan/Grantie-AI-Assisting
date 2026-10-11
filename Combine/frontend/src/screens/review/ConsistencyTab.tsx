// Consistency of information: whether the details match across the form, the documents and the timeline.
// An overview table first (every comparison, including the ones that agree), then a side panel with both values side by side,
// each with its source and a link to the highlighted text. A difference is a difference to check, never a verdict. Nothing here
// changes a rule result or blocks sign-off.
import { useState } from "react";
import type { CompareSide, ConsistencyFlag, Detail, OverviewResult, OverviewRow } from "../../api";
import { Icon } from "../../ui";
import { FlagActions } from "../Consistency";
import { Row } from "./parts";
import { PdfModal } from "./PdfModal";
import { pdfTargets, type EvidenceSpan } from "./pdfTargets";
import { SourceDrawer } from "./SourceDrawer";

const RESULT_ICON: Record<OverviewResult, string> = { Consistent: "check", Differs: "info", "Cannot compare": "question", "Needs evidence": "file" };

export function ResultChip({ result }: { result: OverviewResult }) {
  return <span className={`rv-result ${result === "Consistent" ? "ok" : result === "Differs" || result === "Needs evidence" ? "differs" : "na"}`}><Icon name={RESULT_ICON[result]} size={14} />{result}</span>;
}

export function ConsistencyTable({ rows, selectedKey, onOpen }: { rows: OverviewRow[]; selectedKey: string | null; onOpen: (r: OverviewRow) => void }) {
  return (
    <div className="rv-table-wrap" role="region" aria-label="Consistency of information" tabIndex={-1}>
      <table className="rv-table">
        <thead><tr><th className="col-low">Check</th><th>What was compared</th><th>Result</th><th>Officer decision</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <Row key={r.id} rowKey={`cons:${r.id}`} selected={selectedKey === `cons:${r.id}`} onOpen={() => onOpen(r)} label={`${r.compared}. ${r.result}. Open details`}>
              <td className="col-low">{r.check}</td>
              <td><strong>{r.compared}</strong>{r.why && r.result !== "Consistent" && <small className="rv-reasonline">{r.why}</small>}</td>
              <td><ResultChip result={r.result} /></td>
              <td><span className="rv-decision">{r.decision}</span></td>
            </Row>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Side({ side, onShow }: { side: CompareSide | null; onShow: (s: CompareSide) => void }) {
  if (!side) return <div className="rv-side empty"><p className="rv-muted">Nothing to show for this side.</p></div>;
  const canShow = side.start !== null && side.end !== null;
  return (
    <div className="rv-side">
      <h4>{side.label}</h4>
      <blockquote className="rv-sidevalue">{side.value ?? <span className="rv-muted">Not provided</span>}</blockquote>
      <p className="rv-sidesource">Source: {side.source_label ?? "Application form"}</p>
      {side.has_text && <button className="link-button" onClick={() => onShow(side)}>{canShow ? "Show in source text" : "Open source text"} <Icon name="arrow" size={14} /></button>}
    </div>
  );
}

export function ConsistencyPanel({ row, detail, flags, locked, onChanged, onClose, onTrace }: {
  row: OverviewRow; detail: Detail; flags: ConsistencyFlag[]; locked: boolean; onChanged: () => void; onClose: () => void; onTrace: (item: string) => void;
}) {
  const [show, setShow] = useState<CompareSide | null>(null);
  const [pdf, setPdf] = useState(false);
  const spans: EvidenceSpan[] = ([["left", row.left], ["right", row.right]] as const).flatMap(([kind, side]) =>
    side && side.value ? [{ id: kind, kind, source: side.source, start: side.start, end: side.end, text: side.value, label: side.label } as EvidenceSpan] : []);
  const targets = pdfTargets(detail, spans);
  const mine = flags.filter((f) => row.flag_ids.includes(f.id));
  const target = show && show.start !== null && show.end !== null ? { source: show.source, start: show.start, end: show.end } : null;
  return (
    <aside className="rv-panel" aria-label={`Details: ${row.compared}`}>
      <div className="rv-panel-head">
        <div tabIndex={-1} ref={(el) => el?.focus()}><p className="eyebrow">{row.check}</p><h2>{row.compared}</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="Close details (Esc)"><Icon name="close" /></button>
      </div>
      <div className="rv-panel-body">
        <section className="rv-psec">
          <div className="rv-chips"><ResultChip result={row.result} /><span className="rv-tag">{row.decision}</span></div>
          {row.why && <p className="rv-reason">{row.why}</p>}
          <p className="muted">A difference to check, not a conclusion. It does not change any rule result and does not stop sign-off.</p>
        </section>
        {(row.left || row.right) && (
          <section className="rv-psec">
            <h3>Both values</h3>
            <div className="rv-sides">
              <Side side={row.left} onShow={setShow} />
              <Side side={row.right} onShow={setShow} />
            </div>
            {targets.length > 0 && <button className="button button-secondary rv-showtext" onClick={() => setPdf(true)}><Icon name="search" size={16} />Show both on the PDF</button>}
          </section>
        )}
        {mine.length > 0 && (
          <section className="rv-psec">
            <h3>Your decision</h3>
            {mine.map((f) => <FlagActions key={f.id} flag={f} onChanged={onChanged} locked={locked} />)}
          </section>
        )}
        {mine.length > 0 && (
          <div className="rv-pfoot"><button className="link-button" onClick={() => onTrace(`flag:${mine[0].id}`)}>See how this was redacted and how the AI read it <Icon name="arrow" size={14} /></button></div>
        )}
      </div>
      {pdf && <PdfModal appId={detail.application.id} title={row.compared} targets={targets} onClose={() => setPdf(false)} />}
      {show && <SourceDrawer title={row.compared} detail={detail} target={target} onBack={() => setShow(null)} />}
    </aside>
  );
}
