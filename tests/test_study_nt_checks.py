from app.pipeline.code_checks import CodeContext
from app.pipeline.documents import DocumentCheck, DocumentChecks
from app.pipeline.facts import FactSet
from app.pipeline.study_nt_checks import days_before


def test_s5_uses_verified_travel_booking_when_form_has_no_arrival_date():
    rule = type("Rule", (), {"id": "rule-s5", "rule_code": "S5", "params": {"fact": "arrival_date", "min_days": 14}})()
    documents = DocumentChecks(
        [
            DocumentCheck(
                document_id="booking-1",
                declared_type="travel_booking",
                detected_type="travel_booking",
                type_matches=True,
                extracted_fields={"arrival_date_iso": "2027-02-13"},
            )
        ]
    )
    facts = FactSet()
    context = CodeContext(
        rule=rule,
        facts=facts,
        documents=documents,
        application={"submitted_at": "2026-10-12T00:00:00+00:00"},
    )

    finding = days_before(context)

    assert finding.ai_status == "Met"
    assert "from the travel booking" in finding.rationale
