"""OfflineStubProvider - a deterministic keyword matcher. NOT an LLM.

Used for tests, offline demos and the sample evaluation report when no
GEMINI_API_KEY is available. It reads `prompt.meta` (never sent to a real
provider) and answers with the same schemas a real model would, quoting
sentences verbatim from the redacted text. Its accuracy says nothing about
a real model's accuracy; reports produced with it are labelled as such.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from app.llm.base import LLMInvalidOutput, Prompt

# Keyword hints for the seeded rule codes only.
_HINTS: dict[str, dict[str, list[str]]] = {
    "SNT-R6": {
        "negative": ["live in Sydney", "living in Sydney", "live in Melbourne", "studying online from", "outside the NT"],
        "positive": ["Darwin", "Alice Springs", "Katherine", "Palmerston", "Northern Territory", "in the NT"],
    },
    "CBF-C1": {
        "negative": ["Pty Ltd", "privately owned company", "profits to shareholders", "shareholders"],
        "positive": ["not-for-profit", "not for profit", "non-profit", "nonprofit", "no profit", "charity"],
    },
    "CBF-C2": {
        "negative": [],
        "positive": ["Darwin", "Alice Springs", "Katherine", "Palmerston", "Tennant Creek", "Northern Territory", "in the NT"],
    },
    "SNT-R7": {"evidence": ["community", "volunteer", "contribute", "help other"]},
    # Study NT v2
    "SNT-S8": {
        "field": ["current_study"],
        "negative": ["Charles Darwin University", "studying with an NT provider", "enrolled at CDU", "study at CDU"],
        # a future offer is not current study
        "not_if": ["accepted an offer", "offer to start", "offer to study", "will start", "will begin", "plan to", "intend to"],
        "positive": ["secondary school", "high school", "Year 12", "not studying", "working as", "final semester", "final year", "currently studying", "I study at"],
    },
    "SNT-S4": {"evidence": ["IELTS", "English entry", "entry requirement", "academic requirement"]},
    "SNT-M1": {"evidence": ["average", "prize", "examination", "examinations", "exam", "grades", "results", "distinction", "award", "certificate"]},
    "SNT-M3": {"evidence": ["lead", "leader", "led", "coordinator", "organised", "organise"]},
    "SNT-M4": {"evidence": ["volunteer", "community", "clinic"]},
    "SNT-M2": {"evidence": ["volunteer", "community", "clinic", "recommend", "support", "patients", "helps", "helped", "mentor"]},
    "SNT-M5": {"evidence": ["nurse", "Northern Territory", "remote", "community", "clinic", "families", "help"]},
    "CBF-C6": {"evidence": ["community", "residents", "benefit", "local people", "elders"]},
}
_AMBIGUOUS = ["might", "maybe", "not sure", "planning to", "hope to", "or possibly"]
_ACT = re.compile(
    r"(?:incorporated|registered|incorporation)\s+(?:is\s+)?(?:under|with|by)\s+(?:the\s+)?((?:[A-Z][\w-]*\s+){1,4}Act(?:\s+\d{4})?(?:\s*\([A-Za-z]+\))?)"
)


def _sentences(source: str, fields: list[str] | None = None) -> list[str]:
    out = []
    for line in source.splitlines():
        key, _, value = line.partition(": ")
        if fields and key not in fields:
            continue
        for s in re.split(r"(?<=[.!?])\s+", value):
            if s.strip():
                out.append(s.strip())
    return out


def _free_sentences(source: str) -> list[str]:
    """Every sentence in the text, with a form field's "name: " prefix dropped (each is a verbatim part of the text)."""
    out = []
    for line in source.splitlines():
        value = re.sub(r"^[a-z][a-z0-9_]*: ", "", line.strip())
        out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", value) if s.strip())
    return out


def _find(sentences: list[str], keywords: list[str]) -> str | None:
    for s in sentences:
        for kw in keywords:
            if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", s, re.IGNORECASE):
                return s
    return None


class OfflineStubProvider:
    name = "offline-stub"
    model = "keyword-stub-v1"

    def generate(self, prompt: Prompt, schema: type[BaseModel], *, temperature: float, timeout: float) -> str:
        meta = prompt.meta
        source: str = meta.get("source", "")
        sentences = _sentences(source)
        kind = meta.get("kind")
        if kind == "facts":
            return json.dumps({"facts": [self._fact(k, sentences) for k in meta.get("keys", [])]})
        if kind == "referee":
            return json.dumps(self._referee(source))
        if kind == "merit_claims":
            return json.dumps(_stub_merit_claims(meta))
        if kind == "merit_summary":
            return json.dumps(_stub_merit_summary(meta))
        if kind == "timeline":
            return json.dumps({"events": _stub_timeline(source)})
        if kind == "narrative":
            return json.dumps({"contradictions": _stub_narrative(source)})
        code = self._qualified(meta.get("rule_code", ""), prompt)
        hints = _HINTS.get(code, {})
        if hints.get("field"):
            sentences = _sentences(source, hints["field"])
        if kind == "evidence":
            quotes = [s for s in sentences if any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", s, re.I) for k in hints.get("evidence", []))]
            # The offline stub cannot paraphrase: each bullet only names the keyword that made it point at a passage.
            keys = hints.get("evidence", [])
            matched = [(s, next(k for k in keys if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", s, re.I)))
                       for s in _free_sentences(source) if any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", s, re.I) for k in keys)]
            summaries = [{"summary": f"Mentions {kw.lower()} in this passage.", "passages": [s]} for s, kw in matched[:6]]
            return json.dumps({"supporting_quotes": quotes[:3], "summaries": summaries})
        if kind == "rule":
            return json.dumps(self._rule(hints, sentences))
        raise LLMInvalidOutput("stub: unknown prompt kind")

    @staticmethod
    def _qualified(rule_code: str, prompt: Prompt) -> str:
        prefix = "CBF" if rule_code.startswith("C") else "SNT"
        return f"{prefix}-{rule_code}"

    _REFEREE_LABELS = {
        "referee name": "referee_name", "position": "position", "organisation": "organisation",
        "relationship": "relationship", "known applicant for": "length_of_association",
        "length of association": "length_of_association", "date": "letter_date", "signature": "signature",
    }

    @classmethod
    def _referee(cls, source: str) -> dict[str, Any]:
        found: dict[str, dict[str, Any]] = {}
        for line in source.splitlines():
            label, sep, value = line.partition(":")
            key = cls._REFEREE_LABELS.get(label.strip().lower())
            if sep and key and value.strip() and key not in found:
                found[key] = {"key": key, "value": value.strip(), "quote": line.strip()}
            if "letterhead" in line.lower() and "letterhead" not in found:
                found["letterhead"] = {"key": "letterhead", "value": "yes", "quote": line.strip()}
        keys = ["referee_name", "position", "organisation", "relationship", "length_of_association", "letter_date",
                "signature", "letterhead"]
        fields = [found.get(k, {"key": k, "value": "not stated", "quote": None}) for k in keys]
        highlights = [s for s in re.split(r"(?<=[.!?])\s+", source.replace("\n", " "))
                      if re.search(r"dedicated|hard-working|excellent|led |organised", s)][:3]
        return {"fields": fields, "highlights": [h.strip() for h in highlights]}

    @staticmethod
    def _fact(key: str, sentences: list[str]) -> dict[str, Any]:
        if key == "incorporation_act":
            for s in sentences:
                m = _ACT.search(s)
                if m:
                    return {"key": key, "value": m.group(1).strip(), "quote": s}
        return {"key": key, "value": "not stated", "quote": None}

    @staticmethod
    def _rule(hints: dict[str, list[str]], sentences: list[str]) -> dict[str, Any]:
        past = hints.get("not_if", [])
        neg = _find([x for x in sentences if not _find([x], past)], hints.get("negative", []))
        if neg:
            return {"status": "Not met", "rationale": "The applicant's words contradict the rule.", "evidence_quote": neg,
                    "confidence": "medium", "language_flag": False, "needs_applicant_clarification": False}
        pos = _find(sentences, hints.get("positive", []))
        if pos:
            ambiguous = any(a in pos.lower() for a in _AMBIGUOUS)
            return {
                "status": "Unclear" if ambiguous else "Met",
                "rationale": "The wording could be read more than one way." if ambiguous else "The applicant's words address the rule.",
                "evidence_quote": pos,
                "confidence": "low" if ambiguous else "medium",
                "language_flag": False,
                "needs_applicant_clarification": ambiguous,
            }
        return {"status": "Needs evidence", "rationale": "The application does not address this rule.", "evidence_quote": None,
                "confidence": "medium", "language_flag": False, "needs_applicant_clarification": True}


# ---------------------------------------------------------------------------
# Merit summaries (extract claims, then summarise from verified claims). Pattern matching, NOT an LLM: it copies clauses that
# mention a theme keyword or a number, and groups claims that share three words in a row or two numbers. Written next to the
# synthetic test cases; it says nothing about how a real model performs.
# ---------------------------------------------------------------------------

_NAMED = re.compile(r"(?:[A-Z][\w'’&-]+\s+){0,4}(?:Award|Society|Showcase|Conference|Prize|Scholarship|Pahadi|Club|Project|University|Institute)\b")


def _stub_merit_claims(meta: dict[str, Any]) -> dict[str, Any]:
    kw = meta.get("theme_keywords", {})
    claims = []
    for p in meta.get("passages", []):
        named = _NAMED.search(p["text"])
        for sentence in re.split(r"(?<=[.!?])\s+", p["text"]):
            for clause in re.split(r";\s+", sentence):
                clause = clause.strip()
                if len(clause.split()) < 3:
                    continue
                theme = next((t for t, words in kw.items() if any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", clause, re.I) for w in words)), None)
                if theme is None and not re.search(r"\d", clause):
                    continue
                own = _NAMED.search(clause)
                subject = (own or named).group().strip() if (own or named) else ""
                # The stub only knows one attribute it can name safely: the year(s) of a named thing.
                attribute = "year" if subject and re.search(r"\b(19|20)\d\d\b", clause) else ""
                claims.append({"restatement": " ".join(clause.split()[:25]), "theme": theme or "other", "source_quote": " ".join(clause.split()),
                               "passage_id": p["id"], "subject": subject, "attribute": attribute})
    return {"claims": claims}


def _runs(a: str, b: str, n: int = 3) -> bool:
    ta, tb = re.findall(r"\w+", a.lower()), re.findall(r"\w+", b.lower())
    grams = {tuple(ta[i:i + n]) for i in range(len(ta) - n + 1)}
    return any(tuple(tb[i:i + n]) in grams for i in range(len(tb) - n + 1))


def _plain_name(label: str) -> str:
    name = re.sub(r"\s*·.*$", "", label or "the document").strip()
    return name if name in ("CoE",) else name[:1].lower() + name[1:]


_NUM_PHRASE = re.compile(r"(?:\b[\w'’.-]+\s+){0,2}\d[\d,./%+]*(?:\s+(?:of|out of|to|in)?\s*[\w'’.%+-]+){0,2}")


def _paraphrase(quote: str, doc: str, theme_label: str) -> str:
    """The stub cannot really paraphrase. It names the document and lists the numbers and names it found, in a template
    sentence, so a line is never a cut-off copy of the quote."""
    q = re.sub(r"^[a-z_]+:\s*", "", quote)
    bits: list[str] = []
    for m in _NAMED.finditer(q):
        bits.append(m.group().strip())
    for m in _NUM_PHRASE.finditer(q):
        bit = m.group().strip(" ,.;")
        if bit and not any(bit in b or b in bit for b in bits):
            bits.append(bit)
    lead = re.compile(r"^(?:(?:I|my|me|she|her|he|his|they|their|and|where|also|the|a|an|with|in|of|for|to)\s+)+", re.I)
    bits = [lead.sub("", b).strip(" ,.") for b in bits]
    bits = [b for b in bits if b and len(b) > 2][:3]
    if not bits:
        return f"The {doc} talks about {theme_label.lower()}."
    return f"The {doc} mentions " + (", ".join(bits[:-1]) + " and " + bits[-1] if len(bits) > 1 else bits[0]) + "."


def _line(c: dict[str, Any], names: dict[str, str], labels: dict[str, str]) -> str:
    doc = _plain_name(names.get(c["source"], "document"))
    third = _third_person(c["quote"], doc)
    if third and not _close(third, c["quote"]) and len(third.split()) >= 4:
        return third
    return _paraphrase(c["quote"], doc, labels.get(c.get("attribute_theme", c["theme"]), "this criterion"))


def _third_person(quote: str, doc: str) -> str | None:
    """A plain third-person version of a first-person sentence, shortened at a clause break. None if nothing changed."""
    who = "The referee" if doc.lower().startswith("referee") else "The applicant"
    q = re.sub(r"^[a-z_]+:\s*", "", " ".join(quote.split()))
    q = re.sub(r"\[PERSON_\d+\]", "the applicant", q)
    before = q
    for pat, rep in ((r"\bI have\b", f"{who} has"), (r"\bI've\b", f"{who} has"), (r"\bI am\b", f"{who} is"), (r"\bI'm\b", f"{who} is"),
                     (r"\bI was\b", f"{who} was"), (r"\bI\b", who), (r"\bmy\b", "their"), (r"\bMy\b", "Their"), (r"\bme\b", "them"),
                     (r"^(She|He)\b", "The applicant")):
        q = re.sub(pat, rep, q)
    if q == before:
        # Not in the first person: a referee writing about the applicant ("She built ...", "[PERSON_1] joined us ...").
        m = re.match(r"^(?:She|He|The applicant|the applicant)\s+(.*)$", q)
        if not (m and doc.lower().startswith("referee")):
            return None
        rest = m.group(1).split()
        rest = " ".join(rest[:14])
        rest = rest[:rest.rfind(",")] if "," in rest and len(m.group(1).split()) > 14 else rest
        q = f"The referee says the applicant {rest.strip(' ,;:.')}"
    first = q.find(who)
    if first >= 0:   # name the person once, then "they"
        q = q[:first + len(who)] + q[first + len(who):].replace(f" {who} has", " they have").replace(f" {who} is", " they are").replace(f" {who}", " they")
    words = q.split()
    if len(words) > 22:
        cut = " ".join(words[:22])
        q = cut[:cut.rfind(",")] if "," in cut else cut
    q = q.strip(" ,;:-")
    q = q[:1].upper() + q[1:]
    return q if q.endswith((".", "!", "?")) else q + "."


def _close(a: str, b: str) -> bool:
    from rapidfuzz import fuzz

    na, nb = " ".join(a.lower().split()), " ".join(b.lower().split())
    return fuzz.ratio(na, nb) > 84 or (len(na) > 12 and na.strip(".") in nb)


def _stub_merit_summary(meta: dict[str, Any]) -> dict[str, Any]:
    labels = meta.get("theme_labels", {})
    names = meta.get("names", {})
    groups: list[dict[str, Any]] = []
    wanted = [set(d.get("claim_ids", [])) for d in meta.get("rewrite", [])]
    for c in meta.get("claims", []):
        nums = set(re.findall(r"\d[\d,]*(?:\.\d+)?", c["quote"]))
        for g in groups:
            if g["theme"] == c["theme"] and c["source"] != g["first_source"] and (_runs(c["quote"], g["quote"]) or len(nums & g["nums"]) >= 2):
                g["claim_ids"].append(c["claim_id"])
                break
        else:
            groups.append({"theme": c["theme"], "claim_ids": [c["claim_id"]], "quote": c["quote"], "nums": nums, "first_source": c["source"],
                           "text": _line(c, names, labels)})
    items: list[dict[str, Any]] = []
    for g in groups:   # the same template line twice in one theme becomes one line that cites both
        same = next((i for i in items if i["theme"] == g["theme"] and i["text"] == g["text"]), None)
        if same:
            same["claim_ids"] += g["claim_ids"]
        else:
            items.append({"theme": g["theme"], "text": g["text"], "claim_ids": list(g["claim_ids"])})
    if wanted:   # a rewrite: only the groups asked for, in the same template sentence
        items = [i for i in items if set(i["claim_ids"]) in wanted]
    docs = list(dict.fromkeys(re.sub(r"\d", "", _plain_name(names.get(c["source"], "document"))).strip() for c in meta.get("claims", [])))
    themes = list(dict.fromkeys(labels.get(c.get("attribute_theme", c["theme"]), "") for c in meta.get("claims", []) if labels.get(c.get("attribute_theme", c["theme"]))))
    overview = []
    if docs:
        overview.append(f"This was found in the {', the '.join(docs[:-1]) + ' and the ' + docs[-1] if len(docs) > 1 else docs[0]}.")
    if themes:
        overview.append(f"It covers {', '.join(t.lower() for t in themes[:-1]) + ' and ' + themes[-1].lower() if len(themes) > 1 else themes[0].lower()}.")
    return {"overview": overview, "items": items}


# ---------------------------------------------------------------------------
# Consistency-layer tasks. Pattern matching over the combined redacted text; NOT an LLM, and written alongside
# the synthetic test cases, so it says nothing about how a real model performs.
# ---------------------------------------------------------------------------

_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_WHEN_S = rf"(?:{_MONTH}\.?\s+\d{{4}}|\d{{4}})"
_RANGE = re.compile(rf"(?<![\d/])(?P<s>{_WHEN_S})\s*(?:-|\u2013|\u2014|to|until)\s*(?P<e>{_WHEN_S}|present|current|now)(?![\d/])", re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ROLE_WORDS = re.compile(r"president|manager|engineer|analyst|developer|intern|volunteer|coordinator|captain|lead|director|founder|"
                         r"assistant|officer|teacher|tutor|consultant|designer|member|chair|secretary|treasurer|representative", re.I)
_STUDY_WORDS = re.compile(r"bachelor|master|diploma|degree|studies|studied|student at|studying|secondary school|high school|college|university|certificate", re.I)
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _norm_when(text: str) -> str:
    m = re.match(rf"({_MONTH})\.?\s+(\d{{4}})", text.strip(), re.I)
    return f"{m.group(2)}-{_MONTHS[m.group(1)[:3].lower()]:02d}" if m else text.strip()


def _stub_timeline(source: str) -> list[dict[str, Any]]:
    events = []
    for line in source.splitlines():
        if line.startswith("=== ") or not line.strip():
            continue
        m = _RANGE.search(line)
        if not m:
            continue
        before = re.sub(r"[\s,;:(\-\u2013]+$", "", line[: m.start()]).strip(" -*\u2022\t") or line[m.end():].strip(" ,;:()-")
        kind = "role" if _ROLE_WORDS.search(line) or not _STUDY_WORDS.search(line) else "study"
        if _STUDY_WORDS.search(line) and not _ROLE_WORDS.search(line):
            kind = "study"
        low = line.lower()
        events.append({"label": before[:80] or "Event", "kind": kind, "start": _norm_when(m.group("s")),
                       "end": "present" if m.group("e").lower() in ("present", "current", "now") else _norm_when(m.group("e")),
                       "full_time": True if re.search(r"full[- ]time", low) else False if re.search(r"part[- ]time", low) else None,
                       "quote": line.strip()})
    return events[:40]


def _num(token: str) -> float | None:
    t = token.lower()
    return float(_NUMBER_WORDS[t]) if t in _NUMBER_WORDS else (float(t) if re.fullmatch(r"\d+(?:\.\d+)?", t) else None)


def _stub_narrative(source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    parts = re.split(r"(?m)^=== .* ===\n", source)
    claims, records = [], []
    for part in parts:
        for m in re.finditer(r"[^.\n]*\b(?:studied|study|studying|completed)\b[^.\n]*?\bfor\s+(\w+)\s+years?\b[^.\n]*", part, re.I):
            n = _num(m.group(1))
            if n is not None:
                topic = re.search(r"(?:studied|study|studying|completed)\s+([\w ]{3,40}?)\s+for\b", m.group(0), re.I)
                claims.append((n, m.group(0).strip(), topic.group(1).strip() if topic else "years of study"))
        for m in re.finditer(r"[^\n]*\b(?:years? (?:completed|of study|attended)|duration of (?:study|attendance)|completed\s+\w+\s+years?)\b[^\n]*", part, re.I):
            nums = [n for t in re.findall(r"\b(\d+(?:\.\d+)?|one|two|three|four|five|six)\b", m.group(0), re.I) if (n := _num(t)) is not None and n < 15]
            if nums:
                records.append((nums[0], m.group(0).strip()))
    for n, quote, topic in claims:
        for k, rec in records:
            if abs(n - k) >= 1 and quote != rec:
                out.append({"topic": "years of study" if not topic else topic[:40], "first_quote": quote, "second_quote": rec})
                break
    # A referee's "known for N years" against the same letter's "since <Month YYYY>".
    for part in parts:
        known = re.search(r"[^.\n]*\bknown\b[^.\n]*?\bfor\s+(\w+)\s+years?\b[^.\n]*", part, re.I)
        since = re.search(rf"[^.\n]*\b(?:since|joined|started)\b[^.\n]*?({_MONTH}\.?\s+\d{{4}})[^.\n]*", part, re.I)
        dated = re.search(rf"(?im)^\s*date\s*:\s*(?:\d{{1,2}}\s+)?({_MONTH}\.?\s+\d{{4}})", part)
        if known and since and dated and known.group(0) != since.group(0):
            n = _num(known.group(1))
            a, b = _norm_when(since.group(1)).split("-"), _norm_when(dated.group(1)).split("-")
            if n is not None and len(a) == 2 and len(b) == 2:
                months = (int(b[0]) - int(a[0])) * 12 + int(b[1]) - int(a[1])
                if abs(n * 12 - months) > 12:
                    out.append({"topic": "how long the referee has known the applicant", "first_quote": known.group(0).strip(),
                                "second_quote": since.group(0).strip()})
    return out[:6]
