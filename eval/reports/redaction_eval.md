# Redaction evaluation report

Generated 2026-10-09 06:59 UTC in 5 s. Detector `presidio-2.2.364+spacy-3.8+en_core_web_lg-3.8.0+custom-1`, config `c68750c709ac`.

**Synthetic data only.** Every name, number and address below is fictional. This report shows how the redaction behaves on a small hand-made set; it is not a guarantee for real applications. Redaction cannot promise zero leaks.

## Summary

| Measure | Result |
|---|---|
| Labelled personal values (present in the input) | 95 |
| **Recall** (values fully removed from the redacted text) | 95/95 (100.0%) |
| Values left in the redacted text (any part) | 0 |
| ...of which would actually reach the LLM (leak scan did not block) | **0** |
| **Over-redaction** (needed terms wrongly removed) | 0/128 (0.0%) |
| **Unexpected redactions** (probable over-redaction, listed below) | 8 |
| Cases blocked by the leak scan (no LLM call) | 0/24 |

Name recall (fully covered): detector only 95/96 (99.0%); with known values 96/96 (100.0%).

Findings changed by redaction on the twin set: 0/84 rule checks.

## Results by entity type

| Type | Present | Removed | Leaked | Reached LLM | Recall |
|---|---|---|---|---|---|
| ADDRESS | 10 | 10 | 0 | 0 | 100% |
| DOB | 10 | 10 | 0 | 0 | 100% |
| EMAIL | 12 | 12 | 0 | 0 | 100% |
| HANDLE | 1 | 1 | 0 | 0 | 100% |
| ID | 2 | 2 | 0 | 0 | 100% |
| PERSON | 23 | 23 | 0 | 0 | 100% |
| PHONE | 13 | 13 | 0 | 0 | 100% |
| PLACE | 3 | 3 | 0 | 0 | 100% |
| REFEREE | 20 | 20 | 0 | 0 | 100% |
| URL | 1 | 1 | 0 | 0 | 100% |

## Every miss (personal value left in the redacted text)

None in this set.

## Over-redaction

Terms the rules need. Removing one changes what the AI can judge (for example a provider name or a start date).

None of the 128 checked terms were removed.

### Unexpected redactions (probable over-redaction)

Text replaced by a token that is neither a labelled personal value nor something the case says may rightly be redacted (an address city, a CoE code). Each one is a word the AI no longer sees.

| Case | Token type | Original text |
|---|---|---|
| N06 | PERSON | điểm |
| N06 | PERSON | học |
| N06 | PERSON | năm |
| N06 | PLACE | trường trung học |
| N06 | PERSON | Hà Nội học |
| N06 | PERSON | học vật lý hóa học |
| N06 | PERSON | Anh điểm trung bình chín phẩy năm xếp |
| N06 | PERSON | loại giỏi |

Expected extra redactions (not counted): E0000000 (ID), Test Applicant (PERSON), Hanoi (PLACE), Lagos (PLACE), Nairobi (PLACE).

## Name recall by culture

Each name in each sentence template. *Detector only* = a person the system was not told about (e.g. someone mentioned in an answer). *Known values* = the applicant's own or a referee's name from the form.

| Group | Detector: full | partial | missed | Known values: full |
|---|---|---|---|---|
| Vietnamese (family first) | 8/9 | 0 | 1 | 9/9 |
| Chinese (family first) | 9/9 | 0 | 0 | 9/9 |
| Korean (family first, hyphenated) | 6/6 | 0 | 0 | 6/6 |
| Japanese (family first) | 3/3 | 0 | 0 | 3/3 |
| Indian | 9/9 | 0 | 0 | 9/9 |
| Nepali | 6/6 | 0 | 0 | 6/6 |
| Sri Lankan | 3/3 | 0 | 0 | 3/3 |
| Bangladeshi | 3/3 | 0 | 0 | 3/3 |
| Filipino (multi-part) | 3/3 | 0 | 0 | 3/3 |
| Indonesian (single name) | 6/6 | 0 | 0 | 6/6 |
| Arabic (particles, hyphen) | 6/6 | 0 | 0 | 6/6 |
| Persian | 3/3 | 0 | 0 | 3/3 |
| Nigerian | 6/6 | 0 | 0 | 6/6 |
| Kenyan | 3/3 | 0 | 0 | 3/3 |
| Thai | 3/3 | 0 | 0 | 3/3 |
| Mongolian | 3/3 | 0 | 0 | 3/3 |
| Spanish (hyphenated) | 3/3 | 0 | 0 | 3/3 |
| Pacific | 6/6 | 0 | 0 | 6/6 |
| Anglo | 6/6 | 0 | 0 | 6/6 |

Not fully covered:

| Sentence | Detector only | Known values |
|---|---|---|
| I worked with Le Hoang Nam at the clinic last year. | missed | full |

## Findings before and after redaction (twin set)

Same facts written polished (N01), plain (N08) and in second-language English (N09). Assessed by the **offline keyword stub, not an LLM**, so this checks that redaction does not remove anything the rules and quote checks need; it says nothing about a real model.

| Case | Rule checks | Changed by redaction | Matches answer key (before → after) |
|---|---|---|---|
| N01 | 28 | 0 | 28 → 28 |
| N08 | 28 | 0 | 28 → 28 |
| N09 | 28 | 0 | 28 → 28 |

Twin consistency after redaction: 0 rule(s) differ across the three styles.

## Known limits

- Names in free text are found by patterns and a statistical model; names from some cultures, single names and unusual orders can be missed (see the tables above). Known values (form fields) are the safety net only for the applicant and named referees.
- Leak scanning catches what it can recognise (emails, phones, long numbers, known values, anything already redacted elsewhere). It cannot catch an unknown name that the detector also missed.
- Free text can identify someone indirectly (a rare job, a small town, a family story) without any value above.
- Scanned documents, images, signatures, handwriting and headshots are not read (no OCR); they are flagged for manual review and never sent to the AI.
- This set is small and hand-made, and the detector was tuned while looking at it (for example the sentence-initial name fix for "Sione Tupou" and keeping "NT"). It is not a held-out test, so these numbers are optimistic. Results on real applications will be lower.
- Non-English text is over-redacted: the English NER model tags ordinary words in other languages as names (see N06). The rule is to redact when unsure, so meaning is lost rather than privacy.
