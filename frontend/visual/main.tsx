// Visual regression harness (tests/visual.test.ts): every combination of state for the big step bar (full and slim strip) and
// for the sub-step cards, so the test can check that all items share one top and one height. Not part of the app bundle.
import { createRoot } from "react-dom/client";
import type { StepInfo, SubStep, SubStepKey } from "../src/api";
import "../src/index.css";
import "../src/officer.css";
import "../src/tokens.css";
import "../src/theme.css";
import { StepBar } from "../src/screens/steps/parts";
import { SubStepBar } from "../src/screens/steps/SubStepBar";

const TITLES = ["Documents", "Redaction check", "Assessment", "Outcome"];
const steps = (active: number, lockedFrom: number): StepInfo[] => TITLES.map((title, i) => {
  const step = (i + 1) as 1 | 2 | 3 | 4;
  const unlocked = step < lockedFrom;
  const status = !unlocked ? "not_started" : step < active ? "done" : step === active ? "in_progress" : "not_started";
  return { step, key: title.toLowerCase(), title, status, label: status === "done" ? "Done" : status === "in_progress" ? "In progress" : "Not started",
    unlocked, missing: [], notice: null } as StepInfo;
});
const KEYS: SubStepKey[] = ["eligibility", "consistency", "ai_use", "merit"];
const subs = (states: string[]): SubStep[] => KEYS.map((key, i) => ({
  key, number: `3.${i + 1}`, title: ["Eligibility", "Consistency", "AI-use check", "Merit"][i], count: states[i] === "done" ? 0 : 12 - i * 3, total: 23,
  status: states[i] === "done" ? (i === 1 || i === 2 ? "reviewed" : "done") : "in_progress", blocking: i === 0 || i === 3,
  unlocked: states[i] !== "locked", missing: null,
}) as SubStep);

const groups: JSX.Element[] = [];
for (let active = 1; active <= 4; active++) {
  for (let lockedFrom = active + 1; lockedFrom <= 5; lockedFrom++) {
    for (const compact of [false, true]) {
      groups.push(<div key={`b${active}${lockedFrom}${compact}`} data-group={`bar a${active} lockedFrom${lockedFrom} ${compact ? "strip" : "full"}`} data-item={compact ? ".gf-chip button" : ".gf-step button"}>
        <StepBar steps={steps(active, lockedFrom)} shown={active} onGo={() => undefined} compact={compact} />
      </div>);
    }
  }
}
const STATES = [["todo", "todo", "todo", "todo"], ["done", "todo", "todo", "todo"], ["done", "done", "done", "todo"], ["todo", "locked", "locked", "locked"], ["done", "done", "done", "done"]];
STATES.forEach((st, si) => KEYS.forEach((shown) => groups.push(
  <div key={`s${si}${shown}`} data-group={`sub ${st.join(",")} shown=${shown}`} data-item=".ss-card button">
    <SubStepBar subs={subs(st)} shown={shown} onGo={() => undefined} />
  </div>,
)));

createRoot(document.getElementById("root")!).render(<main className="officer-shell" style={{ display: "block", padding: 24, width: 1180 }}>{groups}</main>);
