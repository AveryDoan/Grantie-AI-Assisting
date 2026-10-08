"""Evaluation CLI.

    python -m eval.run                       # Supabase data + configured LLM (GEMINI_API_KEY)
    python -m eval.run --offline             # in-memory seed + offline keyword stub (no network, NOT an LLM)
    python -m eval.run --consistency         # also run the two-wording consistency check
    python -m eval.run --out eval/reports/my_report.md

Results go to evaluation_runs / evaluation_results (unless --no-db-write)
and to a markdown report. Cached LLM responses are reused, so repeat runs do
not burn free-tier limits.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.llm import LLMClient, build_llm_client
from app.llm.cache import MemoryCache, StoreCache
from app.llm.stub import OfflineStubProvider
from app.logging_utils import configure_logging
from eval.harness import run_evaluation

REPORT_DIR = Path(__file__).with_name("reports")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="in-memory seed data + offline stub provider")
    parser.add_argument("--consistency", action="store_true", help="enable the consistency check")
    parser.add_argument("--no-db-write", action="store_true", help="do not write evaluation_runs/results")
    parser.add_argument("--out", type=Path, help="markdown report path (default eval/reports/eval_<timestamp>.md)")
    args = parser.parse_args()

    configure_logging()
    settings = get_settings().model_copy(update={"consistency_check": args.consistency})

    if args.offline:
        from app.store.memory import MemoryStore
        from seed.run import seed

        store = MemoryStore()
        seed(store)
        llm = LLMClient(OfflineStubProvider(), cache=MemoryCache(), temperature=0.0)
        label = "offline keyword stub (NOT an LLM)"
    else:
        from app.store.supabase_store import SupabaseStore

        store = SupabaseStore(settings)
        llm = build_llm_client(settings, cache=StoreCache(store, settings.llm_retention_days))
        label = llm.primary.name

    report = run_evaluation(store, llm, settings, provider_label=label, write_db=not args.no_db_write)
    out = args.out or REPORT_DIR / f"eval_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.markdown(), encoding="utf-8")

    s = report.summary()
    pct = lambda v: "n/a" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    print(f"Provider: {label}")
    print(f"Accuracy {pct(s['accuracy'])} | quote validity {pct(s['quote_validity_rate'])} | "
          f"twin consistency {pct(s['twin_consistency_rate'])} | failures {s['failures']}")
    for inj in s["injection_tests"]:
        print(f"Injection {inj['case_code']}: flagged={inj['flagged']} obeyed={inj['obeyed']}")
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
