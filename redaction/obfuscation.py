"""Catch personal values hidden by encoding or character tricks.

Two checks, both run by Detector.detect:
  encoded_runs   long runs that are an encoding of something (base64, \\u/\\x/&#/0x/% escapes, hex, binary, Morse,
                 Braille, spelled-out digit groups). Genuine applications contain none, and an LLM can decode them,
                 so the whole run is redacted whatever it hides.
  cleaned_tokens words with junk or look-alike characters inserted (emoji, Chinese characters, "_", circled digits,
                 "%40" for "@", reversed emails). The cleaned word is re-analysed and a hit redacts the original word.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from urllib.parse import unquote

_RUNS: list[tuple[str, re.Pattern[str]]] = [
    ("unicode_escape", re.compile(r"(?:\\u[0-9a-fA-F]{4}){2,}")),
    ("byte_escape", re.compile(r"(?:\\x[0-9a-fA-F]{2}){2,}")),
    ("html_entity", re.compile(r"(?:&#[xX]?[0-9a-fA-F]+;){2,}")),
    ("percent_escape", re.compile(r"(?:%[0-9a-fA-F]{2}){3,}")),
    ("hex_bytes", re.compile(r"(?:\b0[xX][0-9a-fA-F]{2}\b[ ,]*){3,}")),
    ("binary", re.compile(r"(?<![\w.])(?:[01]{4,8}[ ]){3,}[01]{4,8}(?![\w.])")),
    ("morse", re.compile(r"(?<![\w.])(?:[.\-·]{1,6}[ ]){3,}[.\-·]{1,6}(?![\w])")),
    ("braille", re.compile(r"[⠀-⣿]{3,}")),
    ("digit_groups", re.compile(r"(?<![\w.,/-])(?:\d{1,2}[ ]){9,}\d{1,2}(?![\w.,/-])")),
    ("cjk_numerals", re.compile(r"[零壹貳贰參叁肆伍陸陆柒捌玖〇]{3,}")),
    ("hex_blob", re.compile(r"(?<![\w-])(?=[0-9a-fA-F]*\d)(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{12,}(?![\w-])")),
]
_B64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{8,}={0,2}(?![A-Za-z0-9+/=])")


def _b64_ok(tok: str) -> bool:
    if len(tok.rstrip("=")) % 4 == 1:
        return False
    try:
        raw = base64.b64decode(tok + "=" * (-len(tok) % 4), validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(raw) < 6:
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(c.isprintable() or c in "\r\n\t" for c in text)


def encoded_runs(text: str) -> list[tuple[int, int, str]]:
    out = [(m.start(), m.end(), kind) for kind, p in _RUNS for m in p.finditer(text)]
    out += [(m.start(), m.end(), "base64") for m in _B64.finditer(text) if _b64_ok(m.group(0))]
    return out


# ---- look-alike and inserted characters --------------------------------------------------------------------------

_DIGITS = {}
for _i, _c in enumerate("⓪①②③④⑤⑥⑦⑧⑨"):
    _DIGITS[_c] = str(_i)
for _i, _c in enumerate("➀➁➂➃➄➅➆➇➈"):
    _DIGITS[_c] = str(_i + 1)
for _i, _c in enumerate("➊➋➌➍➎➏➐➑➒"):
    _DIGITS[_c] = str(_i + 1)
for _i, _c in enumerate("❶❷❸❹❺❻❼❽❾"):
    _DIGITS[_c] = str(_i + 1)
for _i, _c in enumerate("零壹贰叁肆伍陆柒捌玖"):
    _DIGITS[_c] = str(_i)
_DIGITS.update({"貳": "2", "參": "3", "陸": "6", "〇": "0"})
_JUNK_ASCII = set("_%#*~!^$&=|?\\`'\"<>;")
_REVERSED_EMAIL = re.compile(r"^\S*(?:moc|ten|gro|ude|ua|ku)\.[^@\s]+@\S+$", re.I)


def _clean(token: str) -> str:
    t = html.unescape(unquote(token))
    t = unicodedata.normalize("NFKC", "".join(_DIGITS.get(c, c) for c in t))
    ascii_alnum = sum(c.isascii() and c.isalnum() for c in t)
    if ascii_alnum < 3:
        return token
    keep = []
    for c in t:
        cat = unicodedata.category(c)
        cjk = "一" <= c <= "鿿"
        if cat in ("So", "Sk", "Cs", "Cn", "Cf") or c in _JUNK_ASCII or (cjk and ascii_alnum >= 3 and len(t) < 40):
            continue
        keep.append(c)
    t = "".join(keep)
    if _REVERSED_EMAIL.match(t):
        t = t[::-1]
    return t


def cleaned_tokens(text: str) -> tuple[str, list[tuple[int, int, int, int]]]:
    """(text with dirty tokens replaced by their clean form, [(new_start, new_end, old_start, old_end)]).
    Returns ("", []) when no token changed."""
    parts, spans, pos, cur = [], [], 0, 0
    for m in re.finditer(r"\S+", text):
        clean = _clean(m.group(0))
        if clean == m.group(0):
            continue
        parts.append(text[pos:m.start()]); cur += m.start() - pos
        parts.append(clean)
        spans.append((cur, cur + len(clean), m.start(), m.end()))
        cur += len(clean); pos = m.end()
    if not spans:
        return "", []
    parts.append(text[pos:])
    return "".join(parts), spans
