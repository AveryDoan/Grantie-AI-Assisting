# Models used by Grantie

| Use | Model | Revision | Licence | Where it runs | Weights in the repo? |
|---|---|---|---|---|---|
| Optional AI-use check (sub-step 3.3), a weak signal only | [`openai-community/roberta-base-openai-detector`](https://huggingface.co/openai-community/roberta-base-openai-detector) | `6cba99c003b711c7fe94f8a3aa2be35a792cb6fa` | MIT | Locally, on CPU, through Hugging Face `transformers` (`local_files_only`). No external call. | No. Downloaded once into the Hugging Face cache. |
| Rule checks, merit summaries, consistency (redacted text only, through `GuardedLLM`) | Configured by `LLM_PROVIDER` and `GEMINI_MODEL` / `GROQ_MODEL` (see `.env.example`) | Provider-managed | Provider terms | External API | No |
| PII detection for redaction | spaCy `en_core_web_lg` 3.8.0 with Presidio | 3.8.0 | MIT | Locally | No |

Notes on the AI-use detector:
- It was trained in 2019 on GPT-2 output. It misses text from current models and often marks plain and second-language
  writing as AI-like. It cannot tell allowed help (spelling, grammar, organising ideas) from disallowed use.
- Its thresholds and whether it is switched on are set only by the fairness evaluation (`python -m eval.ai_use_check`),
  recorded in `config/ai_use_check.yaml`, and explained in `eval/reports/ai_use_check.md`.
- The pinned revision is in `config/ai_use_check.yaml`. To change it, change the config and run the evaluation again.
