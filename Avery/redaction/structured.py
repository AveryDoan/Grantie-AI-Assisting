"""Step 2: redact an application's form fields and free-text answers.

Order of work:
1. location_class from the structured address/country fields (before redaction).
2. Known personal fields are replaced by name - no detection needed. Their
   values become KnownValues so they are caught anywhere else.
3. All other fields and the free-text answers go through the detector
   (Presidio + spaCy + custom recognisers + known values).
4. Tokens are allocated in a fixed traversal order, so numbering is
   deterministic: structured fields in config order, then other fields
   alphabetically, then answers alphabetically. (Documents come later, by id.)

Output text keeps the "field: value" line format the rest of the pipeline
already uses, and only the sensitive spans are replaced - sentences are never
rewritten and dates are never reformatted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from redaction.config import RedactionConfig, default_config
from redaction.detector import DETECTOR_VERSION, Detection, Detector
from redaction.location import PLACEHOLDER, LocationClass, classify_location
from redaction.recognizers import KnownValue
from redaction.tokenizer import Occurrence, TokenMap

APPLICATION_SOURCE = "application_text"


@dataclass
class ApplicationRedaction:
    redacted_text: str
    original_text: str
    redacted_fields: dict[str, dict[str, str]]
    location_class: LocationClass
    token_map: TokenMap
    known_values: list[KnownValue]
    low_confidence: int = 0
    report: dict[str, Any] = field(default_factory=dict)


def _first(fields: dict[str, Any], names: list[str]) -> str | None:
    for n in names:
        v = fields.get(n)
        if v not in (None, ""):
            return str(v)
    return None


def known_values_from_fields(fields: dict[str, Any], cfg: RedactionConfig) -> list[KnownValue]:
    known: list[KnownValue] = []
    for name, value in fields.items():
        token_type = cfg.field_token_type(name)
        if token_type and value not in (None, ""):
            known.append(KnownValue(str(value), token_type, name, cfg.person_group(name)))
    # "Given Family" and "Family Given" as full names, for family-name-first cultures and ID documents.
    given = _first(fields, ["given_name", "given_names", "preferred_name"])
    family = _first(fields, ["family_name", "surname"])
    if given and family:
        for full in (f"{given} {family}", f"{family} {given}", f"{family}, {given}"):
            known.append(KnownValue(full, "PERSON", "given_name+family_name", "applicant"))
    return known


def location_placeholder(cfg: RedactionConfig, address: str, country: str | None) -> str:
    return PLACEHOLDER[classify_location(address, country, cfg.country_set())]


def replacements_for(cfg: RedactionConfig, value: str, dets: list[Detection], known_addr: dict[str, str]) -> list[tuple]:
    """Detections -> (start, end, token_type, display_original, group). Shared by forms and documents."""
    reps = []
    for d in dets:
        if d.token_type == "ADDRESS":
            # A full address becomes a coarse location placeholder, never a precise one.
            display = known_addr.get(d.source or "") or location_placeholder(cfg, value[d.start : d.end + 60], None)
            reps.append((d.start, d.end, "ADDRESS", display, None))
        else:
            reps.append((d.start, d.end, d.token_type, value[d.start : d.end], d.group))
    return reps


def render(sections: list[tuple[str, str]]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in sections)


def original_application_text(application_text: dict[str, Any]) -> str:
    """The original text exactly as redaction renders it (officer view, quote mapping)."""
    sections = []
    for section in ("fields", "answers"):
        for name, value in (application_text.get(section) or {}).items():
            if value not in (None, ""):
                sections.append((name, str(value)))
    return render(sections)


class FieldRedactor:
    """Redacts form fields and answers for one application, sharing one TokenMap."""

    def __init__(self, cfg: RedactionConfig | None = None, detector: Detector | None = None) -> None:
        self.cfg = cfg or default_config()
        self.detector = detector or Detector(self.cfg)

    def _location_placeholder(self, address: str, country: str | None) -> str:
        return location_placeholder(self.cfg, address, country)

    def _replacements(self, value: str, dets: list[Detection], known_addr: dict[str, str]) -> list[tuple]:
        return replacements_for(self.cfg, value, dets, known_addr)

    def redact(self, application_text: dict[str, Any], tokens: TokenMap | None = None,
               extra_known: list[KnownValue] | None = None) -> ApplicationRedaction:
        cfg = self.cfg
        fields: dict[str, Any] = dict(application_text.get("fields") or {})
        answers: dict[str, Any] = dict(application_text.get("answers") or {})
        tokens = tokens or TokenMap(cfg)

        country = _first(fields, cfg.location_fields.get("country", []))
        address = _first(fields, cfg.location_fields.get("address", []))
        location_class = classify_location(address, country, cfg.country_set())

        known = known_values_from_fields(fields, cfg) + list(extra_known or [])
        # Placeholder per address field, computed from that field's own country.
        known_addr: dict[str, str] = {}
        for name, value in fields.items():
            if cfg.field_token_type(name) == "ADDRESS" and value:
                field_country = fields.get(name.replace("address", "country")) or country
                known_addr[name] = self._location_placeholder(str(value), field_country)

        # ---- deterministic traversal order
        structured_order = [n for names in cfg.person_groups.values() for n in names] + [
            n for names in cfg.structured_fields.values() for n in names]
        field_order = [n for n in dict.fromkeys(structured_order) if n in fields] + sorted(
            n for n in fields if n not in structured_order)
        redacted_fields: dict[str, str] = {}
        redacted_answers: dict[str, str] = {}
        per_source: dict[str, list[Occurrence]] = {}
        low = 0

        def do(section: str, name: str, value: str) -> str:
            nonlocal low
            source = f"{section}:{name}"
            token_type = cfg.field_token_type(name) if section == "fields" else None
            if token_type:
                if token_type == "ADDRESS":
                    reps = [(0, len(value), "ADDRESS", known_addr[name], None)]
                else:
                    reps = [(0, len(value), token_type, value, cfg.person_group(name))]
            else:
                dets = self.detector.detect(value, known, kind="form" if section == "fields" else "free_text")
                low += sum(d.low_confidence for d in dets)
                reps = self._replacements(value, dets, known_addr)
            out = tokens.apply(value, reps, source)
            per_source[source] = tokens.occurrences.pop(source)
            return out

        for name in field_order:
            v = fields[name]
            redacted_fields[name] = "" if v in (None, "") else do("fields", name, str(v))
        for name in sorted(answers):
            v = answers[name]
            redacted_answers[name] = "" if v in (None, "") else do("answers", name, str(v))

        # ---- render in the application's own order, with occurrences in rendered coordinates
        orig_sections, red_sections, combined = [], [], []
        o_pos = r_pos = 0
        for section, original, redacted in (("fields", fields, redacted_fields), ("answers", answers, redacted_answers)):
            for name, value in original.items():
                if value in (None, ""):
                    continue
                prefix = f"{name}: "
                o_val, r_val = str(value), redacted[name]
                for occ in per_source.get(f"{section}:{name}", []):
                    combined.append(Occurrence(occ.token, occ.original, f"{section}:{name}",
                                               r_pos + len(prefix) + occ.red_start, r_pos + len(prefix) + occ.red_end,
                                               o_pos + len(prefix) + occ.orig_start, o_pos + len(prefix) + occ.orig_end))
                orig_sections.append((name, o_val))
                red_sections.append((name, r_val))
                o_pos += len(prefix) + len(o_val) + 1
                r_pos += len(prefix) + len(r_val) + 1
        tokens.occurrences[APPLICATION_SOURCE] = combined

        self.known_addr = known_addr  # reused for documents
        result = ApplicationRedaction(
            redacted_text=render(red_sections),
            original_text=render(orig_sections),
            redacted_fields={"fields": redacted_fields, "answers": redacted_answers},
            location_class=location_class,
            token_map=tokens,
            known_values=known,
            low_confidence=low,
        )
        result.report = {
            "counts": tokens.counts(),
            "low_confidence_detections": low,
            "location_class": location_class,
            "detector_version": DETECTOR_VERSION,
            "config_hash": cfg.config_hash,
        }
        return result
