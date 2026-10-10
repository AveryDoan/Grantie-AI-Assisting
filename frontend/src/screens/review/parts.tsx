// Small pieces shared by the review tabs.
import type { KeyboardEvent, ReactNode } from "react";
import { Icon } from "../../ui";

export function Badge({ n, label }: { n: number; label: string }) {
  return <span className="rv-badge" aria-label={`${n} ${label}`}>{n}</span>;
}

export function DecisionCell({ text }: { text: string }) {
  const done = text === "Confirmed" || text === "Overridden" || text === "Recorded";
  return <span className={done ? "rv-decision done" : "rv-decision"}><Icon name={done ? "check" : "clock"} size={14} />{text}</span>;
}

/** One row in any table: focusable, opens the side panel on click or Enter. */
export function Row({ rowKey, selected, onOpen, label, children, className }: {
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

