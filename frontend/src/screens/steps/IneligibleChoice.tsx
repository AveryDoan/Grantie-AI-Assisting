// After the officer confirms a rule as Not met (3.1 Eligibility), two plain choices. Only the officer's click moves the application
// to Outcome; the AI never does. If some rules are still Needs evidence or Unclear, a one-line question comes first, and the
// officer chooses: ask the applicant for more, or continue anyway. Keyboard: O = go to Outcome now.
import { useEffect, useState } from "react";
import { ApiError, api, type Detail, type Finding } from "../../api";
import { Button, ErrorNotice, Icon } from "../../ui";

export function confirmedNotMet(findings: Finding[]): Finding[] {
  return findings.filter((f) => f.section !== "merit" && f.latest_review && f.latest_review.action !== "ask_applicant" && f.latest_review.final_status === "Not met");
}

export function IneligibleChoice({ detail, onPath, onKeepChecking, onAsk, onOutcome, reload }: {
  detail: Detail; onPath: boolean; onKeepChecking: () => void; onAsk: (codes: string[]) => void; onOutcome: () => void; reload: () => void;
}) {
  const id = detail.application.id;
  const notMet = confirmedNotMet(detail.findings);
  const [warn, setWarn] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const locked = detail.application.status === "signed_off";

  const goNow = async (continueAnyway = false) => {
    setBusy(true); setFailure(null);
    try {
      await api.goToOutcome(id, continueAnyway);
      reload(); onOutcome();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && Array.isArray(e.details?.could_change)) setWarn(e.details.could_change as string[]);
      else setFailure(e);
    } finally { setBusy(false); }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if ((e.key === "o" || e.key === "O") && notMet.length && !locked && !busy) { e.preventDefault(); if (onPath) onOutcome(); else void goNow(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!notMet.length || locked) return null;
  if (onPath) {
    return (
      <section className="ie-choice done" aria-label="Not eligible">
        <p><Icon name="check" /> <strong>Not eligible (your decision).</strong> Consistency, the AI-use check and merit are recorded as Not assessed (ineligible).</p>
        <Button onClick={onOutcome}>Open Outcome <Icon name="arrow" /></Button>
      </section>
    );
  }
  const n = warn?.length ?? 0;
  return (
    <section className="ie-choice" aria-label="You confirmed a rule as Not met">
      <h3><Icon name="info" /> You confirmed {notMet.length === 1 ? `rule ${notMet[0].rule_code}` : `${notMet.length} rules`} as Not met</h3>
      <div className="ie-options">
        <div className="ie-option">
          <Button variant="secondary" onClick={onKeepChecking}>Keep checking the other rules</Button>
          <small><b>Recommended.</b> So the letter can list every reason in one go.</small>
        </div>
        <div className="ie-option">
          <Button variant="secondary" disabled={busy} onClick={() => void goNow()}>Go to Outcome now <kbd>O</kbd></Button>
          <small>The other checks aren’t needed to decline this application.</small>
        </div>
      </div>
      {warn && (
        <div className="ie-warn" role="alert">
          <p><Icon name="question" /> {n} {n === 1 ? "finding" : "findings"} could change this if the applicant sent more information ({warn.join(", ")}). Ask for it first?</p>
          <div className="ie-actions">
            <Button variant="secondary" icon="send" onClick={() => onAsk(warn)}>Ask applicant for more</Button>
            <Button variant="quiet" disabled={busy} onClick={() => void goNow(true)}>Continue to Outcome anyway</Button>
          </div>
        </div>
      )}
      <ErrorNotice error={failure} />
    </section>
  );
}
