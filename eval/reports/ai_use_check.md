# AI-use check: fairness evaluation

Run 2026-10-12 with `openai-community/roberta-base-openai-detector` (revision `6cba99c003b7`), locally on CPU. Every number below is printed as measured.

## Result

- **Check enabled: NO.**
- Second-language flag rate 0.0% vs polished English 0.0%: a gap of 0.0 points (limit 2). Rate test: passed.
- Human sets really human-written: **no**.

> The "human" sets (a), (b) and (c) are synthetic: an AI assistant wrote them to imitate each writing style. They are
> not human writing, so this run cannot show how real applicants are treated. The check stays off until the sets are
> replaced with consented, de-identified human answers and the evaluation is run again.

Thresholds set by this run: Weak signal from **0.9411**, Stronger signal from **0.9962** (mean detector output across a text's windows).
Stronger is the lowest value that flags at most 2% of every human group; Weak (our assumption) at most 10%.

## Per group

| Group | Texts | Flagged (Weak or Stronger) | Stronger signal | Provenance |
|---|---|---|---|---|
| (a) Human, polished English | 12 | 0.0% | 0.0% | synthetic: written by an AI assistant (Claude) to imitate this writing style. NOT human-written. |
| (b) Human, plain English | 12 | 8.3% | 0.0% | synthetic: written by an AI assistant (Claude) to imitate this writing style. NOT human-written. |
| (c) Human, second-language English | 12 | 0.0% | 0.0% | synthetic: written by an AI assistant (Claude) to imitate this writing style. NOT human-written. |
| (d) Written by a current LLM | 10 | 0.0% | 0.0% | llm:gemini:gemini-3.5-flash |
| (e) Human text, grammar edited by an LLM | 1 | 0.0% | 0.0% | synthetic: written by an AI assistant (Claude) to imitate this writing style. NOT human-written. Grammar edited by llm:gemini:gemini-3.5-flash. |

Detection rate on (d), text written by a current LLM: **0.0%** flagged, 0.0% as Stronger signal.
Human text edited by an LLM for grammar only (e): **0.0%** flagged. Under the form's rule, grammar help is allowed, so any flag here is a false alarm.

## Limits

- Small samples (a: 12, b: 12, c: 12, d: 10, e: 1 texts). With 12 texts, one text is 8.3 points, so a 2% rate can only be met by flagging none.
- The thresholds are set on the same texts they are then measured on (in-sample). Real rates on new text will be higher.
- The detector was trained on GPT-2 output (2019). It is known to miss text from current models and to mark plain and
  second-language writing as AI-like. It cannot tell allowed help (spelling, grammar, organising ideas) from disallowed use.

## Raw scores

Mean detector output per text, for audit. Never shown to officers or applicants.

- (a): 0.000, 0.001, 0.000, 0.000, 0.000, 0.000, 0.001, 0.000, 0.000, 0.001, 0.007, 0.001
- (b): 0.941, 0.004, 0.015, 0.035, 0.001, 0.672, 0.996, 0.003, 0.001, 0.001, 0.331, 0.000
- (c): 0.000, 0.000, 0.002, 0.000, 0.000, 0.000, 0.000, 0.000, 0.002, 0.000, 0.001, 0.031
- (d): 0.000, 0.000, 0.001, 0.000, 0.000, 0.001, 0.000, 0.000, 0.000, 0.000
- (e): 0.281
