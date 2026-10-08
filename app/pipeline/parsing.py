"""Deterministic parsing of dates and amounts (principle 1: code, not the LLM)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_DAY_FIRST = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"]
_OTHER = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B, %Y",
    "%A %d %B %Y",
]


def _clean(s: str) -> str:
    s = s.strip().rstrip(".")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    s = s.replace(",", " ").replace("Sept ", "Sep ")
    return re.sub(r"\s+", " ", s)


def parse_date(value: str | None) -> date | None:
    """Parse common Australian and ISO formats. Numeric dates are day-first."""
    if not value:
        return None
    s = _clean(str(value))
    for fmt in _OTHER + _DAY_FIRST:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def date_candidates(value: str | None) -> set[date]:
    """All plausible readings, including month-first for ambiguous numerics.

    Used for tolerance when comparing a typed date with a document: a value
    that matches under either reading is a format variant, not a mismatch.
    """
    out: set[date] = set()
    d = parse_date(value)
    if d:
        out.add(d)
    if value:
        s = _clean(str(value))
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
            try:
                out.add(datetime.strptime(s, fmt).date())
            except ValueError:
                pass
    return out


def parse_amount(value: str | int | float | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).strip().lower().replace("aud", "").replace("$", "").replace(",", "").strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k)?", s)
    if not m:
        return None
    try:
        amount = Decimal(m.group(1))
    except InvalidOperation:
        return None
    return amount * 1000 if m.group(2) else amount
