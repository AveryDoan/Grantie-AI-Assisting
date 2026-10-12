// Pieces shared by the four steps: the step bar, the progress sidebar and the "Next step" button.
import { Button, Icon } from "../../ui";
import type { StepInfo } from "../../api";


/** The colour of each area of work. The same class carries through to the band, the cards and the drawer. */
export const SEC: Record<number, string> = { 1: "sec-docs", 2: "sec-redaction", 3: "sec-elig", 4: "sec-outcome" };

/** The four big steps. `compact` (used inside Assessment) is a slim strip: icon and name only; status is the icon and colour. */
export function StepBar({ steps, shown, onGo, compact }: { steps: StepInfo[]; shown: number; onGo: (n: number) => void; compact?: boolean }) {
  if (compact) {
    return (
      <nav className="gf-strip" aria-label="Application steps">
        <ol>
          {steps.map((s) => {
            const state = shown === s.step ? "active" : !s.unlocked ? "locked" : s.status === "done" ? "done" : "open";
            const label = state === "active" ? "current step" : state === "locked" ? "locked" : state === "done" ? "done" : s.label.toLowerCase();
            return (
              <li key={s.step} className={`gf-chip ${SEC[s.step]} ${state}`}>
                <button onClick={() => onGo(s.step)} disabled={!s.unlocked} aria-current={shown === s.step ? "step" : undefined}
                  aria-label={`${s.title}, ${label}`} title={s.unlocked ? `${s.title}: ${s.label}` : `Finish step ${s.step - 1} first`}>
                  <Icon name={state === "locked" ? "lock" : state === "done" ? "check" : state === "active" ? "arrow" : "clock"} size={14} />
                  <span>{s.title}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
    );
  }
  return (
    <nav className="gf-bar" aria-label="Application steps">
      <ol>
        {steps.map((s) => (
          <li key={s.step} className={`gf-step ${SEC[s.step]} ${s.status} ${shown === s.step ? "shown" : ""} ${s.unlocked ? "" : "locked"}`}>
            <button onClick={() => onGo(s.step)} disabled={!s.unlocked} aria-current={shown === s.step ? "step" : undefined}
              title={s.unlocked ? undefined : `Finish step ${s.step - 1} first`}>
              <span className="gf-num" aria-hidden="true">{!s.unlocked ? <Icon name="lock" size={16} /> : s.status === "done" ? <Icon name="check" size={16} /> : s.step}</span>
              <span className="gf-text"><strong>{s.title}</strong><small className="gf-state">{!s.unlocked ? "Locked" : s.status === "done" ? "Done" : s.status === "waiting" ? "Waiting for applicant" : s.status === "in_progress" ? "In progress" : "Not started"}</small></span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function StepSidebar({ steps, shown }: { steps: StepInfo[]; shown: number }) {
  const cur = steps.find((s) => s.step === shown);
  return (
    <aside className="gf-side" aria-label="Progress">
      <h2>Progress</h2>
      <ol>
        {steps.map((s) => (
          <li key={s.step} className={`${s.status} ${shown === s.step ? "shown" : ""}`}>
            <span className="gf-dot" aria-hidden="true">{s.status === "done" ? <Icon name="check" size={13} /> : s.step}</span>
            <span><strong>{s.title}</strong><small>{s.label}</small></span>
          </li>
        ))}
      </ol>
      {cur && cur.status !== "done" && cur.missing.length > 0 && (
        <div className="gf-open">
          <h3>Open items in {cur.title.toLowerCase()}</h3>
          <ul>{cur.missing.map((m) => <li key={m}>{m}</li>)}</ul>
        </div>
      )}
      {cur && cur.status === "done" && <p className="muted">This step is done. Editing it reopens the later steps.</p>}
    </aside>
  );
}

/** The "Next step" button. It stays disabled until the step's conditions are met and says what is missing. */
export function NextStep({ label, missing, busy, onClick, note }: { label: string; missing: string[]; busy?: boolean; onClick: () => void; note?: string }) {
  const blocked = missing.length > 0;
  return (
    <div className="gf-next">
      <div>
        {blocked ? (
          <div id="gf-missing" className="gf-missing"><strong>Still needed before the next step</strong><ul>{missing.map((m) => <li key={m}>{m}</li>)}</ul></div>
        ) : note ? <p className="muted">{note}</p> : null}
      </div>
      <Button disabled={blocked || busy} onClick={onClick} title={blocked ? missing.join(". ") : undefined}>{busy ? "Working…" : label} <Icon name="arrow" /></Button>
    </div>
  );
}
