"""Synthetic test applications with deliberate inconsistencies (F01 to F10).

Everything here is FICTIONAL: invented names, example.com / example.org email addresses, and phone numbers from the
ACMA's list of numbers reserved for fiction (mobiles 0491 57x xxx, and the whole (0x) 5550 xxxx and 7010 xxxx
ranges). No real people, institutions' letterheads or documents.

Each case is written so that every rule passes on its own and the contradiction is only visible across items.
The expected flags were written ALONGSIDE the checks: they show that each check can find a planted problem, not how
well the checks do on applications nobody wrote for them.

`python -m seed.fraud.generate` turns these into PDFs and form JSON. Nothing is hand-edited.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seed.fraud.pdfkit import pdf_date

SUBMITTED = "2026-10-12"
SUBMITTED_AT = "2026-10-12T09:00:00+00:00"
FOOTER = "SAMPLE DOCUMENT - FICTIONAL - NOT A REAL DOCUMENT"


@dataclass
class Doc:
    kind: str                      # declared type: coe | offer letter | visa | travel booking | referee letter | headshot | other
    lines: list[str]
    info: dict[str, str] | None = None


@dataclass
class Scenario:
    code: str
    title: str
    name: str
    email: str
    fields: dict[str, str]
    answers: dict[str, str]
    docs: list[Doc]
    should: list[str]
    allow: list[str] = field(default_factory=list)
    should_not: list[str] = field(default_factory=list)
    control: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------- document builders


def coe(name: str, course: str, start: str, end: str, duration: str, issued: str, code: str) -> Doc:
    return Doc("coe", [FOOTER, "Confirmation of Enrolment", "Provider: Charles Darwin University", f"Student Name: {name}",
                       f"Course: {course}", f"Course Start Date: {start}", f"Course End Date: {end}",
                       f"Course Duration: {duration}", "Study Load: Full-time", f"Date CoE Issued: {issued}", f"CoE Code: {code}"])


def offer(name: str, course: str, dated: str) -> Doc:
    return Doc("offer letter", [FOOTER, "Letter of Offer", f"Date: {dated}", f"Dear {name},",
                                f"We are pleased to offer you a place in the {course} at Charles Darwin University. You have met the "
                                "academic entry requirements and the English entry requirements for this course (IELTS 6.5 or equivalent). "
                                "Please accept this offer to receive your Confirmation of Enrolment."])


def visa(family: str, given: str, granted: str, expiry: str = "15 March 2030") -> Doc:
    return Doc("visa", [FOOTER, "Visa Grant Notice", f"Visa Holder: {family.upper()}, {given}", "Visa Subclass: 500 (Student)",
                        f"Date of Grant: {granted}", f"Visa Expiry: {expiry}"])


def booking(name: str, city: str, code: str, depart: str, arrive: str, ref: str) -> Doc:
    return Doc("travel booking", [FOOTER, "Flight Booking Confirmation - Itinerary", f"Booking Reference: {ref}", f"Passenger: {name.upper()}",
                                  "Flight Number: XX 123", f"Departure: {city} ({code}) {depart} 22:10", f"Arrival: Darwin (DRW) {arrive} 06:05", "Class: Economy"])


def letter(org: str, referee: str, position: str, relationship: str, known: str, dated: str, email: str, phone: str,
           body: list[str], info: dict[str, str] | None = None, opening: str = "To whom it may concern,") -> Doc:
    return Doc("referee letter", [FOOTER, f"{org.upper()} - LETTERHEAD", "", opening, "", *[p for para in body for p in (para, "")],
                                  "Yours sincerely,", "", f"Referee name: {referee}", f"Position: {position}", f"Organisation: {org}",
                                  f"Relationship: {relationship}", f"Known applicant for: {known}", f"Date: {dated}", f"Email: {email}",
                                  f"Phone: {phone}", "Signature: [signed]"], info)


def headshot() -> Doc:
    return Doc("headshot", [FOOTER, "SAMPLE IMAGE FILE - FICTIONAL headshot photograph of the applicant (image content is not machine-readable)"])


def resume(name: str, events: list[str], extra: list[str] | None = None) -> Doc:
    return Doc("other", [FOOTER, "RESUME", name, "", "Education and experience", *events, "", *(extra or [])])


def transcript(name: str, program: str, institution: str, years: int, note: str) -> Doc:
    return Doc("other", [FOOTER, "OFFICIAL ACADEMIC TRANSCRIPT (SAMPLE)", f"Student: {name}", f"Program: {program}",
                         f"Institution: {institution}", f"Years completed: {years}", "Credits earned: 48", f"Status: {note}"])


def form(name: str, dob: str, email: str, phone: str, nationality: str, address: str, country: str, course: str, start: str,
         arrival: str, **extra: str) -> dict[str, str]:
    return {
        "applicant_name": name, "date_of_birth": dob, "email": email, "phone": phone, "nationality": nationality,
        "australian_or_nz_citizen_or_pr": "No", "residential_address": address, "residential_country": country,
        "postal_address": address, "postal_country": country, "education_provider": "Charles Darwin University",
        "course_name": course, "course_start_date": start, "study_load": "Full-time", "arrival_date": arrival,
        "under_18": "No", "declaration_agreed": "Yes", "declaration_name": name, "declaration_date": SUBMITTED, **extra,
    }


STD_START, STD_ARRIVE_DEPART, STD_ARRIVE = "2026-11-16", "1 November 2026", "2 November 2026"
ARRIVE_ISO = "2026-11-02"

# ---------------------------------------------------------------------------- the ring's shared letter template (F07 to F09)

RING_PARA = [
    "I am writing to support {first}'s application for the Study in Australia's Northern Territory Scholarship.",
    "{first} has been a {adj1} member of our programme and consistently shows {noun1} in everything {pronoun} does.",
    "In class {pronoun} asks thoughtful questions, helps classmates who are struggling and finishes every project on time. "
    "Colleagues describe {first} as {adj2} and dependable, and {pronoun} has represented the institute at two regional events.",
    "Outside the classroom, {first} organises study groups, volunteers at community days and mentors younger students. "
    "I believe the Northern Territory would benefit greatly from {pronoun2} energy, curiosity and commitment to others.",
    "I give {first} my strongest recommendation and I am happy to answer any questions about {pronoun2} work.",
]


def ring_letter(first: str, referee: str, email: str, phone: str, dated: str, swaps: dict[str, str], pronoun: str = "she", pronoun2: str = "her") -> Doc:
    words = {"adj1": "dedicated", "noun1": "diligence", "adj2": "generous"} | swaps
    body = [p.format(first=first, pronoun=pronoun, pronoun2=pronoun2, **words) for p in RING_PARA]
    return letter("Riverbend Institute", referee, "Programme Coordinator", "Programme coordinator and tutor", "2 years", dated, email, phone, body,
                  opening="To the Assessment Panel,")


# ---------------------------------------------------------------------------- the scenarios


def build() -> list[Scenario]:
    out: list[Scenario] = []

    # F01 ---------------------------------------------------------------- arrival well after the course starts
    n = "Lakshmi Aldridge"
    out.append(Scenario(
        "F01", "Arrival date is four weeks after the course starts", n, "lakshmi.aldridge@example.com",
        form(n, "2003-02-17", "lakshmi.aldridge@example.com", "0491 570 006", "India", "9 Marigold Court, Pune 411001, India", "India",
             "Bachelor of Nursing", STD_START, "2026-12-14"),
        {"current_study": "I am finishing the last term of my higher secondary certificate in Pune.",
         "academic_achievements": "I finished Year 12 with 91 percent and first place in biology in my district.",
         "leadership": "I captain my school's first-aid team of eleven students and run the monthly training evening.",
         "community_engagement": "On Sundays I help at a free eye-screening camp organised by a local trust.",
         "nt_contribution": "I want to work as a nurse in remote Northern Territory clinics, where I can use my training in first aid and my "
                            "experience with screening camps to help communities that live far from a hospital.",
         "biography": "Lakshmi is a quiet, determined student from Pune who has wanted to become a nurse since she helped care for her grandmother "
                      "after a long illness. At school she studies biology, chemistry and mathematics, and she has led the first-aid team for two "
                      "years. Her teachers describe her as careful, calm in a crisis and always willing to stay late to help a classmate revise. "
                      "On weekends she helps at a free eye-screening camp where she registers patients, explains the process in three languages "
                      "and keeps the queue moving. She plays the flute, cooks for her family on Fridays and walks to school every day. In Darwin "
                      "she hopes to complete her nursing degree, join a student health society and eventually work in a remote clinic, bringing "
                      "the same patience and care she has learned at home."},
        [coe(n, "Bachelor of Nursing", "16 November 2026", "15 November 2029", "3 years (6 semesters)", "14 September 2026", "E4401291"),
         offer(n, "Bachelor of Nursing", "12 August 2026"), visa("Aldridge", "Lakshmi", "28 September 2026"),
         booking(n, "Mumbai", "BOM", "13 December 2026", "14 December 2026", "LK4D1P"),
         letter("Pune Girls Higher Secondary School", "Mrs Anjali Deshpande", "Biology Teacher", "Class teacher", "3 years", "8 June 2026",
                "a.deshpande@punegirls.example.org", "+61 2 5550 4417",
                ["I have taught Lakshmi biology for three years and have been her class teacher since 2024.",
                 "She is the most careful practical student I have taught. In our dissection lessons she checks every step twice and never "
                 "rushes a result, and she captains the first-aid team with a patience that younger students respond to.",
                 "I recommend her without reservation."]),
         letter("Pune Community Eye Trust", "Dr Rohan Kulkarni", "Volunteer Coordinator", "Volunteer supervisor", "2 years", "21 June 2026",
                "r.kulkarni@puneeyetrust.example.org", "+61 3 5550 2290",
                ["Lakshmi has volunteered at our Sunday screening camp since the middle of 2024. She registers patients, explains each test "
                 "in plain language and stays until the last patient has been seen.",
                 "In two years she has missed only three Sundays. Patients ask for her by name.", "She would be a credit to any hospital."]),
         headshot(),
         resume(n, ["Higher Secondary Certificate (Science), Pune Girls Higher Secondary School, June 2022 - present, full-time",
                    "Volunteer, Pune Community Eye Trust screening camp, July 2024 - present, part-time",
                    "Captain, school first-aid team, July 2024 - present"])],
        should=["cross_document.arrival_vs_start"],
        should_not=["timeline.*", "narrative.*", "cross_application.*", "document_integrity.*"],
        notes="Everything agrees except the arrival date: 28 days after the course starts. Only visible by comparing the CoE with the booking."))

    # F02 ---------------------------------------------------------------- CoE length vs typed dates; a letter dated 2022
    n = "Tomasz Brindleworth"
    out.append(Scenario(
        "F02", "CoE length does not match the typed dates; one letter is dated 2022", n, "tomasz.brindleworth@example.com",
        form(n, "2002-08-03", "tomasz.brindleworth@example.com", "0491 570 156", "Poland", "27 Linden Row, Gdansk 80-001, Poland", "Poland",
             "Bachelor of Information Technology", STD_START, ARRIVE_ISO, course_end_date="2029-11-15"),
        {"current_study": "I am in the final year of upper secondary school at a technical school in Gdansk.",
         "academic_achievements": "I rank fourth of 120 students and won the regional programming contest in my second year.",
         "leadership": "I started a robotics club that now has fourteen members and meets twice a week.",
         "community_engagement": "I teach basic computer skills to older residents at the public library on Saturdays.",
         "nt_contribution": "Studying information technology in Darwin will let me build software for small businesses and community groups in "
                            "the Territory, such as simple booking and records systems that save volunteers hours of paperwork.",
         "biography": "Tomasz grew up in Gdansk, where he learned to program on an old laptop his uncle repaired for him. He studies at a technical "
                      "school, ranks near the top of his class and has won a regional programming contest. He founded the school robotics club, "
                      "which builds small line-following robots and visits primary schools to show younger children how they work. On Saturdays he "
                      "volunteers at the public library, helping older residents use email, online banking and video calls to stay in touch with "
                      "family overseas. His teachers say he explains difficult ideas patiently and never gives up on a bug. He speaks Polish, "
                      "English and some German, plays chess and sails on the Baltic in summer. In Darwin he wants to study information technology "
                      "and later build tools that help community groups work together."},
        [coe(n, "Bachelor of Information Technology", "16 November 2026", "30 November 2031", "3 years (6 semesters)", "14 September 2026", "E5512870"),
         offer(n, "Bachelor of Information Technology", "10 August 2026"), visa("Brindleworth", "Tomasz", "28 September 2026"),
         booking(n, "Warsaw", "WAW", "1 November 2026", "2 November 2026", "TB9K2M"),
         letter("Gdansk Technical School No. 3", "Mr Jan Wisniewski", "Computer Science Teacher", "Class teacher", "3 years", "18 May 2022",
                "j.wisniewski@gdanskts3.example.org", "+61 7 7010 3318",
                ["Tomasz has been in my programming class for three years. He learns quickly, helps others and has built the robotics club "
                 "from nothing into a team that other schools invite to demonstrations.",
                 "I am glad to recommend him."]),
         letter("Gdansk City Library", "Ms Ewa Nowicka", "Community Programs Officer", "Volunteer supervisor", "2 years", "2 September 2026",
                "e.nowicka@gdanskcitylibrary.example.org", "+61 8 7010 5526",
                ["Tomasz has taught our Saturday computer class for two years. Our older residents are patient with themselves because he is "
                 "patient with them.", "He is reliable, kind and well prepared every week."]),
         headshot()],
        should=["cross_document.coe_length", "cross_document.typed_coe_end", "cross_document.referee_date_window"],
        should_not=["timeline.*", "narrative.*", "cross_application.*", "document_integrity.*"],
        notes="The CoE's own dates cover about 5 years while it states 3, the typed end date follows the 3 years, and one letter is dated 2022. "
              "Rule D5 will also fail on its own for the 2022 letter; the other rules pass."))

    # F03 ---------------------------------------------------------------- 'known for five years' vs the letter's own dates
    n = "Nguyet Hallowell"
    out.append(Scenario(
        "F03", "A referee says five years; the same letter's dates cover eight months", n, "nguyet.hallowell@example.com",
        form(n, "2003-11-22", "nguyet.hallowell@example.com", "0491 570 157", "Vietnam", "31 Lotus Lane, Hue 49000, Vietnam", "Vietnam",
             "Bachelor of Health Science", STD_START, ARRIVE_ISO),
        {"current_study": "I am finishing my last year at Quoc Hoc High School in Hue.",
         "academic_achievements": "I scored 9.4 out of 10 in chemistry and took second place in the provincial science fair.",
         "leadership": "I lead a group of nine students who run a recycling programme at our school.",
         "community_engagement": "I help in a university research group on weekday afternoons and tutor primary students on weekends.",
         "nt_contribution": "I want to study health science in Darwin and come to understand how health services reach people in very remote "
                            "places, because I want to build programmes that help families who live far from clinics.",
         "biography": "Nguyet lives in Hue, the old imperial city, and is the first in her family to plan for university overseas. She loves "
                      "chemistry and spends most afternoons in a small laboratory run by a local university, where she helps prepare samples and "
                      "record results. She also leads a student recycling team that collects and sorts plastic from six schools each month. "
                      "Friends describe her as organised, curious and happy to share her notes before every exam. She enjoys drawing, "
                      "cooking bun bo with her grandmother and walking along the Perfume River at dusk. After the scholarship she hopes to "
                      "complete a health science degree in Darwin, volunteer with community health groups and return to Vietnam to work on "
                      "programmes that bring screening and advice to families who live far from a clinic."},
        [coe(n, "Bachelor of Health Science", "16 November 2026", "15 November 2029", "3 years (6 semesters)", "14 September 2026", "E6630125"),
         offer(n, "Bachelor of Health Science", "9 August 2026"), visa("Hallowell", "Nguyet", "28 September 2026"),
         booking(n, "Da Nang", "DAD", "1 November 2026", "2 November 2026", "NH3C8Q"),
         letter("Hue Applied Chemistry Laboratory", "Dr Pham Thi Lan", "Principal Researcher", "Research supervisor", "5 years", "10 September 2026",
                "p.lan@huechemlab.example.org", "+61 2 7010 8842",
                ["I have known Nguyet for five years and have watched her grow into a careful and thoughtful young scientist.",
                 "She first joined my research group in January 2026 and since then she has prepared samples, kept clear records and "
                 "helped train two new assistants. Her notebooks are the neatest in the laboratory.",
                 "I recommend her warmly."]),
         letter("Quoc Hoc High School", "Mr Tran Van Duc", "Chemistry Teacher", "Class teacher", "3 years", "12 June 2026",
                "t.duc@quochoc.example.org", "+61 3 7010 6615",
                ["I have taught Nguyet chemistry for three years. She asks excellent questions, helps classmates before exams and led our "
                 "school's recycling team from its first meeting.", "She is an outstanding student and a kind person."]),
         headshot()],
        should=["cross_document.known_for_vs_timeline", "narrative.conflicting_statements"],
        should_not=["timeline.*", "cross_application.*", "document_integrity.*"],
        notes="The first letter says 'known for five years' but also 'first joined my research group in January 2026': about eight months."))

    # F04 ---------------------------------------------------------------- timeline impossibilities
    n = "Farid Waverley"
    out.append(Scenario(
        "F04", "A role before the applicant could have held it, overlapping full-time work, a visa before the CoE", n, "farid.waverley@example.com",
        form(n, "2004-06-10", "farid.waverley@example.com", "0491 570 158", "Pakistan", "5 Jasmine Road, Lahore 54000, Pakistan", "Pakistan",
             "Bachelor of Business", STD_START, ARRIVE_ISO),
        {"current_study": "I am studying at a college in Lahore and will finish my diploma this year.",
         "academic_achievements": "I have a distinction average and received the college's business studies award last year.",
         "leadership": "I run a youth robotics club and manage a small team at a retail company.",
         "community_engagement": "I raise money each Ramadan for a food bank that serves families near my college.",
         "nt_contribution": "I want to study business in Darwin and learn how small enterprises in remote communities can grow, because I have "
                            "managed a team and know how much a good plan can change a business.",
         "biography": "Farid is a business student from Lahore who has combined study, work and community projects since his early teens. He "
                      "studies full time at a college, works in retail and has founded a youth robotics club that teaches schoolchildren to build "
                      "and programme simple machines. Each Ramadan he organises a fundraising drive for a food bank, collecting donations from "
                      "local shops and packing parcels with a team of friends. Colleagues describe him as ambitious, energetic and good with "
                      "customers. He enjoys cricket, photography and cooking biryani for large groups. In Darwin he wants to study business, "
                      "learn how small enterprises grow in regional places and one day start a company that gives young people their first "
                      "job. He believes a good plan, a good team and some patience can change a small business."},
        [coe(n, "Bachelor of Business", "16 November 2026", "15 November 2029", "3 years (6 semesters)", "14 September 2026", "E7704418"),
         offer(n, "Bachelor of Business", "11 August 2026"), visa("Waverley", "Farid", "3 August 2026"),
         booking(n, "Lahore", "LHE", "1 November 2026", "2 November 2026", "FW7T2N"),
         letter("Lahore Business College", "Prof Imran Siddiqui", "Head of Business Studies", "Course coordinator", "2 years", "20 June 2026",
                "i.siddiqui@lahorebizcollege.example.org", "+61 7 5550 6671",
                ["Farid has been in my course for two years. He is a confident presenter and an organised team leader, and he balances his "
                 "studies with a demanding schedule.", "I am pleased to recommend him."]),
         letter("Example Retail Co", "Ms Saima Qureshi", "Store Manager", "Direct manager", "2 years", "1 July 2026",
                "s.qureshi@exampleretail.example.org", "+61 8 5550 7703",
                ["Farid has worked in my store for two years. He handles customers well, trains new staff and is honest with the cash.",
                 "I would employ him again at any time."]),
         headshot(),
         resume(n, ["Founder and President, Youth Robotics Club, March 2013 - present",
                    "Diploma of Business, Lahore Business College, September 2023 - present, full-time",
                    "Sales Associate, Example Retail Co, January 2024 - present, full-time",
                    "Data Entry Clerk, Sample Logistics Pvt Ltd, March 2024 - present, full-time"])],
        should=["timeline.role_before_age", "timeline.overlapping_full_time", "timeline.visa_before_coe"],
        should_not=["cross_document.*", "narrative.*", "cross_application.*", "document_integrity.*"],
        notes="Born 2004, president of a club from 2013 (age 8 or 9); two full-time jobs and full-time study overlap; the visa is dated before the CoE."))

    # F05 ---------------------------------------------------------------- narrative contradiction with a transcript
    n = "Chidi Marlowe"
    out.append(Scenario(
        "F05", "'Studied engineering for three years' against a transcript showing one", n, "chidi.marlowe@example.com",
        form(n, "2001-05-09", "chidi.marlowe@example.com", "0491 570 159", "Nigeria", "18 Palm Close, Enugu 400001, Nigeria", "Nigeria",
             "Bachelor of Nursing", STD_START, ARRIVE_ISO),
        {"current_study": "I studied engineering for three years at Enugu Institute of Technology before I moved into healthcare.",
         "academic_achievements": "I finished a certificate in community health with a credit average and received the training centre's attendance award.",
         "leadership": "I coordinate eight volunteers at a weekend clinic run by our church.",
         "community_engagement": "I help with blood-pressure checks and keep the waiting-room records at the clinic every Saturday.",
         "nt_contribution": "I want to become a nurse and work in remote Northern Territory communities, because I have seen how much a calm, "
                            "practical health worker can help when the nearest hospital is hours away.",
         "biography": "Chidi lives in Enugu and changed direction after a family illness showed him how much a good nurse matters. He left "
                      "engineering to study community health, completed a certificate and now coordinates volunteers at a weekend clinic, where "
                      "he checks blood pressure, keeps patient records and calms anxious families in the waiting room. Co-workers describe him "
                      "as steady, humorous and quick to learn new procedures. He plays football with the clinic staff on Sundays, sings in the "
                      "church choir and cooks jollof rice for the volunteers after long shifts. In Darwin he wants to complete a nursing degree, "
                      "join a student health group and later work in a remote clinic. He believes health care starts with listening to "
                      "people and treating each patient as part of a family."},
        [coe(n, "Bachelor of Nursing", "16 November 2026", "15 November 2029", "3 years (6 semesters)", "14 September 2026", "E8821536"),
         offer(n, "Bachelor of Nursing", "13 August 2026"), visa("Marlowe", "Chidi", "28 September 2026"),
         booking(n, "Lagos", "LOS", "1 November 2026", "2 November 2026", "CM5R9W"),
         letter("Enugu Community Health Centre", "Dr Adaeze Obi", "Clinic Director", "Volunteer supervisor", "3 years", "15 June 2026",
                "a.obi@enuguhealth.example.org", "+61 2 5550 9024",
                ["Chidi has volunteered at our Saturday clinic for three years. He is calm with patients, accurate with records and "
                 "generous with his time.", "I recommend him without hesitation."]),
         letter("Enugu Institute of Technology", "Mr Emeka Nwosu", "Lecturer", "Former lecturer", "1 year", "30 June 2026",
                "e.nwosu@enugutech.example.org", "+61 3 5550 1186",
                ["I taught Chidi in his first-year engineering mathematics course. He was diligent and often helped classmates, and I was "
                 "sorry to see him leave the programme.", "I am confident he will do well in any field he chooses."]),
         transcript(n, "Bachelor of Engineering", "Enugu Institute of Technology", 1, "Withdrawn after the first year"),
         headshot()],
        should=["narrative.conflicting_statements"],
        should_not=["cross_document.*", "timeline.*", "cross_application.*", "document_integrity.*"],
        notes="The form says three years of engineering; the transcript shows one year completed and a withdrawal. A referee's letter agrees with one year."))

    # F06 ---------------------------------------------------------------- weak metadata signals only
    n = "Sofia Quennell"
    out.append(Scenario(
        "F06", "A letter's PDF was made after its printed date, with an editing program (weak signals only)", n, "sofia.quennell@example.com",
        form(n, "2003-09-14", "sofia.quennell@example.com", "0491 570 110", "Brazil", "42 Ipe Avenue, Curitiba 80010, Brazil", "Brazil",
             "Bachelor of Education (Primary)", STD_START, ARRIVE_ISO),
        {"current_study": "I am in the final term of my teaching preparation course in Curitiba.",
         "academic_achievements": "I graduated near the top of my class and received the faculty's prize for classroom practice.",
         "leadership": "I lead a reading programme where six volunteers read to children at a local shelter.",
         "community_engagement": "Twice a week I run a homework hour for children at the shelter near my neighbourhood.",
         "nt_contribution": "I want to become a primary teacher and work in remote schools in the Northern Territory, where I can use what I "
                            "have learned about teaching children who are learning in a second language.",
         "biography": "Sofia comes from Curitiba and has loved reading aloud since she was a child. At university she is training to teach "
                      "primary students, and her supervisors praise the way she keeps a whole class interested and still has time for the "
                      "quietest child. She leads a volunteer reading programme at a local shelter and runs a homework hour twice a week, "
                      "where children bring workbooks, snacks and plenty of questions. Friends describe her as warm, patient and "
                      "wonderfully organised. She plays the guitar, bakes pao de queijo for her classmates and spends holidays visiting her "
                      "grandparents in the countryside. In Darwin she plans to complete her education degree, learn from teachers who work "
                      "with many languages in one classroom and later teach in a remote school, helping children discover that stories "
                      "can take them anywhere."},
        [coe(n, "Bachelor of Education (Primary)", "16 November 2026", "15 November 2029", "3 years (6 semesters)", "14 September 2026", "E9953204"),
         offer(n, "Bachelor of Education (Primary)", "12 August 2026"), visa("Quennell", "Sofia", "28 September 2026"),
         booking(n, "Sao Paulo", "GRU", "31 October 2026", "2 November 2026", "SQ2H6D"),
         letter("Curitiba Teaching College", "Dr Beatriz Almeida", "Practicum Supervisor", "Practicum supervisor", "2 years", "9 June 2026",
                "b.almeida@curitibateach.example.org", "+61 7 5550 2418",
                ["I have supervised Sofia's classroom practice for two years. She plans carefully, listens to children and adapts when a lesson "
                 "is not working.", "She will be an excellent teacher."]),
         letter("Shelter Hope Children's Home", "Ms Carolina Duarte", "Director", "Volunteer supervisor", "2 years", "20 May 2026",
                "c.duarte@shelterhope.example.org", "+61 8 5550 3350",
                ["Sofia runs our homework hour with a gentle firmness that the children respect. She is on time every week and brings "
                 "books she has bought herself.", "I am proud to recommend her."],
                info={"Producer": "iLovePDF", "Creator": "Adobe Photoshop 25.0", "CreationDate": pdf_date("2026-10-02"),
                      "ModDate": pdf_date("2026-10-03")}),
         headshot(),
         resume(n, ["Bachelor of Education (Primary) preparation course, Curitiba Teaching College, February 2023 - present, full-time",
                    "Volunteer reader, Shelter Hope Children's Home, March 2024 - present, part-time"])],
        should=["document_integrity.created_after_dated", "document_integrity.editing_software"],
        allow=["document_integrity.modified_after_created"],
        should_not=["cross_document.*", "timeline.*", "narrative.*", "cross_application.*"],
        notes="The shelter's letter is dated 20 May 2026 but the PDF was made on 2 October 2026 with an image editor. Weak signals only."))

    # F07 to F09 ---------------------------------------------------------------- three applicants that share a referee phone, a domain and a template
    ring = [
        ("F07", "Dhruv Ashcombe", "dhruv.ashcombe@example.com", "0491 570 313", "2003-04-27", "Nepal", "Bachelor of Engineering",
         "16 Prayag Marg, Pokhara 33700, Nepal", "he", "his", "Mr Daniel Crossley", "d.crossley@riverbend-institute.example.org",
         "+61 7 5550 3344", "14 June 2026", {}),
        ("F08", "Yuki Pemberley", "yuki.pemberley@example.com", "0491 570 737", "2002-12-05", "Japan", "Bachelor of Science",
         "3-11 Sakura Dori, Sapporo 060-0001, Japan", "she", "her", "Dr Maya Lindqvist", "m.lindqvist@riverbend-institute.example.org",
         "(07) 5550 3344", "19 June 2026", {"adj1": "valued", "noun1": "persistence", "adj2": "thoughtful"}),
        ("F09", "Amara Locksley", "amara.locksley@example.com", "0491 570 313", "2001-10-19", "Kenya", "Master of Public Health",
         "88 Acacia Estate, Kisumu 40100, Kenya", "she", "her", "Prof Samuel Osei", "s.osei@riverbend-institute.example.org",
         "+61 7 5550 3344", "23 June 2026", {"adj1": "committed", "noun1": "enthusiasm", "adj2": "resourceful"}),
    ]
    bios = {
        "F07": "Dhruv grew up in Pokhara beside the lake and discovered engineering by repairing the bicycles and radios of his neighbours. He "
               "is studying at a technical campus, where he leads a small team that is building a low-cost water filter for mountain villages. "
               "Teachers describe him as practical, patient and unafraid to start again after a failed test. He volunteers on weekends with a "
               "trail-cleaning group, tutors younger students in mathematics and plays the madal at family festivals. In Darwin he hopes to "
               "complete his engineering degree, join a student sustainability society and later design small, affordable systems for "
               "communities that are far from the nearest town. He believes a simple machine that works every day is worth more than a "
               "clever one that sits in a cupboard, and he wants to prove it.",
        "F08": "Yuki lives in Sapporo, where winter lasts half the year and she has spent many of those months in a university laboratory studying "
               "snow crystals. She is a careful experimenter who keeps tidy notebooks and explains results with simple drawings. Beyond her studies "
               "she volunteers at a children's science museum, running Saturday workshops about weather, and she plays the cello in a community "
               "orchestra. Her professors say she is calm in front of a class and generous with her time. In Darwin she hopes to complete her "
               "science degree, learn how heat and water shape a very different climate and bring her love of explaining science to schools "
               "in remote places. She also hopes to taste a real tropical mango, which she has never seen outside a photograph.",
        "F09": "Amara comes from Kisumu on the shore of Lake Victoria and works in a community health programme that visits fishing villages. She "
               "trained as a nurse, noticed how often simple problems such as dirty water became serious illness, and decided to study public "
               "health. She leads a team of five health volunteers, keeps careful records of every visit and runs a small savings group for "
               "mothers. Colleagues describe her as determined, funny and tireless on long field days. She sings in her church choir and "
               "loves to cook ugali with sukuma wiki for visiting friends. In Darwin she hopes to complete a Master of Public Health, study "
               "how services reach remote communities and bring those lessons home to the lake.",
    }
    for code, nm, em, ph, dob, country, course, addr, pr, pr2, ref, ref_email, ref_phone, dated, swaps in ring:
        first = nm.split()[0]
        extras = {
            "F07": ("I study at a technical campus in Pokhara.", "I got a first-division result in my diploma examinations.",
                    "I lead a team of four students building a low-cost water filter.", "I clean trails with a volunteer group on weekends.",
                    "I want to use engineering to design affordable systems for remote communities in the Northern Territory."),
            "F08": ("I am a third-year science student in Sapporo.", "My snow-crystal project received the university's poster prize.",
                    "I coordinate the Saturday workshops at the science museum.", "I run weather workshops for children at the museum.",
                    "I want to learn how climate shapes life in the north of Australia and bring science to remote schools."),
            "F09": ("I work in a community health programme and study part of the week.", "I completed my nursing diploma with distinction.",
                    "I lead five health volunteers who visit fishing villages.", "I run a savings group for mothers in the villages I visit.",
                    "I want to study how health services reach remote communities and use it to improve care around Lake Victoria."),
        }[code]
        second = {
            "F07": letter("Pokhara Technical Campus", "Dr Sunita Gurung", "Senior Lecturer", "Project supervisor", "2 years", "2 July 2026",
                          "s.gurung@pokharatech.example.org", "+61 2 7010 4491",
                          ["Dhruv leads our water-filter project with patience and good judgement. He tests every idea twice and shares credit "
                           "with his team.", "I recommend him warmly."]),
            "F08": letter("Hokkaido Science Museum for Children", "Ms Haruka Tanabe", "Education Manager", "Volunteer supervisor", "2 years", "28 June 2026",
                          "h.tanabe@hokkaidosciencemuseum.example.org", "+61 3 7010 8830",
                          ["Yuki's Saturday weather workshops are the most popular programme we run. She is prepared, kind to nervous "
                           "children and always tidies up afterwards.", "I recommend her gladly."]),
            "F09": letter("Lake Victoria Community Health Programme", "Mrs Grace Atieno", "Programme Lead", "Direct supervisor", "4 years", "30 June 2026",
                          "g.atieno@lakevictoriahealth.example.org", "+61 8 7010 2257",
                          ["Amara has worked on my team for four years. Her village records are the most complete we have, and mothers trust "
                           "her because she listens.", "She will be a great public health leader."]),
        }[code]
        out.append(Scenario(
            code, f"One of three applicants sharing a referee phone, an email domain and a letter template ({code})", nm, em,
            form(nm, dob, em, ph, country, addr, country, course, STD_START, ARRIVE_ISO),
            {"current_study": extras[0], "academic_achievements": extras[1], "leadership": extras[2], "community_engagement": extras[3],
             "nt_contribution": extras[4], "biography": bios[code]},
            [coe(nm, course, "16 November 2026", "15 November 2028" if code == "F09" else "15 November 2029",
                 "2 years (4 semesters)" if code == "F09" else "3 years (6 semesters)", "14 September 2026",
                 {"F07": "E3307571", "F08": "E3308572", "F09": "E3309573"}[code]),
             offer(nm, course, "11 August 2026"), visa(nm.split()[1], first, "28 September 2026"),
             booking(nm, {"F07": "Kathmandu", "F08": "Tokyo", "F09": "Nairobi"}[code], {"F07": "KTM", "F08": "NRT", "F09": "NBO"}[code],
                     "1 November 2026", "2 November 2026", {"F07": "DA6B1R", "F08": "YP8M4S", "F09": "AL3V7T"}[code]),
             ring_letter(first, ref, ref_email, ref_phone, dated, swaps, pr, pr2), second, headshot()],
            should=["cross_application.shared_referee_phone", "cross_application.shared_referee_email_domain",
                    "cross_application.reused_wording"] + (["cross_application.shared_contact_phone"] if code in ("F07", "F09") else []),
            allow=["cross_application.identical_text"],
            should_not=["cross_document.*", "timeline.*", "narrative.*", "document_integrity.*"],
            notes="Each application looks unrelated. Together: the same referee phone number (written two ways), one email domain, one "
                  "reused letter template with a few words changed" + (", and one shared contact phone number (F07 and F09)." if code in ("F07", "F09") else ".")))

    # F10 ---------------------------------------------------------------- an innocent control
    n = "Ngoc Anh Fenwick"
    out.append(Scenario(
        "F10", "Control: a scanned and re-saved letter and a late arrival with a good reason", n, "ngocanh.fenwick@example.com",
        form(n, "2004-01-30", "ngocanh.fenwick@example.com", "0491 571 266", "Vietnam", "6 Orchid Street, Can Tho 94000, Vietnam", "Vietnam",
             "Bachelor of Nursing", STD_START, "2026-11-22",
             arrival_note="My student visa was granted later than planned. My provider agreed that I may arrive in the first week."),
        {"current_study": "I am in my final semester of upper secondary school in Can Tho.",
         "academic_achievements": "I received the school prize for the best results in biology and English this year.",
         "leadership": "I organise a monthly blood-donation morning at my school with the help of twelve classmates.",
         "community_engagement": "I read to patients at the children's ward of the provincial hospital on Sunday mornings.",
         "nt_contribution": "I want to be a nurse in the Northern Territory, where I can care for people in small communities and learn from "
                            "Aboriginal health workers about caring well for people on their own land.",
         "biography": "Ngoc Anh lives in Can Tho on the Mekong delta, where her family sells fruit at a floating market. She decided to be a "
                      "nurse after spending a month with her cousin in hospital and seeing how much the night nurses mattered. At school she "
                      "is first in biology, helps younger students with English and organises a monthly blood-donation morning. On Sundays she "
                      "reads picture books to children in the paediatric ward, and some of them now wait for her at the door. Teachers say she "
                      "is gentle, reliable and difficult to rattle. She enjoys swimming, painting lanterns for the autumn festival and making "
                      "banh xeo for friends. After the scholarship she hopes to finish a nursing degree in Darwin, learn from remote-area "
                      "nurses and one day work where people most need a patient, friendly nurse."},
        [coe(n, "Bachelor of Nursing", "16 November 2026", "15 November 2029", "3 years (6 semesters)", "14 September 2026", "E1192640"),
         offer(n, "Bachelor of Nursing", "10 August 2026"), visa("Fenwick", "Ngoc Anh", "5 October 2026"),
         booking(n, "Ho Chi Minh City", "SGN", "21 November 2026", "22 November 2026", "NF6Z3K"),
         letter("Can Tho Provincial Hospital Volunteer Office", "Dr Le Thanh Huong", "Paediatric Ward Doctor", "Volunteer supervisor", "2 years",
                "15 June 2026", "l.huong@canthohospital.example.org", "+61 2 5550 7761",
                ["Ngoc Anh has read to our paediatric patients every Sunday for two years. The children are calmer after her visits and the "
                 "nurses ask her to stay longer.", "I recommend her as a future nurse."],
                info={"Producer": "EPSON Scan 2", "Creator": "EPSON Scan", "CreationDate": pdf_date("2026-09-30"), "ModDate": pdf_date("2026-10-01")}),
         letter("Chau Van Liem High School", "Mrs Nguyen Thi Mai", "Biology Teacher", "Class teacher", "3 years", "4 June 2026",
                "n.mai@chauvanliem.example.org", "+61 3 5550 6048",
                ["I have taught Ngoc Anh for three years. She works steadily, helps classmates and organised the school's first blood-donation "
                 "morning without being asked.", "She has my full support."],
                info={"Producer": "Microsoft: Print To PDF", "Creator": "Microsoft Word", "CreationDate": pdf_date("2026-09-28"), "ModDate": pdf_date("2026-09-28")}),
         headshot(),
         resume(n, ["Upper secondary school (science stream), Chau Van Liem High School, September 2022 - present, full-time",
                    "Volunteer reader, Can Tho Provincial Hospital children's ward, June 2024 - present, part-time"])],
        should=[],
        allow=["document_integrity.created_after_dated", "cross_document.arrival_vs_start", "document_integrity.modified_after_created"],
        should_not=["cross_application.*", "timeline.*", "narrative.*"],
        control=True,
        notes="Innocent: a scanned letter (scanner software, made months after its date), a re-saved PDF and an arrival six days after the "
              "course starts, explained in the form. May raise weak signals; must raise no strong flag."))
    return out


# ---------------------------------------------------------------------------- document cases (DA to DD)
# Four applications for the officer's Documents table: one clean control and three with a document that needs a look.
# They reuse the builders above. Every document ends with a visible "SAMPLE: FICTIONAL TEST DOCUMENT" footer.
# These are not consistency-check scenarios, so `build()` and the consistency evaluation do not include them.

TEST_FOOTER = "SAMPLE: FICTIONAL TEST DOCUMENT"


def footed(doc: Doc) -> Doc:
    """The same document with the visible test footer at the bottom (and no header line)."""
    return Doc(doc.kind, [l for l in doc.lines if l != FOOTER] + ["", TEST_FOOTER], doc.info)


def passport(family: str, given: str, number: str, dob: str, expiry: str, nationality: str) -> Doc:
    return Doc("passport", [FOOTER, "Passport", f"Surname: {family}", f"Given names: {given}", f"Nationality: {nationality}",
                            f"Date of birth: {dob}", f"Passport number: {number}", "Date of issue: 12 March 2022", f"Date of expiry: {expiry}"])


def _doc_case(code: str, title: str, name: str, family: str, given: str, email: str, phone: str, dob_iso: str, dob_text: str, country: str,
              town: str, passport_number: str, passport_expiry: str, notes: str, *, coe_doc: Doc | None, booking_doc: Doc | None,
              letters: tuple[list[str], list[str]], ref_phones: tuple[str, str], story: dict, extra: dict[str, str] | None = None) -> Scenario:
    """Each case has its own referee contacts and letter wording, so the cases do not look linked to each other."""
    domain = town.lower().replace(" ", "")
    docs = [coe_doc, passport(family.upper(), given, passport_number, dob_text, passport_expiry, country), booking_doc,
            visa(family, given, "28 September 2026"),
            letter(f"{town} Secondary College", f"Ms {family[:3]}ana Reyes", "Class Teacher", "Class teacher", "3 years", "9 June 2026",
                   f"teacher@{domain}-college.example.org", ref_phones[0], letters[0]),
            letter(f"{town} Community Health Centre", f"Mr {family[:3]}on Patel", "Volunteer Coordinator", "Volunteer supervisor", "2 years", "20 June 2026",
                   f"volunteers@{domain}-health.example.org", ref_phones[1], letters[1]),
            headshot()]
    fields = form(name, dob_iso, email, phone, country, f"12 Jacaranda Lane, {town}, {country}", country, "Bachelor of Nursing", STD_START,
                  ARRIVE_ISO, passport_number=passport_number, **(extra or {}))
    answers = {k: v for k, v in story.items() if k != "bio"} | {"biography": " ".join(story["bio"])}
    return Scenario(code, title, name, email, fields, answers, [footed(d) for d in docs if d is not None], should=[], notes=notes)


def build_documents() -> list[Scenario]:
    """DA control, DB CoE missing, DC passport expires soon, DD CoE differs from the form and a document is in the wrong slot."""
    out: list[Scenario] = []
    good_booking = lambda n, ref: booking(n, "Hanoi", "HAN", STD_ARRIVE_DEPART, STD_ARRIVE, ref)  # noqa: E731

    n = "Soraya Pemberton"
    out.append(_doc_case(
        "DA", "Control: every document is right and in its slot", n, "Pemberton", "Soraya", "soraya.pemberton@example.com", "0491 571 600",
        "2003-05-09", "9 May 2003", "Vietnam", "Hue", "P1200451", "14 June 2031",
        "Every document matches the form. The Documents table should show nothing that needs attention.",
        coe_doc=coe(n, "Bachelor of Nursing", "16 November 2026", "15 November 2029", "3 years (6 semesters)", "14 September 2026", "E7101001"),
        booking_doc=good_booking(n, "SP1A2B"),
        letters=(["Soraya joined my biology class three years ago and quickly became the student others ask for help before an exam.",
                  "She keeps tidy lab notes, shares them freely and never leaves a practical unfinished. I recommend her."],
                 ["Soraya has greeted patients at our Saturday clinic for two years. Older visitors ask for her by name.",
                  "She is punctual, kind and very good in a crowded waiting room. I recommend her warmly."]),
        ref_phones=("+61 8 5550 3301", "+61 3 5550 4412"),
        story={"current_study": "I am in my final year at a secondary college in Hue and sit my last exams in November.",
               "academic_achievements": "I received the school prize for biology and a distinction in chemistry this year.",
               "leadership": "I lead a first-aid club of nine students and run a practice evening every month.",
               "community_engagement": "Each Saturday I greet patients at a community health centre and help older visitors find their way.",
               "nt_contribution": "I want to nurse in remote Northern Territory clinics, using my first-aid training and clinic volunteering to help families who live far from a hospital.",
               "bio": ["Soraya grew up in Hue beside the Perfume River, where her mother runs a small tea stall and her father repairs boats.",
                       "A long stay in hospital at age eight made her decide to become a nurse, and she has studied biology and chemistry with that goal ever since.",
                       "Her teachers describe a patient listener who explains hard ideas to classmates and finishes every practical she starts.",
                       "On Saturdays she volunteers at a community health centre, greeting patients, translating for older visitors and keeping the waiting room calm.",
                       "She plays badminton, cooks for her family on Sundays and walks by the river at sunset to clear her head before exams.",
                       "In Darwin she plans to complete a nursing degree, join the student health society and later work in a remote clinic, bringing the same patience and care she learned at home."]}))

    n = "Ilari Whitcombe"
    out.append(_doc_case(
        "DB", "The Confirmation of Enrolment was not uploaded", n, "Whitcombe", "Ilari", "ilari.whitcombe@example.com", "0491 571 601",
        "2002-11-21", "21 November 2002", "India", "Kochi", "P2230917", "2 February 2032",
        "No CoE. The Documents table should show the CoE slot as missing, with a plain reason.",
        coe_doc=None, booking_doc=good_booking(n, "IW3C4D"),
        letters=(["I have been Ilari's chemistry teacher since 2023. His practical reports are clear and he explains results to the whole group.",
                  "He volunteers to set up the lab before class. I am pleased to support this application."],
                 ["Ilari helps run our weekend first-aid refreshers, preparing the kits and demonstrating bandaging to new volunteers.",
                  "He is calm, reliable and easy to work with. I support his application."]),
        ref_phones=("+61 7 5550 6128", "+61 2 5550 7340"),
        story={"current_study": "I am completing the last term of senior school in Kochi and take my board exams in March.",
               "academic_achievements": "I finished the year with the top results in chemistry and won the district science fair.",
               "leadership": "I coordinate the weekend first-aid refresher for new volunteers at my local club.",
               "community_engagement": "I help a neighbourhood clinic restock supplies and read to children waiting for their check-ups.",
               "nt_contribution": "A nursing degree in Darwin will let me work in the Territory's smaller towns, where I can use my clinic experience to support patients who travel long distances for care.",
               "bio": ["Ilari lives in Kochi with his grandmother and two younger sisters, near the harbour where his uncle works as a port clerk.",
                       "He became interested in nursing when the family clinic saved his sister during a fever, and he has volunteered there ever since.",
                       "In class he is known for tidy chemistry notes that he copies for anyone who missed a lesson.",
                       "Weekends are spent running first-aid refreshers, packing supply kits and reading picture books to children in the waiting area.",
                       "He enjoys cricket, sketching boats from the jetty and cooking fish curry for the family on holidays.",
                       "His plan is to study nursing in Darwin, join a student volunteer group and one day work in a country clinic, bringing the steady care he saw at home."]}))

    n = "Anh Thu Marlowe"
    out.append(_doc_case(
        "DC", "The passport expires within six months of the course start", n, "Marlowe", "Anh Thu", "anhthu.marlowe@example.com", "0491 571 602",
        "2004-03-17", "17 March 2004", "Vietnam", "Da Nang", "P3345128", "10 March 2027",
        "Course starts 16 November 2026; the passport expires 10 March 2027 (under four months later).",
        coe_doc=coe(n, "Bachelor of Nursing", "16 November 2026", "15 November 2029", "3 years (6 semesters)", "14 September 2026", "E7101003"),
        booking_doc=good_booking(n, "AM5E6F"),
        letters=(["Anh Thu has been in my English and science classes for three years. She asks careful questions and writes thoughtful lab summaries.",
                  "She mentors the younger students at lunchtime. I am glad to recommend her."],
                 ["For two years Anh Thu has helped at our health centre on Saturdays, welcoming families and keeping the children's corner tidy.",
                  "Visitors comment on her gentle manner. I recommend her to any nursing programme."]),
        ref_phones=("+61 8 5550 8216", "+61 3 5550 9124"),
        story={"current_study": "I am in my final semester at a high school in Da Nang and will graduate in December.",
               "academic_achievements": "I achieved the top results in my year for English and received the regional award for science writing.",
               "leadership": "I mentor ten younger students at lunchtime and run the school's science reading corner.",
               "community_engagement": "I help at a health centre on Saturdays, welcoming families and keeping the children's corner tidy.",
               "nt_contribution": "Studying nursing in the Northern Territory will prepare me to work with communities far from city hospitals, where I can use my volunteering and language skills to help patients feel at ease.",
               "bio": ["Anh Thu was born in Da Nang, where her parents run a bakery that opens before sunrise.",
                       "She has loved science since a teacher let her look at onion cells under a microscope, and she now wants a career caring for people.",
                       "Classmates say she asks careful questions and writes clear summaries that others borrow before tests.",
                       "On Saturdays she welcomes families at a health centre and arranges the toys in the children's corner so the wait feels shorter.",
                       "She likes table tennis, folding paper cranes and trying new bread recipes with her father.",
                       "In Darwin she hopes to finish a nursing degree, make friends through a student society and return to clinic work in a regional town."]}))

    n = "Imelda Fairweather"
    wrong_slot = Doc("travel booking", offer(n, "Bachelor of Nursing", "10 August 2026").lines)  # a letter of offer uploaded as the arrival evidence
    out.append(_doc_case(
        "DD", "The CoE differs from the form, and a letter of offer is in the arrival slot", n, "Fairweather", "Imelda", "imelda.fairweather@example.com", "0491 571 603",
        "2003-08-30", "30 August 2003", "Philippines", "Cebu", "P4456239", "5 July 2031",
        "The CoE names a different student and a different start date (30 November 2026). The arrival slot holds a letter of offer, not a booking.",
        coe_doc=coe("Imelda Fairchild", "Bachelor of Nursing", "30 November 2026", "29 November 2029", "3 years (6 semesters)", "14 September 2026", "E7101004"),
        booking_doc=wrong_slot,
        letters=(["I taught Imelda mathematics and biology for three years. She finishes extra problems before they are set and shares her methods.",
                  "She leads our first-aid club with patience. She has my full support."],
                 ["Imelda assists at our community clinic every Saturday, setting out information leaflets and walking elderly patients to their appointments.",
                  "She is cheerful under pressure. I am happy to endorse her application."]),
        ref_phones=("+61 2 5550 1187", "+61 7 5550 2263"),
        story={"current_study": "I am studying in the final year of senior high school in Cebu and graduate in October.",
               "academic_achievements": "I earned the top mathematics results in my school and a school prize in biology.",
               "leadership": "I head a first-aid club of twelve students and organise a yearly bandaging workshop for younger classes.",
               "community_engagement": "On Saturdays I help at a community clinic, setting out leaflets and walking elderly patients to their appointments.",
               "nt_contribution": "I would like to train as a nurse in Darwin and then work in a remote Territory clinic, where my first-aid leadership and clinic experience can support families with limited access to care.",
               "bio": ["Imelda was born in Cebu, where her father drives a jeepney and her mother sews school uniforms for the neighbourhood.",
                       "She chose nursing after watching a nurse calm her grandfather during a night in hospital, and she studies biology and mathematics with that in mind.",
                       "Her teachers say she finishes extra problems before they are set and happily explains her method to anyone who asks.",
                       "On Saturdays she volunteers at a community clinic, handing out leaflets and walking older patients to their appointments.",
                       "She enjoys volleyball, singing in the church choir and baking pandesal with her cousins.",
                       "Her plan is to complete a nursing degree in Darwin, join a student health group and work in a remote clinic where patient care matters most."]}))
    return out
