"""Step 6: map a span in REDACTED text to the matching span in the ORIGINAL.

The AI's quotes are verified against the redacted text, so they contain
tokens. Officers must see the applicant's real words. Using the recorded
occurrences (positions of every replacement in both texts) the mapping is
exact, even when one token stands for different spellings.

Rules:
- A position outside any token shifts by the length difference of all the
  replacements before it.
- A span that starts or ends INSIDE a token is widened to the whole token, so
  a partial token never yields half a name.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from rapidfuzz import fuzz

from redaction.tokenizer import Occurrence, TokenMap


@dataclass(frozen=True)
class MappedQuote:
    red_start: int
    red_end: int
    orig_start: int
    orig_end: int
    original: str
    method: str  # exact | normalised | fuzzy


class SpanMapper:
    def __init__(self, token_map: TokenMap, source: str) -> None:
        self.occs: list[Occurrence] = sorted(token_map.occurrences.get(source, []), key=lambda o: o.red_start)
        self._starts = [o.red_start for o in self.occs]

    def _containing(self, pos: int) -> Occurrence | None:
        i = bisect.bisect_right(self._starts, pos) - 1
        if i >= 0 and self.occs[i].red_start <= pos < self.occs[i].red_end:
            return self.occs[i]
        return None

    def _shift(self, pos: int) -> int:
        """Original position for a redacted position that is not inside a token."""
        i = bisect.bisect_right(self._starts, pos) - 1
        while i >= 0 and self.occs[i].red_end > pos:  # occurrences starting at/after pos do not count
            i -= 1
        if i < 0:
            return pos
        o = self.occs[i]
        return o.orig_end + (pos - o.red_end)

    def map_span(self, red_start: int, red_end: int) -> tuple[int, int, int, int]:
        """(red_start, red_end, orig_start, orig_end), widened to whole tokens."""
        if red_end < red_start:
            raise ValueError("end before start")
        first = self._containing(red_start)
        if first is not None:
            red_start, orig_start = first.red_start, first.orig_start
        else:
            orig_start = self._shift(red_start)
        last = self._containing(red_end - 1) if red_end > red_start else None
        if last is not None:
            red_end, orig_end = last.red_end, last.orig_end
        else:
            orig_end = self._shift(red_end)
        return red_start, red_end, orig_start, orig_end


def _norm_with_index(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace and curly quotes; keep a map back to original indexes."""
    trans = {"‘": "'", "’": "'", "“": '"', "”": '"'}
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(text):
        ch = trans.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch.casefold())
        idx.append(i)
    return "".join(out), idx


def locate_quote(quote: str, redacted_text: str, fuzzy_threshold: float = 92.0) -> tuple[int, int, str] | None:
    """Where an AI quote sits in the redacted text: exact, then normalised, then fuzzy."""
    q = quote.strip()
    if not q:
        return None
    at = redacted_text.find(q)
    if at >= 0:
        return at, at + len(q), "exact"
    nt, idx = _norm_with_index(redacted_text)
    nq, _ = _norm_with_index(q)
    at = nt.find(nq)
    if at >= 0:
        return idx[at], idx[at + len(nq) - 1] + 1, "normalised"
    if len(nq) >= 12:
        al = fuzz.partial_ratio_alignment(nq, nt)
        if al is not None and al.score >= fuzzy_threshold:
            return idx[al.dest_start], idx[al.dest_end - 1] + 1, "fuzzy"
    return None


def quote_to_original(
    quote: str, redacted_text: str, original_text: str, token_map: TokenMap, source: str, *, fuzzy_threshold: float = 92.0
) -> MappedQuote | None:
    """The applicant's original words for a quote found in the redacted text (None if not found)."""
    found = locate_quote(quote, redacted_text, fuzzy_threshold)
    if found is None:
        return None
    rs, re_, method = found
    rs, re_, os_, oe = SpanMapper(token_map, source).map_span(rs, re_)
    return MappedQuote(rs, re_, os_, oe, original_text[os_:oe], method)

