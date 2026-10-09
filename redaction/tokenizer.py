"""Consistent tokens and the token map.

- The same original value always maps to the same token within one
  application, and the same PERSON maps to one token across its forms
  ("Linh Tran", "TRAN, Linh", "Linh"), so the LLM can follow who is who.
- Numbering is deterministic per type: order of first appearance in a fixed
  traversal order (structured fields, then answers, then documents by id).
- Every replacement is recorded as an Occurrence with its exact original
  text and positions in both texts. That makes restore() exact even when one
  token stands for several spellings, and lets quote spans be mapped back.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from redaction.config import RedactionConfig


@dataclass
class TokenEntry:
    token: str
    token_type: str
    canonical: str                                   # first original seen (officer display)
    variants: list[str] = field(default_factory=list)  # every distinct original spelling
    sources: list[str] = field(default_factory=list)   # field names / document ids (never values)


@dataclass(frozen=True)
class Occurrence:
    token: str
    original: str
    source: str
    red_start: int   # span of the token in the redacted text
    red_end: int
    orig_start: int  # span of the original in the original text
    orig_end: int


def _fold(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^\w@.+]+", " ", s.casefold()).strip()


def _name_parts(s: str) -> frozenset[str]:
    return frozenset(p for p in _fold(s).split() if len(p) > 1)


class TokenMap:
    """Token registry for ONE application."""

    def __init__(self, cfg: RedactionConfig) -> None:
        self.cfg = cfg
        self.entries: dict[str, TokenEntry] = {}
        self.occurrences: dict[str, list[Occurrence]] = {}  # source -> ordered occurrences
        self._counters: dict[str, int] = {}
        self._by_key: dict[tuple[str, str], str] = {}        # (type, normalised key) -> token
        self._people: list[tuple[str, frozenset[str], str]] = []  # (group, name parts, token)

    # ---- token allocation -------------------------------------------------
    def _new(self, token_type: str, original: str) -> str:
        n = self._counters.get(token_type, 0) + 1
        self._counters[token_type] = n
        token = self.cfg.format_token(token_type, n)
        self.entries[token] = TokenEntry(token, token_type, original)
        return token

    def _person_token(self, token_type: str, original: str, group: str | None) -> str:
        parts = _name_parts(original)
        if group:  # a known person (applicant, guardian...): every form shares one token
            for g, _, tok in self._people:
                if g == group and self.entries[tok].token_type == token_type:
                    return tok
        for g, known_parts, tok in self._people:
            if self.entries[tok].token_type != token_type or not parts:
                continue
            # Same set of name parts in any order, or a single part of exactly one known person.
            if parts == known_parts or (len(parts) == 1 and parts <= known_parts
                                        and sum(1 for _, kp, _t in self._people if parts <= kp) == 1):
                if group and g and g != group:
                    continue
                return tok
        tok = self._new(token_type, original)
        self._people.append((group or "", parts, tok))
        return tok

    def token_for(self, token_type: str, original: str, source: str, group: str | None = None) -> str:
        if token_type in ("PERSON", "REFEREE"):
            token = self._person_token(token_type, original, group)
        elif token_type == "ADDRESS" and original.startswith("[LOCATION:"):
            token = original  # coarse placeholder text is its own token
            self.entries.setdefault(token, TokenEntry(token, "ADDRESS", original))
        else:
            if token_type in ("PHONE", "ID"):
                norm = re.sub(r"[\s\-().]", "", _fold(original))
            elif token_type == "DOB":  # one date of birth, whatever its format
                from redaction.recognizers import parse_any_date

                parsed = parse_any_date(original)
                norm = parsed.isoformat() if parsed else _fold(original)
            else:
                norm = _fold(original)
            key = (token_type, norm)
            token = self._by_key.get(key) or self._new(token_type, original)
            self._by_key[key] = token
        entry = self.entries[token]
        if original not in entry.variants:
            entry.variants.append(original)
        if source not in entry.sources:
            entry.sources.append(source)
        return token

    # ---- applying replacements ---------------------------------------------
    def apply(self, text: str, replacements: list[tuple[int, int, str, str, str | None]], source: str) -> str:
        """Replace spans in `text`. Each replacement: (start, end, token_type, display_original, group).

        Spans must not overlap. Returns the redacted text and records occurrences.
        """
        out: list[str] = []
        occs: list[Occurrence] = []
        pos = 0
        red_len = 0
        for start, end, token_type, original, group in sorted(replacements, key=lambda r: r[0]):
            chunk = text[pos:start]
            out.append(chunk)
            red_len += len(chunk)
            token = self.token_for(token_type, original, source, group)
            occs.append(Occurrence(token, text[start:end], source, red_len, red_len + len(token), start, end))
            out.append(token)
            red_len += len(token)
            pos = end
        out.append(text[pos:])
        self.occurrences[source] = occs
        return "".join(out)

    # ---- reporting ---------------------------------------------------------
    def counts(self) -> dict[str, int]:
        """Number of replacements per token type. Counts only - no values."""
        out: dict[str, int] = {}
        for occs in self.occurrences.values():
            for o in occs:
                t = self.entries[o.token].token_type
                out[t] = out.get(t, 0) + 1
        return dict(sorted(out.items()))

    def known_originals(self) -> list[str]:
        return [v for e in self.entries.values() for v in e.variants]
