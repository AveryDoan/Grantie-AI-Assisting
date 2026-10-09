"""Coarse location classes, computed by code BEFORE redaction.

Rules such as "not already living in the NT" and "living outside Australia"
need where someone lives, not their address. A full address is replaced by
a coarse placeholder, and the class is kept as a structured field so rule
code can use it without any free text.
"""

from __future__ import annotations

import re
from typing import Literal

LocationClass = Literal["outside_australia", "nt_australia", "australia_outside_nt", "unknown"]

PLACEHOLDER: dict[str, str] = {
    "outside_australia": "[LOCATION: outside Australia]",
    "nt_australia": "[LOCATION: NT, Australia]",
    "australia_outside_nt": "[LOCATION: Australia, outside NT]",
    "unknown": "[LOCATION: unknown]",
}

_AUSTRALIA = re.compile(r"\b(Australia|Aust\.?|AUS)\b", re.IGNORECASE)
_NT = re.compile(
    r"\b(N\.?T\.?|Northern Territory)\b|\b(Darwin|Palmerston|Alice Springs|Katherine|Tennant Creek|Nhulunbuy|"
    r"Jabiru|Casuarina|Humpty Doo|Batchelor|Yulara)\b",
    re.IGNORECASE,
)
_NT_POSTCODE = re.compile(r"\b0[89]\d{2}\b")
_OTHER_STATE = re.compile(
    r"\b(NSW|VIC|QLD|SA|WA|TAS|ACT|New South Wales|Victoria|Queensland|South Australia|Western Australia|Tasmania|"
    r"Australian Capital Territory)\b(?:\s+\d{4})?",
)


def classify_location(address: str | None, country: str | None, countries: frozenset[str] = frozenset()) -> LocationClass:
    """Classify from the country field first, then the address text.

    Australia is only "outside NT" with positive evidence of another state;
    an Australian address with no state is "unknown" (never assumed).
    """
    address = (address or "").strip()
    country_norm = (country or "").strip().casefold()
    in_australia: bool | None = None
    if country_norm:
        in_australia = country_norm in {"australia", "au", "aus", "commonwealth of australia"}
    elif address:
        if _AUSTRALIA.search(address) or _OTHER_STATE.search(address):
            in_australia = True
        elif any(re.search(rf"\b{re.escape(c)}\b", address, re.IGNORECASE) for c in countries if c not in ("australia",)):
            in_australia = False

    if in_australia is False:
        # Country field says abroad but the address looks Australian: do not guess.
        if address and (_AUSTRALIA.search(address) or _OTHER_STATE.search(address)
                        or (_NT.search(address) and _NT_POSTCODE.search(address))):
            return "unknown"
        return "outside_australia"
    if in_australia is None:
        return "unknown"
    if _NT.search(address) or _NT_POSTCODE.search(address):
        return "nt_australia"
    if _OTHER_STATE.search(address):
        return "australia_outside_nt"
    return "unknown"
