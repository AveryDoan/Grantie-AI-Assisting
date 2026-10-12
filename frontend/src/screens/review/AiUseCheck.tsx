// Sub-step 3.3, AI-use check. Filled in by part 2.
import type { Detail } from "../../api";

export function AiUseCheck(_props: { detail: Detail; locked: boolean; onChanged: () => void }) {
  return <section className="rv-section" aria-label="AI-use check"><p className="empty-state">The AI-use check is not available.</p></section>;
}
