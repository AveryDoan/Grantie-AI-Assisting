"""Privacy-safe logging.

Application logs must never contain application text, personal data or API
keys. Log identifiers and counts only. The filter below is a backstop that
masks anything that looks like an email, phone number or key if a developer
logs one by mistake.
"""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b(?:sb_secret|sb_publishable)_[A-Za-z0-9_-]+"), "[key]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "[key]"),
    (re.compile(r"\bgsk_[0-9A-Za-z]{20,}"), "[key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[jwt]"),
    (re.compile(r"(?:\+?61|0)[2-478](?:[ -]?\d){8}"), "[phone]"),
]


def scrub(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(str(record.getMessage()))
        record.args = ()
        return True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, ScrubFilter) for f in logger.filters):
        logger.addFilter(ScrubFilter())
    return logger


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Third-party HTTP clients log full URLs and sometimes bodies at DEBUG.
    for noisy in ("httpx", "httpcore", "google_genai", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
