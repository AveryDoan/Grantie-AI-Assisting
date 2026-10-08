"""Deterministic checks used by the Study NT rule pack v2 (rules S1-S16, D1-D7, M5).

Same conventions as app.pipeline.code_checks:
- missing evidence -> "Needs evidence" (never "Not met");
- an official list that has not been loaded -> "Unclear" + error_flag;
- unreadable or ambiguous values -> "Unclear";
- a typed value that disagrees with a document -> "needs verification"
  (Needs evidence / Unclear), never a fraud label or an automatic "Not met".
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from rapidfuzz import fuzz

from app.domain import NOT_STATED, Finding
from app.pipeline.code_checks import DOC_LABEL, CodeContext, _f, _fact, _quote_kw
from app.pipeline.documents import is_english
from app.pipeline.parsing import parse_date

NT_ADDRESS = re.compile(
    r"\b(N\.?T\.?|Northern Territory)\b|\b(Darwin|Palmerston|Alice Springs|Katherine|Tennant Creek|Nhulunbuy|Jabiru|Casuarina)\b",
    re.IGNORECASE,
)
NT_POSTCODE = re.compile(r"\b08\d{2}\b")
AUSTRALIA = {"australia", "au", "aus", "commonwealth of australia"}


def _label(doc_type: str) -> str:
    return DOC_LABEL.get(doc_type, doc_type.replace("_", " "))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().casefold())


def _yes_no(value: str | None) -> str | None:
    if value is None:
        return None
    v = _norm(value)
    if v in {"yes", "y", "true", "i agree", "agreed", "agree"}:
        return "yes"
    if v in {"no", "n", "false"}:
        return "no"
    return None


def _answer(ctx: CodeContext, key: str) -> str | None:
    text = ctx.application.get("application_text") or {}
    for section in ("fields", "answers"):
        v = (text.get(section) or {}).get(key)
        if v not in (None, ""):
            return str(v)
    return None


def _submitted(ctx: CodeContext) -> date | None:
    raw = ctx.application.get("submitted_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return parse_date(str(raw))


_GENERIC = frozenset(
    "bachelor master masters diploma advanced graduate certificate honours associate degree doctor doctorate "
    "of in and the with studies study science sciences arts course program programme level general".split()
)


_SUFFIXES = sorted(
    ["ational", "ations", "ation", "ments", "ment", "ings", "ing", "ists", "ist", "ians", "ian", "ance", "ence",
     "ants", "ant", "ents", "ent", "ors", "or", "ers", "er", "ions", "ion", "ics", "ic", "ed", "al", "es", "s", "e", "y"],
    key=len, reverse=True,
)


def _stem(word: str) -> str:
    """Light suffix stripping, applied until stable: nursing/nurse -> nurs, engineering/engineer -> engine."""
    while True:
        for suf in _SUFFIXES:
            if word.endswith(suf) and len(word) - len(suf) >= 4:
                word = word[: -len(suf)]
                break
        else:
            return word


def _stems(text: str) -> set[str]:
    """Meaningful word stems, ignoring generic course words (Bachelor, Studies, ...)."""
    words = [w for w in re.findall(r"[a-z]+", text.casefold()) if w not in _GENERIC and len(w) > 3]
    return {_stem(w) for w in words}


def _list_or_error(ctx: CodeContext, name: str) -> tuple[list[str] | None, Finding | None]:
    items = ctx.reference_lists.get(name)
    if not items:
        return None, _f(
            ctx, "Unclear", f"This rule needs the official {name.replace('_', ' ')}, which has not been loaded yet.",
            error_flag=True, error_detail=f"Reference list not loaded: {name}", confidence="low", is_valid=False,
        )
    return items, None


# ---------------------------------------------------------------- documents


def document_field_in_list(ctx: CodeContext) -> Finding:
    """S1 / S3: a value on a document must appear on an official list."""
    p = ctx.rule.params
    docs = ctx.documents.by_type(p["document_type"])
    if not docs:
        return _f(ctx, "Needs evidence", f"No {_label(p['document_type'])} was provided.", needs_applicant_clarification=True)
    value = next((d.extracted_fields.get(p["field"]) for d in docs if d.extracted_fields.get(p["field"])), None)
    if not value:
        return _f(ctx, "Unclear", f"The {p['field'].replace('_', ' ')} could not be read from the {_label(p['document_type'])}.",
                  confidence="low")
    items, early = _list_or_error(ctx, p["list"])
    if early:
        return early
    if p.get("match") == "stem":
        # A course leads to an occupation: link them by shared word stems, never by guesswork.
        want = _stems(value)
        hits = [i for i in items if want & _stems(i)]
        if hits:
            shown = "; ".join(hits[:4]) + (f" (+{len(hits) - 4} more)" if len(hits) > 4 else "")
            return _f(ctx, "Met", f'The course "{value}" relates to listed occupation(s): {shown}. Confirm the course leads to one of them.',
                      confidence="medium")
        return _f(ctx, p.get("not_found_status", "Unclear"),
                  f'No occupation on the {p["list"].replace("_", " ")} clearly matches the course "{value}". Check which occupation it leads to.',
                  confidence="low")
    best = max(items, key=lambda i: fuzz.token_set_ratio(_norm(i), _norm(value)))
    if fuzz.token_set_ratio(_norm(best), _norm(value)) >= p.get("match_threshold", 90):
        return _f(ctx, "Met", f'The {_label(p["document_type"])} shows "{value}", which is on the {p["list"].replace("_", " ")} ("{best}").')
    return _f(ctx, p.get("not_found_status", "Unclear"),
              f'The {_label(p["document_type"])} shows "{value}", which was not found on the {p["list"].replace("_", " ")}. '
              "Check whether it appears under a different name.", confidence="medium")


def document_date_in_window(ctx: CodeContext) -> Finding:
    """S2: a document date must fall inside a window (e.g. course start in Round 1)."""
    p = ctx.rule.params
    start, end = parse_date(p["start"]), parse_date(p["end"])
    docs = ctx.documents.by_type(p["document_type"])
    if not docs:
        return _f(ctx, "Needs evidence", f"No {_label(p['document_type'])} was provided.", needs_applicant_clarification=True)
    iso = next((d.extracted_fields.get(f"{p['date_field']}_iso") for d in docs if d.extracted_fields.get(f"{p['date_field']}_iso")), None)
    if not iso:
        return _f(ctx, "Needs evidence", f"The {p['date_field'].replace('_', ' ')} could not be read from the {_label(p['document_type'])}.",
                  needs_applicant_clarification=True, confidence="medium")
    d = date.fromisoformat(iso)
    inside = start <= d <= end
    return _f(ctx, "Met" if inside else "Not met",
              f"The {_label(p['document_type'])} shows {p['date_field'].replace('_', ' ')} {d:%d %B %Y}; "
              f"the allowed window is {start:%d %B %Y} to {end:%d %B %Y}.")


def document_present_right_type(ctx: CodeContext) -> Finding:
    """D1: the document is uploaded and is the right type."""
    t = ctx.rule.params["document_type"]
    if ctx.documents.by_type(t):
        return _f(ctx, "Met", f"A {_label(t)} was uploaded and looks like the right document.")
    wrong = [c for c in ctx.documents.checks if c.declared_type == t and c.detected_type != t]
    if wrong:
        return _f(ctx, "Needs evidence", f"A file was uploaded as a {_label(t)} but looks like a {_label(wrong[0].detected_type)}.",
                  needs_applicant_clarification=True)
    return _f(ctx, "Needs evidence", f"No {_label(t)} was uploaded.", needs_applicant_clarification=True)


def document_count(ctx: CodeContext) -> Finding:
    """D3: at least N documents of a type."""
    p = ctx.rule.params
    n = len(ctx.documents.by_type(p["document_type"]))
    if n >= p["min"]:
        return _f(ctx, "Met", f"{n} {_label(p['document_type'])}s were uploaded (at least {p['min']} needed).")
    return _f(ctx, "Needs evidence", f"{n} {_label(p['document_type'])}(s) uploaded; {p['min']} are needed.",
              needs_applicant_clarification=True)


def document_field_matches(ctx: CodeContext) -> Finding:
    """S12: e.g. study load on the CoE is full-time."""
    p = ctx.rule.params
    docs = ctx.documents.by_type(p["document_type"])
    value = next((d.extracted_fields.get(p["field"]) for d in docs if d.extracted_fields.get(p["field"])), None)
    if value is None:
        value = _answer(ctx, p.get("typed_field", p["field"]))
        if value is None:
            return _f(ctx, "Needs evidence", f"The {p['field'].replace('_', ' ')} is not shown.", needs_applicant_clarification=True)
    if re.search(p["fail_pattern"], value, re.IGNORECASE):
        return _f(ctx, "Not met", f'The {p["field"].replace("_", " ")} is given as "{value}".')
    if re.search(p["pass_pattern"], value, re.IGNORECASE):
        return _f(ctx, "Met", f'The {p["field"].replace("_", " ")} is given as "{value}".')
    return _f(ctx, "Unclear", f'The {p["field"].replace("_", " ")} is given as "{value}"; it is not clear whether this is full-time.',
              needs_applicant_clarification=True, confidence="medium")


def student_visa(ctx: CodeContext) -> Finding:
    """S9: a valid Student Visa of the required subclass."""
    p = ctx.rule.params
    docs = ctx.documents.by_type("visa")
    if not docs:
        return _f(ctx, "Needs evidence", "No visa grant notice was provided.", needs_applicant_clarification=True)
    f = docs[0].extracted_fields
    subclass = f.get("visa_subclass")
    if not subclass:
        return _f(ctx, "Unclear", "The visa subclass could not be read from the visa grant notice.", confidence="low")
    if not re.search(rf"\b{re.escape(str(p['required_subclass']))}\b", subclass):
        return _f(ctx, "Not met", f'The visa grant notice shows subclass "{subclass}"; subclass {p["required_subclass"]} is required.')
    expiry, submitted = f.get("visa_expiry_date_iso"), _submitted(ctx)
    if not expiry:
        return _f(ctx, "Unclear", "The visa expiry date could not be read.", confidence="low")
    if submitted and date.fromisoformat(expiry) < submitted:
        return _f(ctx, "Not met", f"The visa expired on {date.fromisoformat(expiry):%d %B %Y}, before the application date.")
    return _f(ctx, "Met", f"Subclass {p['required_subclass']} visa, valid until {date.fromisoformat(expiry):%d %B %Y}.")


def arrival_evidence(ctx: CodeContext) -> Finding:
    """D2: a booking or itinerary showing the arrival date (a screenshot is not enough)."""
    p = ctx.rule.params
    bookings = ctx.documents.by_type(p["booking_type"])
    if not bookings:
        if ctx.documents.by_type(p["reject_type"]):
            return _f(ctx, "Needs evidence", "A screenshot of a flight was uploaded. A booking or itinerary is needed; a screenshot is not enough.",
                      needs_applicant_clarification=True)
        return _f(ctx, "Needs evidence", "No travel booking or itinerary was uploaded.", needs_applicant_clarification=True)
    iso = next((b.extracted_fields.get("arrival_date_iso") for b in bookings if b.extracted_fields.get("arrival_date_iso")), None)
    if not iso:
        return _f(ctx, "Unclear", "A booking was uploaded but the arrival date could not be read from it.", confidence="low")
    typed = parse_date(_answer(ctx, p["fact"]))
    booked = date.fromisoformat(iso)
    if typed and typed != booked:
        return _f(ctx, "Unclear",
                  f"Needs verification: the booking shows arrival on {booked:%d %B %Y} but the form says {typed:%d %B %Y}.",
                  needs_applicant_clarification=True, confidence="medium")
    return _f(ctx, "Met", f"The booking shows arrival on {booked:%d %B %Y}.")


def documents_english(ctx: CodeContext) -> Finding:
    """D7: documents are in English, or a certified translation is provided."""
    p = ctx.rule.params
    skip = {p["translation_type"], "headshot"}
    non_english = [c for c in ctx.documents.checks if c.detected_type not in skip
                   and is_english(next((d.get("extracted_text") or "" for d in ctx.raw_documents if d["id"] == c.document_id), "")) is False]
    if not non_english:
        return _f(ctx, "Met", "All documents appear to be in English.")
    names = ", ".join(next(d["file_name"] for d in ctx.raw_documents if d["id"] == c.document_id) for c in non_english)
    if ctx.documents.by_type(p["translation_type"]):
        return _f(ctx, "Unclear", f"Not in English: {names}. A translation was uploaded; an officer must check it is certified and matches the original.",
                  confidence="medium")
    return _f(ctx, "Needs evidence", f"Not in English: {names}. A certified translation and the original are needed.",
              needs_applicant_clarification=True)


# ---------------------------------------------------------------- referee letters


def _referee_letters(ctx: CodeContext) -> tuple[list[Any], Finding | None]:
    letters = ctx.referees or []
    if not letters:
        return [], _f(ctx, "Needs evidence", "No referee letters were uploaded.", needs_applicant_clarification=True)
    failed = [l for l in letters if l.error]
    if failed:
        return [], _f(ctx, "Unclear", "The referee letters could not be read automatically. An officer needs to read them.",
                      error_flag=True, error_detail=f"Referee extraction failed: {failed[0].error}", confidence="low", is_valid=False)
    return letters, None


def referee_fields(ctx: CodeContext) -> Finding:
    """D4: each letter states the required details (text). Signature and letterhead are checked by eye."""
    from app.pipeline.referees import REQUIRED_TEXT_FIELDS

    letters, early = _referee_letters(ctx)
    if early:
        return early
    gaps = []
    for i, letter in enumerate(letters, start=1):
        missing = [k.replace("_", " ") for k in REQUIRED_TEXT_FIELDS if not letter.stated(k)]
        if missing:
            gaps.append(f"letter {i}: {', '.join(missing)}")
    if gaps:
        return _f(ctx, "Needs evidence", "Missing from the referee letters - " + "; ".join(gaps) + ".",
                  needs_applicant_clarification=True, confidence="medium")
    sig = sum(l.stated("signature") for l in letters)
    head = sum(l.stated("letterhead") for l in letters)
    return _f(ctx, "Met",
              f"Each letter states the referee's name, position, organisation, relationship, length of association and date. "
              f"The text mentions a signature on {sig} of {len(letters)} and a letterhead on {head} of {len(letters)}: "
              "check the signature and letterhead by eye.", confidence="medium")


def referee_dates(ctx: CodeContext) -> Finding:
    """D5: each letter is dated within the allowed years."""
    p = ctx.rule.params
    letters, early = _referee_letters(ctx)
    if early:
        return early
    start, end = parse_date(p["start"]), parse_date(p["end"])
    dates, unreadable = [], 0
    for letter in letters:
        f = letter.fields.get("letter_date")
        d = parse_date(f.fact_value) if f and f.fact_value != NOT_STATED and f.quote_verified else None
        if d is None:
            unreadable += 1
        else:
            dates.append(d)
    outside = [d for d in dates if not start <= d <= end]
    if outside:
        return _f(ctx, "Not met", f"A referee letter is dated {outside[0]:%d %B %Y}; letters must be dated {start:%Y} to {end:%Y}.")
    if unreadable:
        return _f(ctx, "Unclear", f"{unreadable} referee letter(s) have no readable date.", needs_applicant_clarification=True,
                  confidence="low")
    submitted = _submitted(ctx)
    old = [d for d in dates if submitted and (submitted - d).days > 730]
    note = " One is older than 24 months, which the form asks to avoid where possible." if old else ""
    return _f(ctx, "Met", f"All referee letters are dated between {start:%Y} and {end:%Y}.{note}")


# ---------------------------------------------------------------- typed facts


def days_before(ctx: CodeContext) -> Finding:
    """S5: the application was submitted at least N days before arrival."""
    p = ctx.rule.params
    value, early = _fact(ctx, p["fact"])
    if early:
        return early
    arrival, submitted = parse_date(value), _submitted(ctx)
    if arrival is None:
        return _f(ctx, "Unclear", f"The arrival date given ({value}) could not be read as a date.", confidence="low", **_quote_kw(ctx, p["fact"]))
    if submitted is None:
        return _f(ctx, "Unclear", "The application has no submission date.", confidence="low")
    gap = (arrival - submitted).days
    ok = gap >= p["min_days"]
    return _f(ctx, "Met" if ok else "Not met",
              f"Submitted {submitted:%d %B %Y}; arrival {arrival:%d %B %Y} is {gap} days later (at least {p['min_days']} needed).",
              **_quote_kw(ctx, p["fact"]))


def country_not_in(ctx: CodeContext) -> Finding:
    """S6: the applicant lives outside Australia when applying."""
    p = ctx.rule.params
    value, early = _fact(ctx, p["fact"])
    if early:
        return early
    if _norm(value) in AUSTRALIA:
        return _f(ctx, "Not met", f"The residential address is in {value}.", **_quote_kw(ctx, p["fact"]))
    postal = _answer(ctx, p.get("secondary_fact", ""))
    if postal and _norm(postal) in AUSTRALIA:
        return _f(ctx, "Needs evidence", f"Needs verification: the residential address is in {value} but the postal address is in Australia.",
                  needs_applicant_clarification=True, confidence="medium")
    return _f(ctx, "Met", f"The residential address is in {value}, outside Australia.", **_quote_kw(ctx, p["fact"]))


def address_not_in_nt(ctx: CodeContext) -> Finding:
    """S7: the applicant is not already living in the NT."""
    p = ctx.rule.params
    residential = _answer(ctx, p["residential_fact"])
    if not residential:
        return _f(ctx, "Needs evidence", "No residential address was given.", needs_applicant_clarification=True)

    def in_nt(addr: str) -> bool:
        return bool(NT_ADDRESS.search(addr)) or (bool(NT_POSTCODE.search(addr)) and "australia" in addr.lower())

    if in_nt(residential):
        return _f(ctx, "Not met", "The residential address is in the Northern Territory.")
    postal = _answer(ctx, p.get("postal_fact", ""))
    if postal and in_nt(postal):
        return _f(ctx, "Needs evidence", "Needs verification: the postal address is in the Northern Territory.",
                  needs_applicant_clarification=True, confidence="medium")
    return _f(ctx, "Met", "Neither the residential nor the postal address is in the Northern Territory.")


def fact_equals(ctx: CodeContext) -> Finding:
    """S11, S15: a yes/no answer. `fail_status` lets S15 flag rather than fail."""
    p = ctx.rule.params
    value = _answer(ctx, p["fact"])
    if value is None:
        return _f(ctx, "Needs evidence", f"The question \"{p.get('question', p['fact'])}\" was not answered.",
                  needs_applicant_clarification=True)
    yn = _yes_no(value)
    if yn == p["pass_value"]:
        return _f(ctx, "Met", f"Answered \"{value}\" to \"{p.get('question', p['fact'])}\".")
    if yn is None:
        return _f(ctx, "Unclear", f"The answer \"{value}\" to \"{p.get('question', p['fact'])}\" is not a clear yes or no.",
                  needs_applicant_clarification=True, confidence="low")
    return _f(ctx, p.get("fail_status", "Not met"), p.get("fail_note") or f"Answered \"{value}\" to \"{p.get('question', p['fact'])}\".",
              confidence="medium" if p.get("fail_status") == "Unclear" else "high")


def declaration_complete(ctx: CodeContext) -> Finding:
    """S16: the declaration is agreed, named and dated."""
    p = ctx.rule.params
    agreed = _yes_no(_answer(ctx, p["agree_field"]))
    missing = [label for key, label in (("name_field", "name"), ("date_field", "date")) if not _answer(ctx, p[key])]
    if agreed != "yes":
        missing.insert(0, "agreement")
    if missing:
        return _f(ctx, "Needs evidence", "The declaration is incomplete: missing " + ", ".join(missing) + ".",
                  needs_applicant_clarification=True)
    return _f(ctx, "Met", "The declaration is agreed, named and dated.")


def register_none(ctx: CodeContext) -> Finding:
    """S10, S13, S14: records check against the (mock) register."""
    p = ctx.rule.params
    rows = [r for r in ctx.register_rows if r.get("record_type") == p["record_type"] and r.get("status") == "active"]
    match = [m.casefold() for m in p.get("match", [])]
    if match:
        rows = [r for r in rows if any(m in (r.get("record_name") or "").casefold() for m in match)]
    allowed = [a.casefold() for a in p.get("allowed", [])]
    blocking = [r for r in rows if (r.get("record_name") or "").casefold() not in allowed]
    allowed_found = [r for r in rows if (r.get("record_name") or "").casefold() in allowed]
    if blocking:
        names = ", ".join(sorted({r.get("record_name") or "unnamed record" for r in blocking}))
        return _f(ctx, "Not met", f"The {p['record_type']} records show: {names}.")
    note = f" (allowed: {', '.join(r['record_name'] for r in allowed_found)})" if allowed_found else ""
    return _f(ctx, "Met", f"No conflicting {p['record_type']} records were found{note}.")


def word_count_and_photo(ctx: CodeContext) -> Finding:
    """D6: biography of about N words, plus a headshot (the photo itself is checked by eye)."""
    p = ctx.rule.params
    bio = _answer(ctx, p["field"])
    if not bio:
        return _f(ctx, "Needs evidence", "No biography was provided.", needs_applicant_clarification=True)
    if not ctx.documents.by_type(p["photo_type"]):
        return _f(ctx, "Needs evidence", "No headshot photo was uploaded.", needs_applicant_clarification=True)
    words = len(re.findall(r"\b\w+\b", bio))
    lo, hi = p["target_words"] - p["tolerance"], p["target_words"] + p["tolerance"]
    if not lo <= words <= hi:
        return _f(ctx, "Unclear", f"The biography has {words} words; about {p['target_words']} are asked for.",
                  needs_applicant_clarification=True, confidence="medium")
    return _f(ctx, "Met", f"The biography has {words} words and a headshot was uploaded (check the photo by eye).")


def text_length(ctx: CodeContext) -> Finding:
    """M5: the answer is given and within the character limit."""
    p = ctx.rule.params
    text = _answer(ctx, p["field"])
    if not text:
        return _f(ctx, "Needs evidence", "This question was not answered.", needs_applicant_clarification=True)
    n = len(text)
    if n > p["max_chars"]:
        return _f(ctx, "Not met", f"The answer is {n:,} characters; the limit is {p['max_chars']:,}.")
    return _f(ctx, "Met", f"The question is answered ({n:,} of {p['max_chars']:,} characters). Read it alongside the merit criteria.")


STUDY_NT_CHECKS = {
    "document_field_in_list": document_field_in_list,
    "document_date_in_window": document_date_in_window,
    "document_present_right_type": document_present_right_type,
    "document_count": document_count,
    "document_field_matches": document_field_matches,
    "student_visa": student_visa,
    "arrival_evidence": arrival_evidence,
    "documents_english": documents_english,
    "referee_fields": referee_fields,
    "referee_dates": referee_dates,
    "days_before": days_before,
    "country_not_in": country_not_in,
    "address_not_in_nt": address_not_in_nt,
    "fact_equals": fact_equals,
    "declaration_complete": declaration_complete,
    "register_none": register_none,
    "word_count_and_photo": word_count_and_photo,
    "text_length": text_length,
}
