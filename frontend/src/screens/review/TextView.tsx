// A text with paragraph and page markers, and highlighted passages the reader can click.
// Highlights come from exact positions only (see highlight.ts). A click on one reports its note.
import { useEffect, useMemo, useRef } from "react";
import { segment, toBlocks, type Block, type Highlight } from "./highlight";
import { fieldLabel } from "./labels";

// Form text is stored as "field_name: answer". Show the form's own label; the characters (and so every highlight position) are unchanged.
const FORM_KEY = /^([a-z][a-z0-9]*(?:_[a-z0-9]+)+): /;
function plain(text: string, first: boolean) {
  const m = first ? FORM_KEY.exec(text) : null;
  return m ? <><strong className="rv-formlabel">{fieldLabel(m[1])}:</strong> {text.slice(m[0].length)}</> : text;
}

export function TextView({ text, highlights, selected, onPick, only, focusId }: {
  text: string;
  highlights: Highlight[];
  selected?: string | null;                 // note id whose passage is emphasised and scrolled into view
  onPick?: (noteId: string) => void;
  only?: Block[];                            // show just these paragraphs (a section), not the whole text
  focusId?: string;                          // a highlight to scroll to on first show (the "Show the application text" drawer)
}) {
  const root = useRef<HTMLDivElement>(null);
  const blocks = useMemo(() => only ?? toBlocks(text), [only, text]);
  const shown = useMemo(() => (only ? only : blocks), [only, blocks]);

  useEffect(() => {
    const el = root.current?.querySelector<HTMLElement>(selected ? `[data-note="${selected}"]` : focusId ? `[data-hl="${focusId}"]` : "__none__");
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selected, focusId, text]);

  return (
    <div className="rv-text" ref={root}>
      {shown.map((b, i) => {
        if (b.kind === "page") return <div key={`p${b.start}`} className="rv-pagemark"><span>Page {b.page}</span></div>;
        const clamped = highlights.map((h) => ({ ...h, start: Math.max(h.start, b.start) - b.start, end: Math.min(h.end, b.end) - b.start })).filter((h) => h.end > h.start);
        const segs = segment(text.slice(b.start, b.end), clamped);
        return (
          <p key={b.start} className="rv-para" data-para={b.number ?? i + 1}>
            <span className="rv-parano" aria-hidden="true">¶ {b.number ?? i + 1}</span>
            {segs.map((s) => s.highlight ? (
              <mark key={s.start} data-hl={s.highlight.id} data-note={s.highlight.noteId} tabIndex={s.highlight.noteId ? 0 : undefined}
                role={s.highlight.noteId ? "button" : undefined}
                className={`rv-hl ${s.highlight.kind} ${selected && s.highlight.noteId === selected ? "sel" : ""}`}
                onClick={() => s.highlight!.noteId && onPick?.(s.highlight!.noteId)}
                onKeyDown={(e) => { if ((e.key === "Enter" || e.key === " ") && s.highlight!.noteId) { e.preventDefault(); onPick?.(s.highlight!.noteId); } }}>{s.text}</mark>
            ) : <span key={s.start}>{plain(s.text, s.start === 0)}</span>)}
          </p>
        );
      })}
    </div>
  );
}
