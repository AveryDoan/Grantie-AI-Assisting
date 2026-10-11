"""Check 1b, claims against the evidence provided (plain code).

- An award the application claims for several years, where the certificate covers fewer years, needs evidence for the others.
  That is "Needs evidence", never "Not met": missing proof is not proof of the opposite.
- A referee who says they have known the applicant since a date earlier than the start of the degree the resume gives is a
  difference to check, not a conclusion: referees often know a student from before, or the dates are loose.

Both quote the exact lines they compare (from the redacted text). Neither decides what it means.
"""

from __future__ import annotations

import re

from app.pipeline.consistency.common import MONTH_RE, parse_when, sentence_matching
from app.pipeline.consistency.context import ConsistencyContext, DocView
from app.pipeline.consistency.models import Evidence, Flag, flag

TYPE = "cross_document"
_AWARD = re.compile(r"((?:[A-Z][\w'’]*\s+){0,3}(?:Award|Prize|Scholarship|Medal))\b")
_YEAR = re.compile(r"(?<![\d/.-])((?:19|20)\d{2})(?![\d/.-])")
_ACADEMIC_YEAR = re.compile(r"(?:for|in|during)\s+the\s+((?:19|20)\d{2})\s+(?:academic\s+)?year|\b((?:19|20)\d{2})\s+academic\s+year", re.I)
_EVIDENCE_WORDS = ("certificate", "transcript", "presented", "awarded")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s*\n\s*", " ", text)) if s.strip()]


def _claims(ctx: ConsistencyContext) -> dict[str, dict]:
    """award name -> {years, sentence, source}. From the application's answers first, then the resume-like documents."""
    out: dict[str, dict] = {}
    sources = [("form", ctx.form_text, None)] + [(d.source, d.text, d) for d in ctx.docs if d.text and d.raw_declared in ("resume", "application responses")]
    for src, text, doc in sources:
        for s in _sentences(text):
            m = _AWARD.search(s)
            years = {int(y) for y in _YEAR.findall(s)}
            if not m or not years:
                continue
            name = m.group(1).strip()
            entry = out.setdefault(name.lower(), {"name": name, "years": set(), "sentence": s, "source": src, "doc": doc})
            entry["years"] |= years
    return out


def _evidence_docs(ctx: ConsistencyContext, name: str) -> list[DocView]:
    out = []
    for d in ctx.docs:
        head = d.text[:400].lower()
        if name.lower() in d.text.lower() and (d.raw_declared in ("certificate", "transcript") or any(w in head or w in d.text.lower() for w in _EVIDENCE_WORDS)) \
                and d.raw_declared not in ("resume", "application responses"):
            out.append(d)
    return out


def claim_needs_evidence(ctx: ConsistencyContext) -> list[Flag]:
    out: list[Flag] = []
    for key, claim in _claims(ctx).items():
        years = claim["years"]
        if len(years) < 2:
            continue   # one year claimed and one certificate is the ordinary case; only a run of years needs a run of proof
        docs = _evidence_docs(ctx, claim["name"])
        covered = {int(y) for d in docs for m in _ACADEMIC_YEAR.finditer(d.text) for y in m.groups() if y}
        missing = sorted(years - covered)
        if not missing:
            continue
        said = ", ".join(map(str, sorted(years)))
        gap = ", ".join(map(str, missing))
        ev = [Evidence(kind="quote", source="form" if claim["source"] == "form" else claim["source"], label="What the application says",
                       quote=claim["sentence"], verified=claim["sentence"] in (ctx.form_text if claim["source"] == "form" else claim["doc"].text))]
        for d in docs:
            line = sentence_matching(d.text, _ACADEMIC_YEAR.pattern)
            if line:
                ev.append(Evidence(kind="quote", source=d.source, label=f"What the certificate says ({d.label})", quote=line, verified=line in d.text))
        covered_text = ", ".join(map(str, sorted(covered))) if covered else "no year"
        out.append(flag("cross_document.claim_needs_evidence", TYPE, "strong",
                        f"The application says the {claim['name']} was received in {said}. "
                        f"The evidence provided covers {covered_text} only. Evidence for {gap} is needed.",
                        ev, key_parts=(key, gap)))
    return out


_SINCE = re.compile(rf"since\s+({MONTH_RE}\s+\d{{4}})", re.I)
_DEGREE = re.compile(rf"((?:Bachelor|Master|Diploma|Doctor)[^\n]{{0,140}}?)\b({MONTH_RE}\.?\s+\d{{4}})\s+to\s+", re.I)


def known_since_vs_degree_start(ctx: ConsistencyContext) -> list[Flag]:
    """A referee's "known since" date that is earlier than the start of the degree on the resume."""
    resumes = [d for d in ctx.docs if d.text and d.raw_declared == "resume"]
    if not resumes:
        return []
    degree = None
    for d in resumes:
        flat = re.sub(r"\s*\n\s*", " ", d.text)
        m = _DEGREE.search(flat)
        if m:
            degree = (d, m.group(2), sentence_matching(d.text, re.escape(m.group(2)) + r"\s+to"))
            break
    if not degree:
        return []
    start = parse_when(degree[1])
    out: list[Flag] = []
    for doc in ctx.letters():
        m = _SINCE.search(re.sub(r"\s*\n\s*", " ", doc.text))
        since = parse_when(m.group(1)) if m else None
        if not (since and start and (start - since).days > 60):
            continue
        line = sentence_matching(doc.text, r"since\s+" + MONTH_RE)
        ev = [e for e in (
            Evidence(kind="quote", source=doc.source, label=f"When the referee says they met the applicant ({doc.label})", quote=line, verified=bool(line) and line in doc.text) if line else None,
            Evidence(kind="quote", source=degree[0].source, label=f"When the degree started ({degree[0].label})", quote=degree[2], verified=bool(degree[2]) and degree[2] in degree[0].text) if degree[2] else None,
        ) if e]
        out.append(flag("cross_document.known_since_vs_degree_start", TYPE, "weak",
                        f"A referee says they have known the applicant since {since:%B %Y}, but the resume gives the start of the degree "
                        f"as {start:%B %Y}. Referees can know a student before a course; this is a difference to check.",
                        ev, key_parts=(doc.id,)))
    return out
