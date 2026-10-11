"""Check 4, cross_application (plain code, whole pool).

Do unrelated applicants share a contact, a referee, or the same document wording?

Privacy by construction:
- identifiers (emails, phones, addresses, referee names) are normalised and stored ONLY as HMAC-SHA256 hashes
  keyed by IDENTIFIER_HASH_KEY. The identifiers themselves are never stored, logged or sent to an LLM;
- document similarity compares keyed hashes of word shingles of the REDACTED text (names and places are already
  placeholders, so a letter reused with new names still matches);
- a flag names the shared attribute ("a referee's phone number") and the linked applications' references,
  never a value.

Sharing is only ever a reason to look: schools and families legitimately share addresses, domains and templates.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

from app.pipeline.consistency.common import reference
from app.pipeline.consistency.context import ConsistencyContext
from app.pipeline.consistency.models import Evidence, Flag, flag

TYPE = "cross_application"
SKETCH_K = 64
SHINGLE_WORDS = 5
MIN_SHINGLES = 30
IDENTICAL = 0.80
REUSED = 0.40
REUSED_STRONG = 0.50
FINGERPRINT_TYPES = ("referee_letter", "other")

GENERIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "icloud.com", "me.com",
    "proton.me", "protonmail.com", "qq.com", "163.com", "126.com", "example.com", "example.org", "example.net",
})
KIND_LABEL = {
    "contact_email": "contact email address", "contact_phone": "contact phone number", "contact_address": "address",
    "referee_name": "referee", "referee_email": "referee's email address", "referee_phone": "referee's phone number",
    "referee_email_domain": "referee's email domain",
}
KIND_STRENGTH = {k: "strong" for k in KIND_LABEL} | {"referee_email_domain": "weak"}

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_PHONE = re.compile(r"(?<![\w/.-])(?:\+\d[\d ()-]{7,}\d|0\d[\d ()-]{7,}\d)")


def keyed_hash(key: str, kind: str, value: str) -> str:
    return hmac.new(key.encode("utf-8"), f"{kind}\x00{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def norm_email(s: str) -> str:
    return s.strip().lower()


def norm_phone(s: str) -> str | None:
    digits = re.sub(r"\D", "", s)
    return digits[-9:] if len(digits) >= 8 else None   # national number: +61 491... and 0491... match


def norm_address(s: str) -> str | None:
    t = " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())
    return t if len(t) >= 10 else None


def norm_name(s: str) -> str | None:
    toks = sorted(t for t in re.sub(r"[^a-z ]", " ", s.lower()).split() if t not in ("dr", "mr", "mrs", "ms", "miss", "prof", "professor", "mx"))
    return " ".join(toks) if len(toks) >= 2 else None


@dataclass(frozen=True)
class Ident:
    kind: str
    hash: str
    source: str


def collect_identifiers(ctx: ConsistencyContext) -> list[Ident]:
    """Keyed hashes of contact and referee identifiers. Raw values never leave this function."""
    if not ctx.hash_key:
        return []
    key, out, seen = ctx.hash_key, [], set()

    def add(kind: str, value: str | None, source: str) -> None:
        if value and (kind, value) not in seen:
            seen.add((kind, value))
            out.append(Ident(kind, keyed_hash(key, kind, value), source))

    own_emails = {norm_email(ctx.fields[k]) for k in ("email", "contact_email") if ctx.fields.get(k)}
    own_phones = {n for k in ("phone", "contact_phone") if ctx.fields.get(k) and (n := norm_phone(ctx.fields[k]))}
    for k in ("email", "contact_email"):
        if ctx.fields.get(k):
            add("contact_email", norm_email(ctx.fields[k]), "form")
    for k in ("phone", "contact_phone"):
        if ctx.fields.get(k):
            add("contact_phone", norm_phone(ctx.fields[k]), "form")
    for k in ("residential_address", "postal_address"):
        if ctx.fields.get(k):
            add("contact_address", norm_address(ctx.fields[k]), "form")
    for doc in ctx.docs:
        if doc.type != "referee_letter":
            continue
        raw = doc.original or ""
        for m in _EMAIL.finditer(raw):
            email = norm_email(m.group(0))
            if email in own_emails:
                continue
            add("referee_email", email, doc.source)
            domain = m.group(1).lower()
            if domain not in GENERIC_DOMAINS:
                add("referee_email_domain", domain, doc.source)
        for m in _PHONE.finditer(raw):
            n = norm_phone(m.group(0))
            if n and n not in own_phones:
                add("referee_phone", n, doc.source)
        ref = ctx.referee_for(doc.id)
        if ref is not None and ref.stated("referee_name") and ctx.token_map is not None:
            from redaction.restore import display_original

            for tok in re.findall(r"\[[A-Z]+_\d+\]", ref.fields["referee_name"].fact_value):
                add("referee_name", norm_name(display_original(ctx.token_map, tok) or ""), doc.source)
    return out


# ---------------------------------------------------------------------------- document wording


_BOILERPLATE = re.compile(r"(?im)^\s*(?:referee name|position|organisation|relationship|known applicant for|date|email|phone|signature)\s*:.*$|"
                          r"^.*(?:SAMPLE DOCUMENT|LETTERHEAD).*$|^\s*(?:yours sincerely|to whom it may concern|to the assessment panel),?\s*$")


def prose(text: str) -> str:
    """The wording of a document without its form-like labels, so shared layouts do not look like shared text."""
    return _BOILERPLATE.sub("", text)


def shingle_hashes(redacted: str, key: str) -> list[str]:
    """Keyed hashes of the 5-word shingles of REDACTED text. Placeholders count by type, not number,
    so a letter reused with different names or places still matches."""
    words = []
    for w in re.findall(r"\[[A-Z]+_\d+\]|\[LOCATION[^\]]*\]|[A-Za-z0-9']+", redacted):
        words.append(f"<{re.match(r'.([A-Z]+)', w).group(1).lower()}>" if w.startswith("[") else w.lower())
    if len(words) < SHINGLE_WORDS:
        return []
    shingles = {" ".join(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}
    return [hmac.new(key.encode("utf-8"), s.encode("utf-8"), hashlib.sha256).hexdigest()[:16] for s in shingles]


def sketch(hashes: list[str], k: int = SKETCH_K) -> list[str]:
    return sorted(set(hashes))[:k]


def jaccard(a: list[str], b: list[str], k: int = SKETCH_K) -> float:
    """Bottom-k estimate of the Jaccard similarity of two shingle sets."""
    if not a or not b:
        return 0.0
    union = sorted(set(a) | set(b))[:k]
    sa, sb = set(a), set(b)
    return sum(1 for h in union if h in sa and h in sb) / len(union)


@dataclass(frozen=True)
class Finger:
    scope: str
    doc_type: str
    label: str
    count: int
    sketch: list[str]
    document_id: str | None = None


def collect_fingerprints(ctx: ConsistencyContext) -> list[Finger]:
    if not ctx.hash_key:
        return []
    out = []
    for d in ctx.docs:
        if d.type in FINGERPRINT_TYPES and d.text:
            hs = shingle_hashes(prose(d.text), ctx.hash_key)
            if len(hs) >= MIN_SHINGLES:
                out.append(Finger(d.source, d.type, d.label, len(hs), sketch(hs), d.id))
    answers = "\n".join(line for line in ctx.form_text.splitlines() if line.split(":", 1)[0] in ctx.answers)
    hs = shingle_hashes(answers, ctx.hash_key) if answers else []
    if len(hs) >= MIN_SHINGLES:
        out.append(Finger("answers", "answers", "Written answers", len(hs), sketch(hs)))
    return out


# ---------------------------------------------------------------------------- storage


def save(store: Any, app_id: str, idents: list[Ident], fingers: list[Finger]) -> None:
    store.delete("identifier_hashes", eq={"application_id": app_id})
    store.delete("document_fingerprints", eq={"application_id": app_id})
    if idents:
        store.insert("identifier_hashes", [{"application_id": app_id, "kind": i.kind, "hash": i.hash, "source": i.source} for i in idents])
    if fingers:
        store.insert("document_fingerprints", [
            {"application_id": app_id, "scope": f.scope, "document_id": f.document_id, "doc_type": f.doc_type, "label": f.label,
             "shingle_count": f.count, "sketch": f.sketch} for f in fingers])


def _peer_apps(store: Any, app_id: str) -> dict[str, dict[str, Any]]:
    """Other applications of unrelated applicants in the same organisation (the only ones an officer can see)."""
    me = store.select("applications", eq={"id": app_id}, limit=1)[0]
    prog = {p["id"]: p for p in store.select("grant_programs")}
    org = prog.get(me["grant_program_id"], {}).get("organisation_id")
    return {a["id"]: a for a in store.select("applications")
            if a["id"] != app_id and a["status"] != "draft" and a["applicant_id"] != me["applicant_id"]
            and prog.get(a["grant_program_id"], {}).get("organisation_id") == org}


def flags_for(store: Any, app_id: str) -> list[Flag]:
    """Cross-application flags for one application, from the stored hashes and fingerprints of the whole pool."""
    peers = _peer_apps(store, app_id)
    if not peers:
        return []
    mine = store.select("identifier_hashes", eq={"application_id": app_id})
    out: list[Flag] = []
    by_hash: dict[tuple[str, str], list[str]] = {}
    if mine:
        theirs = store.select("identifier_hashes", in_={"hash": sorted({r["hash"] for r in mine})})
        for r in theirs:
            if r["application_id"] in peers:
                by_hash.setdefault((r["kind"], r["hash"]), []).append(r["application_id"])
    emitted: set[str] = set()
    for (kind, h), others in sorted(by_hash.items()):
        others = sorted(set(others))
        key = f"{kind}:{h}"
        if key in emitted:
            continue
        emitted.add(key)
        n = len(others)
        what = KIND_LABEL[kind]
        strength = KIND_STRENGTH[kind]
        out.append(flag(
            f"cross_application.shared_{kind}", TYPE, strength,
            f"This application shares a {what} with {n} other application{'s' if n != 1 else ''} from "
            f"{'a different applicant' if n == 1 else 'different applicants'}." + (
                " Schools and organisations often share an email domain." if kind == "referee_email_domain" else ""),
            [Evidence(kind="link", source=f"application:{o}", label=f"{reference(o)} shares the same {what}") for o in others],
            key_parts=(kind, h)))
    # Wording: compare sketches with other applications' fingerprints of the same kind.
    my_prints = store.select("document_fingerprints", eq={"application_id": app_id})
    if my_prints:
        others_prints = store.select("document_fingerprints", in_={"application_id": sorted(peers)})
        pairs: list[tuple[float, dict, dict]] = []
        for a in my_prints:
            for b in others_prints:
                if a["doc_type"] == b["doc_type"]:
                    j = jaccard(a["sketch"], b["sketch"])
                    if j >= REUSED:
                        pairs.append((j, a, b))
        for j, a, b in sorted(pairs, key=lambda t: -t[0]):
            identical = j >= IDENTICAL
            strength = "strong" if j >= REUSED_STRONG else "weak"
            what = "answers" if a["doc_type"] == "answers" else a["label"].lower()
            out.append(flag(
                "cross_application.identical_text" if identical else "cross_application.reused_wording", TYPE, strength,
                (f"The {what} is almost word for word the same as a document in {reference(b['application_id'])}."
                 if identical else
                 f"The {what} uses largely the same wording as a document in {reference(b['application_id'])}, once names and places are ignored. "
                 "Official templates look the same."),
                [Evidence(kind="link", source=f"application:{b['application_id']}",
                          label=f"{reference(b['application_id'])}: {b['label']} is about {j:.0%} similar")],
                key_parts=(a["scope"], b["application_id"], b["scope"])))
    return out


def linked_apps(store: Any, app_id: str) -> set[str]:
    """Applications that share an identifier or wording with this one (for refreshing their flags too)."""
    linked: set[str] = set()
    for f in flags_for(store, app_id):
        linked.update(e.source.split(":", 1)[1] for e in f.evidence if e.kind == "link")
    return linked
