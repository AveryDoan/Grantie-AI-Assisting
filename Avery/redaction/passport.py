"""Passport / ID-document layer on top of the detector (it does not retune it).

The detector finds names and numbers in free text. Identity documents also
carry the same values in a machine-readable zone (MRZ) and in labelled
fields, often in UPPER CASE or in two languages. This module turns those
into *known values*, exactly like form fields, so the existing pipeline
redacts them everywhere in the document and the leak scan checks for them:

  - every MRZ line (TD1 3x30, TD2 2x36, TD3 2x44)      -> MRZ   (whole line)
  - TD3 parsed: surname + given names                   -> PERSON
                document number                         -> ID
                date of birth (YYMMDD)                  -> DOB
  - labelled fields (same line or next line):
        surname / given names                           -> PERSON
        passport / document / personal number          -> ID
        date of birth                                   -> DOB
        place of birth                                  -> PLACE

Kept on purpose (current policy: only the date of birth is a personal
date; countries are kept): expiry and issue dates, nationality, issuing
country. The 3-letter codes inside the MRZ are hidden with the MRZ line.

Nothing here logs or returns values except as KnownValue objects that go
straight into redaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from redaction.recognizers import KnownValue

MRZ_LINE = re.compile(r"^[A-Z0-9<]{30,44}$")
_MRZ_IN_TEXT = re.compile(r"(?m)^[ \t]*([A-Z0-9<]{30,44})[ \t]*$")
_TD3_1 = re.compile(r"^P[A-Z<]([A-Z<]{3})([A-Z<]{39})$")
_TD3_2 = re.compile(r"^([A-Z0-9<]{9})(\d)([A-Z<]{3})(\d{6})(\d)([MFX<])(\d{6})(\d)([A-Z0-9<]{14})([\d<])(\d)$")

# TD3 line 2: which positions are letters and which are digits (ICAO 9303). OCR often
# swaps look-alikes (O/0, I/1, S/5, B/8, Z/2); a swap is only accepted where the
# position's type requires it, and a field is only used if its check digit validates.
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "G": "6", "B": "8"})
_TO_LETTER = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"})
_TD3_2_DIGITS = [9, *range(13, 20), *range(21, 28), 43]
_TD3_2_LETTERS = [10, 11, 12]


def normalise_td3_line2(line: str) -> str:
    if len(line) != 44:
        return line
    chars = list(line)
    for i in _TD3_2_DIGITS + ([42] if chars[42] != "<" else []):  # personal-number check digit, when present
        chars[i] = chars[i].translate(_TO_DIGIT)
    for i in _TD3_2_LETTERS:
        chars[i] = chars[i].translate(_TO_LETTER)
    return "".join(chars)

# Labels in English plus the most common bilingual forms (value on the same line after
# ':' or '/', or on the next non-empty line).
LABELS: dict[str, list[str]] = {
    "surname": ["surname", "family name", "last name", "nom"],
    "given": ["given names", "given name(s)", "given name", "first names", "forenames", "prénoms"],
    "number": ["passport no", "passport number", "passport no.", "document no", "document number", "personal no",
               "personal number", "no. du passeport"],
    "dob": ["date of birth", "birth date", "date de naissance"],
    "pob": ["place of birth", "lieu de naissance", "birthplace"],
}
KEPT_LABELS = ["date of expiry", "expiry date", "date of issue", "issue date", "nationality", "issuing country",
               "issuing state", "authority", "type", "code", "country code"]


def _check_digit(s: str) -> int:
    weights = (7, 3, 1)
    total = 0
    for i, ch in enumerate(s):
        if ch.isdigit():
            v = int(ch)
        elif ch.isalpha():
            v = ord(ch) - 55
        else:
            v = 0
        total += v * weights[i % 3]
    return total % 10


def _yymmdd(s: str, *, past: bool) -> date | None:
    try:
        yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
        today = date.today()
        century = 2000 if (yy <= today.year % 100 or not past) else 1900
        return date(century + yy, mm, dd)
    except ValueError:
        return None


@dataclass
class PassportParse:
    mrz_lines: list[str] = field(default_factory=list)
    td3_parsed: bool = False
    checks_ok: dict[str, bool] = field(default_factory=dict)   # check-digit results (no values)
    known: list[KnownValue] = field(default_factory=list)
    derived: list[str] = field(default_factory=list)           # which fields became known values (names of fields only)
    kept_labels: list[str] = field(default_factory=list)       # labels deliberately kept (no values)


def _label_value(text: str, labels: list[str]) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.casefold()
        for label in labels:
            m = re.search(rf"(?<!\w){re.escape(label)}(?!\w)", low)
            if not m:
                continue
            rest = line[m.end():]
            if ":" in rest:
                # "Date of birth / Date de naissance: 14 JUL/JUL 1999" - bilingual labels end at the last colon.
                rest = rest.rsplit(":", 1)[1]
                rest = re.split(r"\s{3,}", rest.strip())[0].strip()  # a second column on the same line
                return rest or None
            rest = re.sub(r"^[\s.()]*(?:/\s*)*", "", rest).strip()
            if rest and not _looks_like_label(rest) and not re.search(r"\s/\s", line):
                return re.split(r"\s{3,}", rest)[0].strip()
            for nxt in lines[i + 1:i + 3]:
                nxt = nxt.strip()
                if nxt and not _looks_like_label(nxt) and not MRZ_LINE.match(nxt):
                    return nxt
            return None
    return None


def _looks_like_label(s: str) -> bool:
    low = s.casefold()
    return any(low.startswith(l) for group in LABELS.values() for l in group) or any(low.startswith(l) for l in KEPT_LABELS)


def parse_passport(text: str) -> PassportParse:
    out = PassportParse()
    out.mrz_lines = [m.group(1) for m in _MRZ_IN_TEXT.finditer(text) if m.group(1).count("<") >= 2]
    for line in out.mrz_lines:
        out.known.append(KnownValue(line, "MRZ", "mrz"))
    if out.mrz_lines:
        out.derived.append("mrz_lines")

    # TD3 (passport): two consecutive 44-character lines.
    for a, b in zip(out.mrz_lines, out.mrz_lines[1:]):
        m1, m2 = _TD3_1.match(a), _TD3_2.match(normalise_td3_line2(b))
        if not (m1 and m2):
            continue
        out.td3_parsed = True
        number, num_cd, _nat, dob, dob_cd, _sex, exp, exp_cd = m2.group(1, 2, 3, 4, 5, 6, 7, 8)
        out.checks_ok = {"document_number": _check_digit(number) == int(num_cd),
                         "date_of_birth": _check_digit(dob) == int(dob_cd),
                         "date_of_expiry": _check_digit(exp) == int(exp_cd)}
        names = m1.group(2).rstrip("<")
        surname, _, given = names.partition("<<")
        surname = " ".join(p for p in surname.split("<") if p)
        given = " ".join(p for p in given.split("<") if p)
        full = " ".join(p for p in (given, surname) if p)
        if full:
            out.known.append(KnownValue(full.title(), "PERSON", "mrz", "applicant"))
            out.derived.append("name")
        # Only fields whose check digit validates are used: a misread is never guessed at.
        doc_no = number.replace("<", "")
        if len(doc_no) >= 5 and out.checks_ok["document_number"]:
            out.known.append(KnownValue(doc_no, "ID", "mrz"))
            out.derived.append("document_number")
        born = _yymmdd(dob, past=True) if out.checks_ok["date_of_birth"] else None
        if born:
            out.known.append(KnownValue(born.isoformat(), "DOB", "mrz"))
            out.derived.append("date_of_birth")
        break

    # Labelled fields (visual zone). The raw value is used as written, so a bilingual
    # date like "12 MAR/MAR 2007" is matched exactly as well as by its parsed form.
    surname = _label_value(text, LABELS["surname"])
    given = _label_value(text, LABELS["given"])
    if surname or given:
        name = " ".join(p for p in (given, surname) if p)
        out.known.append(KnownValue(name.title(), "PERSON", "label", "applicant"))
        out.derived.append("name_label")
    for key, ttype, tag in (("number", "ID", "number_label"), ("dob", "DOB", "dob_label"), ("pob", "PLACE", "place_of_birth")):
        v = _label_value(text, LABELS[key])
        if v and len(v) >= 2:
            out.known.append(KnownValue(v, ttype, "label"))
            out.derived.append(tag)
    out.kept_labels = sorted({l for l in KEPT_LABELS if re.search(rf"(?i)\b{re.escape(l)}\b", text)})
    return out


def mrz_lines_in(text: str) -> int:
    """How many MRZ-looking lines are in a text (for the sandbox leak check)."""
    return sum(1 for m in _MRZ_IN_TEXT.finditer(text) if m.group(1).count("<") >= 2)
