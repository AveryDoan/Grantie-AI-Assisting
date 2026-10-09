# Redaction sandbox (local only)

A place to drop one document and watch the redaction pipeline process it, step by step. It is an inspection tool, separate from the app and the database.

- **Fully local.** No LLM, no Supabase, no cloud service. While a command runs, every network connection is refused.
- **Uses the real pipeline**: the detector, tokens, leak scan, encryption, restore and span mapping in `redaction/`, plus the passport layer in `redaction/passport.py`. The detector is not retuned for the document.
- **Never committed.** `sandbox/input/` and `sandbox/output/` are in `.gitignore`. Only this README is tracked.

## Before you use a real document

This repository lives in a **OneDrive-synced folder**. Anything placed in `sandbox/input/` here is uploaded to OneDrive before the sandbox even runs, and `.gitignore` does not stop that. The sandbox therefore **refuses cloud-synced folders** (OneDrive, iCloud, Dropbox, Google Drive).

For a real document, use a plain local folder:

```bash
export GRANTIE_SANDBOX_DIR=~/grantie-sandbox
mkdir -p ~/grantie-sandbox/input
# copy the document straight into ~/grantie-sandbox/input/ (not via a synced folder)
```

`--allow-cloud-sync` exists for synthetic test files only.

## Commands

Run from the repository root with the project's Python environment (for example `conda activate grantie`). `REDACTION_KEY` must be set in `.env`; the sandbox stops if it is missing and never creates or prints a key.

```bash
# Redact one document (PDF with a text layer, or .txt)
python -m redaction.sandbox run ~/grantie-sandbox/input/passport.pdf

# Add what the form would know (typed automatically; or name=… dob=… id=… place=… country=…)
python -m redaction.sandbox run ~/grantie-sandbox/input/passport.pdf --known "Full Name" 1999-07-14 country=Vietnam

# Also re-insert one original value and watch the leak scan block it (in memory only)
python -m redaction.sandbox run ~/grantie-sandbox/input/passport.pdf --inject-test

# Officer-side restore: decrypt the token map and rebuild the original exactly
python -m redaction.sandbox restore ~/grantie-sandbox/output/doc-xxxxxxxxxx

# Delete everything in input/ and output/ (asks you to type WIPE)
python -m redaction.sandbox wipe
```

Images (JPG/PNG) and PDFs without a text layer stop with "No text layer found. Local OCR is needed". The sandbox never guesses at text in images, and there is no cloud OCR.

## What you get (`output/doc-<hash>/`)

Folder names come from a hash of the file's content, never from the file name (file names often contain names).

| File | Contains personal values? | What |
|---|---|---|
| `1_original_view.html` | **Yes, officer only** | The extracted text with every detection highlighted by type |
| `2_redacted.txt` | No | Exactly what an LLM would receive |
| `3_report.html` | Shows the original in a frame | Side by side (original \| redacted), tokens and counts, needed information kept, two-pass consistency, location class, MRZ, leak scan and the inject test |
| `token_map.enc` | Encrypted | AES-256-GCM token map (`REDACTION_KEY`) |
| `meta.json` | No | Counts, statuses and hashes only |
| `4_restored_view.html` | **Yes, officer only** | Written by `restore` |
| `output/index.html` | No | A list of processed documents; double-click to open |

The terminal shows tokens, counts and PASS/FAIL only, never values or file names.

## Limits

This is not a guarantee. Read the redacted text before relying on it. In particular:

- **Scans and photos are not read.** Most real passports are scans, so expect the OCR message unless the PDF has real text.
- **Names:** an unusual layout, a name split over lines, or a name that is also an English word can be missed or over-matched.
- **Dates:** upper-case month abbreviations (for example `MAR`) can be wrongly tagged as places, which damages the issue and expiry dates.
- **Not redacted:** sex/gender markers and the issuing country code (outside the MRZ).
- **Non-English labels and text** are handled only for common English/French passport labels.
- **Deleting:** `wipe` overwrites files before deleting them, but SSDs, APFS snapshots, Time Machine and cloud sync can keep older copies.
