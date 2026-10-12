"""AI-use check: a WEAK signal for the officer, from a local detector model. It decides nothing.

Background: the Study NT form says applicants should only use AI for spelling, grammar and organising ideas, not to write,
improve or paraphrase their answers. This check runs the open-source RoBERTa "OpenAI detector" (MIT licence, pinned
revision, CPU, no external call) over the applicant's free-text answers and maps the average score to one of four neutral
results. The model was trained on older AI text and cannot tell allowed use (spelling, grammar, organising ideas) from
disallowed use; people who write plainly or in a second language are often marked "AI-like" by tools like this. So:
- it only runs when an officer asks, and only when the rule pack's fairness gate switched it on;
- it reads REDACTED text, with every redaction token replaced by a neutral placeholder;
- fields under the word minimum are not checked ("Too short to check");
- its result never changes a rule status, a merit mark, a letter or the outcome.

The weights are not in the repository. Download them once (about 500 MB, into the Hugging Face cache):
    python -m app.pipeline.ai_use --download
After that the model loads with local_files_only=True: nothing is sent anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "ai_use_check.yaml"
RESULTS = ("Too short to check", "No signal", "Weak signal", "Stronger signal")
_TOKEN = re.compile(r"\[([A-Z][A-Z_]*?)_\d+\]")
_PLACEHOLDER = {"PERSON": "the person", "LOCATION": "the place", "GPE": "the place", "EMAIL": "the email", "PHONE": "the number",
                "INTERNATIONAL_PHONE": "the number", "ORG": "the organisation", "DATE": "the date"}
# A detector score: the probability the model gives to its "Fake" (machine-written) label for one window of text.
Scorer = Callable[[list[str]], list[float]]


class DetectorUnavailable(RuntimeError):
    """The model is not downloaded or the libraries are not installed. Messages never contain application text."""


def load_config(path: Path | None = None) -> dict[str, Any]:
    p = path or CONFIG_PATH
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.exists() else {}


def neutral(text: str) -> str:
    """Redaction tokens become plain neutral words, so the detector reads ordinary prose and never sees a token."""
    return _TOKEN.sub(lambda m: _PLACEHOLDER.get(m.group(1), "the detail"), text or "")


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def windows(text: str, count_tokens: Callable[[str], int], limit: int = 400) -> list[str]:
    """Consecutive sentences grouped into windows of at most `limit` model tokens. A single sentence longer than the limit
    is its own window (the model truncates it)."""
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for s in sentences(text):
        n = count_tokens(s)
        if cur and size + n > limit:
            out.append(" ".join(cur))
            cur, size = [], 0
        cur.append(s)
        size += n
    if cur:
        out.append(" ".join(cur))
    return out


def band(score: float | None, thresholds: dict[str, float]) -> str:
    """The neutral result for a mean score. No percentage is ever shown to anyone."""
    if score is None:
        return RESULTS[0]
    if score >= thresholds["stronger"]:
        return "Stronger signal"
    if score >= thresholds["weak"]:
        return "Weak signal"
    return "No signal"


@dataclass
class FieldResult:
    field: str
    words: int
    result: str
    windows: int = 0
    scores: list[float] = field(default_factory=list)
    mean: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "words": self.words, "result": self.result, "windows": self.windows,
                "scores": [round(s, 4) for s in self.scores], "mean": None if self.mean is None else round(self.mean, 4)}


def check_fields(texts: dict[str, str], *, scorer: Scorer, count_tokens: Callable[[str], int], thresholds: dict[str, float],
                 min_words: int = 80, window_tokens: int = 400) -> list[FieldResult]:
    """One result per configured field. `texts` is REDACTED text; it is made neutral here before the detector sees it."""
    out = []
    for name, text in texts.items():
        clean = neutral(text)
        words = word_count(clean)
        if words < min_words:
            out.append(FieldResult(name, words, RESULTS[0]))
            continue
        wins = windows(clean, count_tokens, window_tokens)
        scores = scorer(wins) if wins else []
        mean = sum(scores) / len(scores) if scores else None
        out.append(FieldResult(name, words, band(mean, thresholds), len(wins), scores, mean))
    return out


# ---------------------------------------------------------------- the local model

@lru_cache(maxsize=1)
def _pipeline(model: str, revision: str) -> Any:
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline
    except ImportError as exc:   # pragma: no cover - depends on the install
        raise DetectorUnavailable("Install the AI-use check extras: pip install -e '.[ai_check]'") from exc
    try:
        tok = AutoTokenizer.from_pretrained(model, revision=revision, local_files_only=True)
        net = AutoModelForSequenceClassification.from_pretrained(model, revision=revision, local_files_only=True)
        return pipeline("text-classification", model=net, tokenizer=tok, device=-1, top_k=None)
    except Exception as exc:
        raise DetectorUnavailable("The detector model is not downloaded. Run: python -m app.pipeline.ai_use --download") from exc


def local_detector(cfg: dict[str, Any] | None = None) -> tuple[Scorer, Callable[[str], int], dict[str, Any]]:
    """(scorer, token counter, model info) for the pinned local model. CPU only; no network."""
    import os

    cfg = cfg or load_config()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")   # never fetch anything while checking an application
    pipe = _pipeline(cfg["model"], cfg["revision"])
    tok = pipe.tokenizer

    def count(text: str) -> int:
        return len(tok.encode(text, add_special_tokens=False))

    def score(texts: list[str]) -> list[float]:
        out = pipe(texts, truncation=True, max_length=512)
        return [next(x["score"] for x in row if x["label"].lower() == "fake") for row in out]

    return score, count, {"model": cfg["model"], "revision": cfg["revision"], "licence": cfg.get("licence")}


def download(cfg: dict[str, Any] | None = None) -> str:
    """Fetch the pinned model once into the Hugging Face cache (outside the repository)."""
    from huggingface_hub import snapshot_download

    cfg = cfg or load_config()
    return snapshot_download(cfg["model"], revision=cfg["revision"],
                             allow_patterns=["config.json", "merges.txt", "vocab.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"])


if __name__ == "__main__":   # python -m app.pipeline.ai_use --download
    import sys

    if "--download" in sys.argv:
        print(download())
