# Consistency layer evaluation

Signals, never verdicts: these flags point at things an officer should check. They are not scores, ratings or recommendations.

**Read this first.**
- The 11 cases (F01 to F10 and one clean N case) were **written alongside the checks**. They show that each check can find a
  problem it was built to find, not how it performs on real applications.
- The cases are small and synthetic (fictional names, example.com addresses, ACMA-reserved phone numbers).
- The offline stub is a pattern matcher, **not an LLM**, and was written for these cases. The Gemini run is the only
  AI-assisted result that says anything about a real model, and even it is 11 cases.
- A real pool, real scans, real edited PDFs and real template letters will produce more false positives than this.

<!-- run:stub -->

## Run: offline keyword stub (NOT an LLM)

Generated 2026-10-09 09:42 UTC. 11 cases assessed.

The AI-assisted checks ran on the offline stub: pattern matching written alongside these cases. These numbers show the plumbing works (quotes, verification, persistence); they say nothing about a real model.

### Per check type

| Check type | Expected flags | Raised (recall) | Missed | Flags raised | Unexpected | Precision |
|---|---|---|---|---|---|---|
| cross_document | 5 | 5/5 (100%) | 0 | 6 | 0 | 6/6 (100%) |
| timeline | 3 | 3/3 (100%) | 0 | 3 | 0 | 3/3 (100%) |
| document_integrity | 2 | 2/2 (100%) | 0 | 3 | 0 | 3/3 (100%) |
| cross_application | 11 | 11/11 (100%) | 0 | 11 | 0 | 11/11 (100%) |
| narrative | 2 | 2/2 (100%) | 0 | 2 | 0 | 2/2 (100%) |

Precision here is the share of flags raised that the case expected. Flags a case lists as allowed (weak signals or a near-duplicate of an expected one) count as expected.

### Per case (planted problems)

| Case | Should raise | Raised it | Missed | Unexpected flags | Strong | Unclear |
|---|---|---|---|---|---|---|
| F01 | `cross_document.arrival_vs_start` | 1/1 | - | - | 1 | 0 |
| F02 | `cross_document.coe_length`, `cross_document.typed_coe_end`, `cross_document.referee_date_window` | 3/3 | - | - | 3 | 0 |
| F03 | `cross_document.known_for_vs_timeline`, `narrative.conflicting_statements` | 2/2 | - | - | 2 | 0 |
| F04 | `timeline.role_before_age`, `timeline.overlapping_full_time`, `timeline.visa_before_coe` | 3/3 | - | - | 3 | 0 |
| F05 | `narrative.conflicting_statements` | 1/1 | - | - | 1 | 0 |
| F06 | `document_integrity.created_after_dated`, `document_integrity.editing_software` | 2/2 | - | - | 0 | 0 |
| F07 | `cross_application.shared_referee_phone`, `cross_application.shared_referee_email_domain`, `cross_application.reused_wording`, `cross_application.shared_contact_phone` | 4/4 | - | - | 3 | 0 |
| F08 | `cross_application.shared_referee_phone`, `cross_application.shared_referee_email_domain`, `cross_application.reused_wording` | 3/3 | - | - | 2 | 0 |
| F09 | `cross_application.shared_referee_phone`, `cross_application.shared_referee_email_domain`, `cross_application.reused_wording`, `cross_application.shared_contact_phone` | 4/4 | - | - | 3 | 0 |

### Controls (precision)

Innocent cases. They must raise **no strong flag**. Weak signals are listed because they are the false-positive risk (scans, re-saved PDFs, a short agreed late arrival).

| Case | Strong flags | Weak flags raised | Unexpected | Result |
|---|---|---|---|---|
| F10 | 0 | `cross_document.arrival_vs_start`, `document_integrity.created_after_dated` | - | pass |
| N08 | 0 | - | - | pass |

Flags a case lists as ones it should NOT raise, but did: none.

AI items dropped because their quote could not be verified in the text: **0**. Items kept only as 'unclear' (no quotes shown): **0**.
<!-- /run:stub -->
