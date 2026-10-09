"""Synthetic redaction evaluation set with labelled personal values.

ALL FICTIONAL. Each case lists the personal values that must NOT reach the
LLM, and harmless terms that MUST survive (the rules need them). Values are
deliberately varied: many cultures and name orders, international phones,
social handles, IDs, plain and second-language English, referee letters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from redaction.extract import ExtractedDocument, extract_document


@dataclass
class EvalCase:
    case_id: str
    group: str
    application_text: dict
    documents: list[ExtractedDocument] = field(default_factory=list)
    kinds: dict[str, str] = field(default_factory=dict)
    personal: list[tuple[str, str]] = field(default_factory=list)  # (value, type) that must be removed
    keep: list[str] = field(default_factory=list)                   # harmless terms that must survive
    expected_other: list[str] = field(default_factory=list)         # unlabelled text that may rightly be redacted


def _text_doc(doc_id: str, text: str) -> ExtractedDocument:
    return extract_document(doc_id, f"{doc_id}.txt", text.encode("utf-8"))


def seeded_cases() -> list[EvalCase]:
    """The 9 Study NT applicants and their sample documents."""
    from seed import data

    out = []
    for case in [c for c in data.CASES if c["code"].startswith("N")]:
        f = case["application_text"]["fields"]
        docs, kinds = [], {}
        for i, (declared, text) in enumerate(case["documents"]):
            doc_id = f"{case['code']}-doc{i}"
            docs.append(_text_doc(doc_id, text))
            if declared == "referee letter":
                kinds[doc_id] = "referee_letter"
        personal = [(f["applicant_name"], "PERSON"), (f["email"], "EMAIL"), (f["phone"], "PHONE"),
                    (f["date_of_birth"], "DOB"), (f["residential_address"].split(",")[0], "ADDRESS")]
        if any("Hoa Pham" in t for _, t in case["documents"]):
            personal.append(("Hoa Pham", "REFEREE"))
        if any("Minh Le" in t for _, t in case["documents"]):
            personal.append(("Minh Le", "REFEREE"))
        keep = [f["education_provider"], f["course_name"], f["course_start_date"], f["arrival_date"],
                f["residential_country"], "Head of Science", "3 years", "Visa Subclass: 500",
                "Darwin", "Northern Territory", "Red Cross", "biology"]
        # The address city/district and the CoE code are personal-adjacent and rightly redacted.
        other = ["Hanoi", "Hoan Kiem", "E0000000"]
        out.append(EvalCase(case["code"], f"seeded ({case['style']})", case["application_text"], docs, kinds, personal, keep, other))
    return out


# Multi-culture names (fairness / name recall), fictional people only.
NAMES: dict[str, list[str]] = {
    "Vietnamese (family first)": ["Nguyen Van An", "Tran Thi Mai", "Le Hoang Nam"],
    "Chinese (family first)": ["Wang Xiaoming", "Zhang Wei", "Chen Yuting"],
    "Korean (family first, hyphenated)": ["Park Ji-hoon", "Kim Min-seo"],
    "Japanese (family first)": ["Sato Haruka"],
    "Indian": ["Priya Raghunathan", "Arjun Venkataraman", "Sukhdeep Kaur"],
    "Nepali": ["Sabin Shrestha", "Anisha Gurung"],
    "Sri Lankan": ["Thushara Wickramasinghe"],
    "Bangladeshi": ["Mohammad Rahim Uddin"],
    "Filipino (multi-part)": ["Maria Clara Dela Cruz"],
    "Indonesian (single name)": ["Wulandari", "Sutrisno"],
    "Arabic (particles, hyphen)": ["Fatima Al-Zahrani", "Omar Abdullah Haddad"],
    "Persian": ["Reza Ahmadi"],
    "Nigerian": ["Chukwuemeka Okonkwo", "Ngozi Adeyemi"],
    "Kenyan": ["Wanjiru Kamau"],
    "Thai": ["Somchai Wongsakul"],
    "Mongolian": ["Batbayar Dorj"],
    "Spanish (hyphenated)": ["José García-López"],
    "Pacific": ["Sione Tupou", "Losana Ratu"],
    "Anglo": ["Emily Carter", "James O'Neill"],
}

# Sentences in which another person is named in free text (no known values).
TEMPLATES = [
    "I worked with {name} at the clinic last year.",
    "My mentor {name} encouraged me to apply.",
    "During the project, {name} and I organised a health workshop.",
]


# Free-text cases: personal values the system was NOT told about (detector only),
# mixed with terms the rules need.
FREE_TEXT = [
    ("FT01", "Vietnamese, family first",
     "I worked with Nguyen Thi Hoa at the clinic. You can call her on +84 912 345 678. I start at Charles Darwin University on 5 October 2026.",
     [("Nguyen Thi Hoa", "PERSON"), ("+84 912 345 678", "PHONE")], ["Charles Darwin University", "5 October 2026"]),
    ("FT02", "Chinese, second-language",
     "My teacher Wang Xiaoming he help me very much. His email is wang.xm@example.invalid. I study Bachelor of Nursing.",
     [("Wang Xiaoming", "PERSON"), ("wang.xm@example.invalid", "EMAIL")], ["Bachelor of Nursing"]),
    ("FT03", "Indian, plain",
     "Arjun Venkataraman is my mentor. He works in Chennai. I live in India now and will hold a subclass 500 visa.",
     [("Arjun Venkataraman", "PERSON"), ("Chennai", "PLACE")], ["India", "subclass 500"]),
    ("FT04", "Nepali",
     "My friend Sabin Shrestha posts my photos at @sabin.shrestha99 and instagram.com/sabin.shrestha99.",
     [("Sabin Shrestha", "PERSON"), ("@sabin.shrestha99", "HANDLE"), ("instagram.com/sabin.shrestha99", "URL")], []),
    ("FT05", "Filipino, multi-part",
     "Maria Clara Dela Cruz supervised my volunteering at the Red Cross in Manila for two years.",
     [("Maria Clara Dela Cruz", "PERSON"), ("Manila", "PLACE")], ["two years"]),
    ("FT06", "Arabic, particles",
     "Omar Abdullah Haddad and Fatima Al-Zahrani are my referees. Passport number: A12345678.",
     [("Omar Abdullah Haddad", "PERSON"), ("Fatima Al-Zahrani", "PERSON"), ("A12345678", "ID")], []),
    ("FT07", "Nigerian, second-language",
     "Mr Chukwuemeka Okonkwo is my uncle, he live at 14 Example Close, Lagos. Phone +234 803 123 4567.",
     [("Chukwuemeka Okonkwo", "PERSON"), ("14 Example Close", "ADDRESS"), ("+234 803 123 4567", "PHONE")], []),
    ("FT08", "Korean, hyphenated",
     "Park Ji-hoon taught me English. I was born on 3 March 2007 in Busan. My student ID is s7654321.",
     [("Park Ji-hoon", "PERSON"), ("3 March 2007", "DOB"), ("Busan", "PLACE"), ("s7654321", "ID")], []),
    ("FT09", "Pacific, single line",
     "Sione Tupou and Losana Ratu run the youth group. I will arrive in Darwin on 20 September 2026.",
     [("Sione Tupou", "PERSON"), ("Losana Ratu", "PERSON")], ["Darwin", "20 September 2026"]),
    ("FT10", "Indonesian single name",
     "Wulandari is my older sister. She studied at Darwin City College and holds the CDU Global Merit Scholarship.",
     [("Wulandari", "PERSON")], ["Darwin City College", "CDU Global Merit Scholarship"]),
    ("FT11", "Spanish, hyphenated, accents",
     "José García-López wrote my reference. Contact: jose.garcia@example.invalid or 0412 555 019.",
     [("José García-López", "PERSON"), ("jose.garcia@example.invalid", "EMAIL"), ("0412 555 019", "PHONE")], []),
    ("FT12", "Vietnamese, three-part, lower-case cue",
     "During the project, Le Hoang Nam and I organised a first-aid workshop at Alice Springs College of Australia.",
     [("Le Hoang Nam", "PERSON")], ["Alice Springs College of Australia"]),
]

REFEREE_LETTERS = [
    ("RL01", "Kenyan referee",
     "To whom it may concern,\nI have supervised the applicant for 3 years as her manager.\nYours sincerely,\nWanjiru Kamau\n"
     "Position: Operations Manager\nOrganisation: Fictional Logistics Nairobi\nPhone: +254 712 345 678\nDate: 2 June 2026",
     [("Wanjiru Kamau", "REFEREE"), ("+254 712 345 678", "PHONE")], ["Operations Manager", "3 years", "2 June 2026"]),
    ("RL02", "Mongolian referee, title",
     "To whom it may concern,\nThe applicant was my student for two years.\nDr Batbayar Dorj\nPosition: Lecturer\n"
     "Email: b.dorj@example.invalid\nDate: 15 April 2026",
     [("Batbayar Dorj", "REFEREE"), ("b.dorj@example.invalid", "EMAIL")], ["Lecturer", "two years", "15 April 2026"]),
    ("RL03", "Thai referee, labelled",
     "To whom it may concern,\nI have known the applicant for 4 years.\nReferee name: Somchai Wongsakul\n"
     "Relationship: Football coach\nDate: 1 May 2026",
     [("Somchai Wongsakul", "REFEREE")], ["Football coach", "4 years", "1 May 2026"]),
]

# Unlabelled places that are still personal context (where someone lives or works).
OTHER = {"FT07": ["Lagos"], "RL01": ["Nairobi"]}


def free_text_cases() -> list[EvalCase]:
    out = []
    for cid, group, text, personal, keep in FREE_TEXT:
        out.append(EvalCase(cid, f"free text: {group}", {"fields": {"applicant_name": "Test Applicant"},
                                                          "answers": {"answer": text}}, personal=personal, keep=keep,
                            expected_other=["Test Applicant", *OTHER.get(cid, [])]))
    for cid, group, text, personal, keep in REFEREE_LETTERS:
        doc = _text_doc(f"{cid}-letter", text)
        out.append(EvalCase(cid, f"referee letter: {group}", {"fields": {"applicant_name": "Test Applicant"}, "answers": {}},
                            [doc], {doc.document_id: "referee_letter"}, personal, keep,
                            ["Test Applicant", *OTHER.get(cid, [])]))
    return out


def all_cases() -> list[EvalCase]:
    return seeded_cases() + free_text_cases()
