# Demo run: Sita Karki (fictional)

Eight synthetic PDFs go through the real applicant upload route, then an officer takes the application through the four steps.
Every file carries the footer "SAMPLE DOCUMENT - synthetic test data only". The two AI Challenge PDFs are not applicant documents and are not loaded.

## Where the files go
- Files: `seed/demo_sita/` (copy only, never edited).
- By script (API running with `APP_MODE=demo LLM_PROVIDER=stub`): `python -m seed.load_demo_applicant --dir seed/demo_sita`
- By hand: sign in as **Sita Karki (fictional)** on the applicant login and upload each file on the documents screen.
- Documents with no matching file stay **Not provided**. Nothing is invented for an empty slot.

## Script
| Step | Do | Look for | Screenshot |
|---|---|---|---|
| 1 Documents | Open the application, Step 1 | Needs-attention first, Not provided slots, drafted (not sent) request, no "same PDF software" signal | `demo_sita/1-documents.png` |
| 2 Redaction | Step 2 | Table by type, leak scan result, AI blocked until Approve | `demo_sita/2-redaction-table.png` |
| 2 Redaction | Open a PDF | Blurred boxes with token on hover, "Show what the AI saw", reveal one item with confirm (audit-logged) | `demo_sita/3-blurred-page.png` |
| 3 Assessment | Eligibility, open S2 | Verified quote highlighted on the PDF | `demo_sita/4-highlighted-quote.png` |
| 3 Assessment | Merit, open a bullet | Bullet opens its source on the PDF | `demo_sita/5-merit-source.png`, `6-merit-summary.png` |
| 3 Assessment | Consistency of information | Dean's Merit Award: **Needs evidence** (2023, 2024) | `demo_sita/7-deans-merit-needs-evidence.png` |
| 3 Assessment | Consistency table | Same values across documents, neutral "Differs, check" | `demo_sita/8-consistency.png` |
| 4 Outcome | Confirm a Not met rule | Decline letter drafted at once, nothing sent, sign-off manual | `demo_sita/9-outcome.png` |

Merit marks (0 to 100) are entered by the officer. The system never fills one, adds them up or ranks.

## Test
`python -m pytest tests/e2e/test_demo_sita.py` loads the eight files and walks Step 1 to Step 4.

## Findings against the brief (shown as the system produced them; data and rules were not changed)
| Brief | Result |
|---|---|
| Dean's Merit 2023/2024 not Not met | Needs evidence (matches) |
| Degree start Jan 2023 vs "since August 2022" | Differs, check, neutral, does not block sign-off (matches) |
| Names, DOB, passport, email, phone consistent | Consistent (matches) |
| Arrival 13 Feb, orientation 15 Feb, start 22 Feb 2027 consistent | Consistent. S2 (course start inside the round window) shows **Not met** because 22 Feb 2027 is outside it |
| Referee 2 duration fits | No conflict raised |
| Shared PDF software / creation time | No signal (`config/document_signals.yaml`) |

Differences from what the brief expected:
- The application-responses PDF is free text, so many form rules (S5, S6, S7, S11, S15, S16) read "Needs evidence" instead of a value.
- The offline stub LLM produces generic wording (S8, referee fields D4/D5, bullet text). A real model gives better prose; code checks are the same.
- Highlights for passages that come from the form PDF are matched by words and shown as approximate (dashed), never as exact.
- The student-ID containing "2022" is not used as a signal.
- Letters have no PDF export; redacted documents and the evidence pack are exported with burned-in redaction.
- The leak scan first failed on this pack (names read as places, CoE and booking numbers not recognised). Fixed with detectors in `config/redaction.yaml`, not by loosening the scan.
