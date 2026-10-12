// Layout harness (tests/spacing.test.ts): the real class names and stylesheets, with fixed content. A sticky header, an
// Eligibility-like list with filter chips, shortcut hints and a table, a plain panel, and the rule drawer with its sticky action bar.
import { createRoot } from "react-dom/client";
import "../src/index.css";
import "../src/officer.css";
import "../src/tokens.css";
import "../src/theme.css";
import "../src/dashboard.css";
import "../src/outcomes.css";
import "../src/layout.css";

const rows = Array.from({ length: 12 }, (_, i) => (
  <tr key={i} className="rv-row" tabIndex={0}><td><strong>S{i + 1}</strong></td><td>Course starts between 1 October 2026 and 31 December 2026</td><td>Met</td></tr>
));
const lines = Array.from({ length: 40 }, (_, i) => <p key={i}>Line {i + 1} of the evidence that the officer reads in the panel.</p>);

createRoot(document.getElementById("root")!).render(
  <div className="officer-shell" style={{ display: "block" }}>
    <header className="workspace-topbar" data-header><strong>Header</strong></header>
    <div className="workspace-content">
      <div className="rv-page has-panel">
        <div className="rv-layout">
          <div className="rv-main">
            <section className="rv-section" aria-label="Eligibility" data-card>
              <div className="rv-section-body">
                <div className="rv-toolrow">
                  <div className="rv-filters"><button className="rv-filter on">All <strong>16</strong></button><button className="rv-filter">Needs me <strong>4</strong></button><button className="rv-filter">Decided <strong>12</strong></button></div>
                  <details className="rv-shortcuts"><summary>Shortcuts</summary><small className="rv-hints"><kbd>J</kbd><kbd>K</kbd> next · <kbd>C</kbd> confirm · <kbd>Esc</kbd> close</small></details>
                  <div className="rv-toolbtns"><button className="button button-secondary">Review next</button><button className="button button-secondary">Ask applicant for more</button></div>
                </div>
                <div className="rv-table-wrap"><table className="rv-table"><thead><tr><th>Rule</th><th>What it checks</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>
              </div>
            </section>
            <section className="panel" data-card>
              <div className="panel-heading"><div><h2>Documents</h2><p>Check each document.</p></div></div>
              <label className="field"><span>Note</span><textarea rows={3} defaultValue="A note" /></label>
              <pre className="doc-text gf-redtext">Redacted text with [PERSON_1] in it.</pre>
              <div className="rv-table-wrap"><table className="rv-table"><tbody>{rows.slice(0, 3)}</tbody></table></div>
            </section>
          </div>
          <aside className="rv-panel sec-elig" aria-label="Details: Rule D1" data-drawer data-card>
            <div className="rv-panel-head"><div><h2>Rule D1</h2></div><span className="rv-head-btns"><button className="icon-button rv-expand" data-close aria-label="Close">x</button></span></div>
            <div className="rv-panel-nav"><button className="button button-secondary">Previous (K)</button><span>1 of 16</span><button className="button button-secondary">Next (J)</button></div>
            <div className="rv-panel-body">
              <section className="rv-psec"><h3>Evidence</h3>{lines}</section>
              <div className="rule-actions" data-actions><button className="button">Confirm met</button><button className="button button-secondary">Override</button><button className="button button-quiet">Ask applicant</button></div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  </div>,
);
