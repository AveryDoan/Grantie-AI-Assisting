"""Small deterministic helpers shared by the consistency checks (plain code, no LLM)."""

from __future__ import annotations

import re
from datetime import date, datetime

from app.pipeline.parsing import parse_date

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
MONTH_RE = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
# "15 March 2021", "March 2021", "Mar 2021", "2021-03", "03/2021", "2021"
WHEN_RE = rf"(?:\d{{1,2}}(?:st|nd|rd|th)?\s+)?{MONTH_RE}\.?,?\s+\d{{4}}|\d{{4}}-\d{{2}}(?:-\d{{2}})?|\d{{1,2}}/\d{{4}}|\d{{4}}"

_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20}


def parse_when(value: str | None, *, end: bool = False) -> date | None:
    """A date from a day, a month or just a year. A bare month is its first day (its last when end=True)."""
    if not value:
        return None
    s = re.sub(r"\s+", " ", value.strip().rstrip(".,"))
    full = parse_date(s)
    if full:
        return full
    m = re.fullmatch(rf"({MONTH_RE})\.?,? (\d{{4}})", s, flags=re.I)
    if m:
        month, year = _MONTHS[m.group(1)[:3].lower()], int(m.group(2))
        return _month_edge(year, month, end)
    m = re.fullmatch(r"(\d{4})-(\d{2})", s) or re.fullmatch(r"(\d{1,2})/(\d{4})", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        year, month = (a, b) if a > 31 else (b, a)
        return _month_edge(year, month, end) if 1 <= month <= 12 else None
    if re.fullmatch(r"\d{4}", s):
        return date(int(s), 12, 31) if end else date(int(s), 1, 1)
    return None


def _month_edge(year: int, month: int, end: bool) -> date:
    if not end:
        return date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def months_between(a: date, b: date) -> float:
    return (b - a).days / 30.4375


def years_between(a: date, b: date) -> float:
    return (b - a).days / 365.25


def count_from(token: str) -> float | None:
    t = token.strip().lower()
    if t in _WORDS:
        return float(_WORDS[t])
    try:
        return float(t)
    except ValueError:
        return None


def parse_duration_months(text: str | None) -> float | None:
    """'five years', '5 years', '8 months', 'two and a half years' -> months. First match wins."""
    if not text:
        return None
    t = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?|[a-z]+)\s+and\s+a\s+half\s+year", t)
    if m and (n := count_from(m.group(1))) is not None:
        return (n + 0.5) * 12
    m = re.search(r"(\d+(?:\.\d+)?|[a-z]+)\s*(years?|yrs?|months?|semesters?)\b", t)
    if not m or (n := count_from(m.group(1))) is None:
        return None
    unit = m.group(2)
    return n * 12 if unit.startswith(("year", "yr")) else n * 6 if unit.startswith("semester") else n


def line_matching(text: str, pattern: str) -> str | None:
    """The first line of `text` matching `pattern`, exactly as written (so it can be quoted and verified)."""
    for line in text.splitlines():
        if re.search(pattern, line, re.IGNORECASE):
            return line.strip()
    return None


def sentence_matching(text: str, pattern: str) -> str | None:
    """The first sentence matching `pattern`, as written."""
    flat = re.sub(r"\s*\n\s*", " ", text)
    for s in re.split(r"(?<=[.!?])\s+", flat):
        if re.search(pattern, s, re.IGNORECASE):
            return s.strip()
    return None


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def today_or(d: date | datetime | None) -> date:
    if isinstance(d, datetime):
        return d.date()
    return d or date.today()


def reference(application_id: str) -> str:
    """The short reference an officer sees (same format as the queue)."""
    return "APP-" + application_id.replace("-", "")[:6].upper()
