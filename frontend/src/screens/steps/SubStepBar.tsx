// The sub-steps of Assessment (3.1 to 3.4): the main navigation inside step 3. Four equal cards in one row, each in its section
// colour (Eligibility teal, Consistency slate, AI-use sky blue, Merit amber), joined by chevrons so the order reads left to
// right. Counts and statuses come from the backend (step 3 "substeps"). No side stripes: tinted fill and a full border.
import type { SubStep, SubStepKey } from "../../api";
import { Icon } from "../../ui";

export const SUB_SEC: Record<SubStepKey, string> = { eligibility: "sec-elig", consistency: "sec-consistency", ai_use: "sec-ai", merit: "sec-merit" };

export function subStatusLine(s: SubStep): string {
  if (!s.unlocked) return "Locked";
  if (s.status === "done") return "Complete";
  if (s.status === "reviewed") return "Reviewed";
  if (!s.blocking) return "Optional";
  return s.count ? `${s.count} to do` : "Not started";
}

export function SubStepBar({ subs, shown, onGo }: { subs: SubStep[]; shown: SubStepKey; onGo: (k: SubStepKey) => void }) {
  return (
    <nav className="ss-bar" aria-label="Assessment sub-steps">
      <ol style={{ gridTemplateColumns: `repeat(${subs.length}, minmax(0, 1fr))` }}>
        {subs.map((s) => {
          const state = !s.unlocked ? "locked" : shown === s.key ? "active" : s.status === "done" || s.status === "reviewed" ? "done" : "todo";
          return (
            <li key={s.key} className={`ss-card ${SUB_SEC[s.key]} ${state}`} data-substep={s.key}>
              <button onClick={() => onGo(s.key)} disabled={!s.unlocked} aria-current={shown === s.key ? "step" : undefined}
                title={s.unlocked ? undefined : "Run the AI check first"} data-count={s.count}>
                <span className="ss-badge" aria-hidden="true">
                  {state === "locked" ? <Icon name="lock" size={18} /> : state === "done" ? <Icon name="check" size={20} /> : s.number}
                </span>
                <span className="ss-text"><strong>{s.title}</strong><small>{subStatusLine(s)}</small></span>
                {s.count > 0 && <span className="ss-count" aria-label={`${s.count} open`}>{s.count}</span>}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
