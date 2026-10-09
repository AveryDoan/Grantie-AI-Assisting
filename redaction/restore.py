"""Step 5: restore() - swap tokens back for officers only.

Two modes:
- restore(text, token_map, source=...) on a stored redacted text (the whole
  application text, or a whole document): EXACT. Tokens are matched, in
  order, to the recorded occurrences, so "Linh Tran", "TRAN, Linh" and
  "Linh" - all [PERSON_1] - come back exactly as written.
- restore(text, token_map) on any other text (e.g. an AI rationale): each
  token becomes its canonical original (first form seen). A placeholder that
  stands for different originals (two addresses both "outside Australia") is
  left as the placeholder - never guessed. For AI quotes, use the span
  mapping in redaction.spans instead (exact).

Never call this on anything sent to an LLM or written to logs.
"""

from __future__ import annotations

import re

from redaction.tokenizer import TokenMap

TOKEN_RE = re.compile(r"\[(?:[A-Z]+_\d+|LOCATION: [^\]]+)\]")


def display_original(token_map: TokenMap, token: str) -> str | None:
    """The single original to show for a token, or None if it is ambiguous/unknown."""
    entry = token_map.entries.get(token)
    if entry is None:
        return None
    originals = []
    for occs in token_map.occurrences.values():
        for o in occs:
            if o.token == token and o.original not in originals:
                originals.append(o.original)
    if token.startswith("[LOCATION:"):
        return originals[0] if len(originals) == 1 else None
    return entry.canonical if entry.canonical and not entry.canonical.startswith("[") else (originals[0] if originals else None)


def restore(text: str, token_map: TokenMap, source: str | None = None) -> str:
    if source is not None:
        occs = sorted(token_map.occurrences.get(source, []), key=lambda o: o.red_start)
        found = [m for m in TOKEN_RE.finditer(text) if m.group() in token_map.entries]
        if occs and [m.group() for m in found] == [o.token for o in occs]:
            out, pos = [], 0
            for m, o in zip(found, occs):
                out.append(text[pos:m.start()])
                out.append(o.original)
                pos = m.end()
            out.append(text[pos:])
            return "".join(out)

    def swap(m: re.Match[str]) -> str:
        original = display_original(token_map, m.group())
        return original if original is not None else m.group()

    return TOKEN_RE.sub(swap, text)
