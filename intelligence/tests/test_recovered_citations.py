from __future__ import annotations

from typing import Any, Optional

from intelligence.contract import (
    Claim,
    Department,
    Entity,
    EventStatus,
    Generation,
    Incident,
    IncidentCategory,
    IssueCode,
    Location,
    PriorityLevel,
    Provenance,
    RecordKind,
    ReviewState,
    SCHEMA_VERSION,
    Validation,
)
from intelligence.context import build_context
from intelligence.intelligence import extract_incident
from intelligence.models.enums import (
    EventType,
    ExtractionMethod,
    ResolutionState,
    SeverityLevel,
    TimePrecision,
)
from intelligence.models.evidence import Evidence
from intelligence.pipeline import describe_incident
from intelligence.spans import compute_field_hash
from intelligence.tests.builders import CONTENT, Scripted
from intelligence.tests.record_fixtures import RETRIEVED_AT, base_record

TITLE = "தனக்குத்தானே கட்டு போடும் நோயாளிகள்: அரசு சுகாதார நிலையத்தில் அவலம்"
BODY = (
    "தேனி: அரசு மருத்துவமனையில் மாதக்கணக்காக டாக்டர் இல்லை. திருவாதவூர் ஆரம்ப சுகாதார "
    "நிலையத்தில் டாக்டர், செவிலியர்கள் பற்றாக்குறையால் நோயாளிகள் அவதிப்படுகின்றனர். "
    "200க்கும் மேற்பட்ட கர்ப்பிணிகளும் சிகிச்சைக்கு வருகின்றனர். மாவட்ட நிர்வாகம் சித்த மருத்துவர், "
    "டாக்டர் மற்றும் செவிலியர் காலி பணியிடங்களை நிரப்ப வேண்டும் என்றார்."
)

AT_THE_PHC = "ஆரம்ப சுகாதார நிலையத்தில்"
SUFFER = "அவதிப்படுகின்றனர்"
PATIENTS_SUFFER = "நோயாளிகள் அவதிப்படுகின்றனர்"
PUBLISHED = "அரசு மருத்துவமனையில்"
VILLAGE_AT_PHC = "திருவாதவூர் ஆரம்ப சுகாதார நிலையத்தில்"
APPEAL = "மாவட்ட நிர்வாகம் சித்த மருத்துவர், டாக்டர் மற்றும் செவிலியர் காலி பணியிடங்களை நிரப்ப வேண்டும் என்றார்"
URGENCY = "200க்கும் மேற்பட்ட கர்ப்பிணிகளும் சிகிச்சைக்கு வருகின்றனர்"

GAP_PARAPHRASE = "டாக்டர், செவிலியர்கள் பற்றாக்குறையால் சிகிச்சை பெறுபவர்கள் அவதிப்படுகின்றனர்"
GAP_COVER = "டாக்டர், செவிலியர்கள் பற்றாக்குறையால் நோயாளிகள் அவதிப்படுகின்றனர்"
APPEAL_WITH_ELLIPSIS = "மாவட்ட நிர்வாகம் ... காலி பணியிடங்களை நிரப்ப வேண்டும் என்றார்"

GAP_INVENTED = "டாக்டர், செவிலியர்கள் பற்றாக்குறையால் சிகிச்சை பெறுபவர்கள் உயிரிழந்தனர்"
APPEAL_INVENTED = (
    "மாவட்ட நிர்வாகம் ... உடனடி நடவடிக்கை எடுக்குமாறு மக்கள் கோரிக்கை விடுத்தனர்"
)
PRIORITY_INVENTED = "உடனடி நடவடிக்கை தேவை"

CITATION_HEAD = " ".join(GAP_PARAPHRASE.split(" ")[:3])
CITATION_TAIL = " ".join(GAP_PARAPHRASE.split(" ")[3:])
BETWEEN = (
    "வாரம் முழுவதும் இந்நிலை தொடர்ந்தது. அருகிலுள்ள ஊர்களிலிருந்தும் மக்கள் வருகின்றனர். "
    "பலரும் மணி நேரம் காத்திருக்க வேண்டிய அவசியம் ஏற்பட்டது."
)
LOOSE_SENTENCE = "மதுரையின் அரசு மருத்துவமனையில் " + CITATION_HEAD + " இளையவரகள் காலை நோயாளிகள் " + SUFFER + "."
TIGHT_SENTENCE = "கவுண்டரில் " + CITATION_HEAD + " இளையவரகள மாலை " + CITATION_TAIL + " என்றார்."
TWINS_SENTENCE = "சேதையூரில் " + CITATION_HEAD + " இளையவரகள மாலை நோயாளிகள் " + SUFFER + " என்றார்."
LOOSE_COVER = CITATION_HEAD + " இளையவரகள் காலை நோயாளிகள் " + SUFFER
TIGHT_COVER = CITATION_HEAD + " இளையவரகள மாலை " + CITATION_TAIL
TWINS_COVER = CITATION_HEAD + " இளையவரகள மாலை நோயாளிகள் " + SUFFER
TIGHT_BODY = LOOSE_SENTENCE + " " + BETWEEN + " " + TIGHT_SENTENCE
TWINS_BODY = LOOSE_SENTENCE + " " + BETWEEN + " " + TWINS_SENTENCE


def article(**fields: Any) -> dict[str, Any]:
    url = "https://example.test/self-control-patients-carry-their-own-dressings"
    payload = base_record(
        record_id="NEWS-MDU-PHC-LIVE-0001",
        title=TITLE,
        event_time="2026-10-10T16:16:00",
        data={"content": BODY, "language": "ta"},
        source_url=url,
        raw_reference=url,
        retrieved_at=RETRIEVED_AT,
    )
    payload.update(fields)
    return payload


def in_field(field: str, text: str, *, value: Optional[str] = None) -> dict[str, Any]:
    source = TITLE if field == "title" else BODY
    return {
        "value": value if value is not None else text,
        "field": field,
        "quote": text,
        "char_start": source.index(text),
        "confidence": 0.85,
    }


def cited(text: str, value: str) -> dict[str, Any]:
    return {"value": value, "field": CONTENT, "quote": text, "char_start": 0, "confidence": 0.75}


def answer(**changes: Any) -> dict[str, Any]:
    payload = {
        "record_kind": "incident",
        "title": in_field("title", TITLE),
        "description": in_field(CONTENT, PATIENTS_SUFFER),
        "incident_type": cited(GAP_PARAPHRASE, "hospital_service_gap"),
        "category": in_field(CONTENT, AT_THE_PHC, value="health"),
        "department": in_field(CONTENT, AT_THE_PHC, value="public_health"),
        "severity": in_field(CONTENT, SUFFER, value="moderate"),
        "priority": in_field(CONTENT, URGENCY, value="high"),
        "event_status": cited(APPEAL_WITH_ELLIPSIS, "reported"),
        "locations": [
            {"text": "திருவாதவூர்", "normalized_name": "Thiruvadhavur", "location_type": "town",
             "role": "event_location", "district": "Madurai", "field": CONTENT,
             "quote": VILLAGE_AT_PHC, "char_start": BODY.index(VILLAGE_AT_PHC),
             "confidence": 0.8},
        ],
    }
    return payload | changes


def read(payload: Any, record: Optional[dict[str, Any]] = None) -> Incident:
    return extract_incident(record if record is not None else article(),
                            provider=Scripted(payload=payload))


def holders(record: Optional[dict[str, Any]] = None) -> dict[str, str]:
    return {item.field: item.text for item in build_context(record or article()).fields}


def recovered(incident: Incident, name: str) -> tuple[Claim, Evidence]:
    claim = incident.claim(name)
    assert claim is not None, name
    return claim, incident.evidence_by_id()[claim.evidence_ids[0]]


def reading(citation: str, body: str) -> Incident:
    return read(
        {
            "record_kind": "incident",
            "title": in_field("title", TITLE),
            "incident_type": cited(citation, "hospital_service_gap"),
        },
        article(data={"content": body, "language": "ta"}),
    )


def test_a_paraphrase_of_the_shortage_is_anchored_on_the_sentence_its_own_two_runs_bracket():
    found = read(answer(event_status=in_field(CONTENT, APPEAL, value="reported")))

    assert found.incident_type is EventType.HOSPITAL_SERVICE_GAP
    assert found.validation.issues == []
    assert found.validation.state is ReviewState.ACCEPTED

    claim, evidence = recovered(found, "incident_type")
    assert claim.review is ReviewState.ACCEPTED
    assert (evidence.field, evidence.quote) == (CONTENT, GAP_COVER)
    assert evidence.char_start == BODY.index(GAP_COVER)
    assert BODY[evidence.char_start : evidence.char_end] == evidence.quote
    assert evidence.field_text_hash == compute_field_hash(BODY)
    assert "the answer's own opening and closing runs bracket" in (claim.notes or "")

    said = " ".join(found.generation.warnings)
    assert "is not the record's own words but its opening and closing runs are" in said
    assert "what the answer changed between them is dropped" in said
    assert GAP_PARAPHRASE not in [item.quote for item in found.evidence]


def test_the_ellipsis_that_compressed_the_appeal_is_anchored_on_the_whole_sentence():
    found = read(answer(incident_type=in_field(CONTENT, PUBLISHED, value="hospital_service_gap")))

    assert found.event_status is EventStatus.REPORTED
    assert found.validation.state is ReviewState.ACCEPTED

    claim, evidence = recovered(found, "event_status")
    assert claim.review is ReviewState.ACCEPTED
    assert (evidence.field, evidence.quote) == (CONTENT, APPEAL)
    assert BODY[evidence.char_start : evidence.char_end] == APPEAL
    assert claim.evidence_ids[0] == evidence.evidence_id

    said = " ".join(found.generation.warnings)
    assert APPEAL_WITH_ELLIPSIS[:40] in said
    assert "the evidence was anchored on the one stretch of" in said


def test_the_live_account_is_now_whole_with_its_own_priority_and_its_tamil_place():
    found = read(answer())

    assert found.validation.issues == []
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.review_required is False
    assert found.record_kind is RecordKind.INCIDENT
    assert found.context_type is None
    assert found.incident_type is EventType.HOSPITAL_SERVICE_GAP
    assert found.category is IncidentCategory.HEALTH
    assert found.department is Department.PUBLIC_HEALTH
    assert found.severity is SeverityLevel.MODERATE
    assert found.priority is PriorityLevel.HIGH
    assert found.event_status is EventStatus.REPORTED
    assert found.event_time is None
    assert found.event_time_precision is TimePrecision.UNKNOWN
    assert found.provenance.declared_event_time.isoformat() == "2026-10-10T16:16:00"

    assert [item.text for item in found.locations] == ["திருவாதவூர்"]
    place = found.locations[0]
    assert place.review is ReviewState.ACCEPTED
    assert place.resolution_state is ResolutionState.PENDING_GIS
    assert place.canonical_location_id is None
    assert (place.latitude, place.longitude) == (None, None)

    gap = recovered(found, "incident_type")[1]
    status = recovered(found, "event_status")[1]
    severity = recovered(found, "severity")[1]
    priority = recovered(found, "priority")[1]
    assert (severity.quote, priority.quote) == (SUFFER, URGENCY)
    assert len({item.evidence_id for item in (gap, status, severity, priority)}) == 4

    line = describe_incident(found)
    assert "type=hospital_service_gap" in line
    assert "priority=high" in line
    assert "status=reported" in line
    assert "event_time=-" in line
    assert "places=திருவாதவூர்" in line
    assert "review=accepted(0)" in line


def test_a_citation_whose_second_run_the_record_never_wrote_is_still_refused():
    found = read(answer(incident_type=cited(GAP_INVENTED, "hospital_service_gap"),
                        event_status=cited(APPEAL_INVENTED, "reported")))

    assert found.incident_type is EventType.UNRESOLVED
    assert found.event_status is EventStatus.UNKNOWN
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    codes = {(item.field, item.code) for item in found.validation.issues}
    assert (("incident_type", IssueCode.UNSUPPORTED_CLAIM) in codes
            and ("event_status", IssueCode.UNSUPPORTED_CLAIM) in codes)
    for name in ("incident_type", "event_status"):
        detail = next(item.detail for item in found.validation.issues if item.field == name)
        assert "verbatim in no field this record carries" in detail
    assert all(item.quote not in (GAP_INVENTED, APPEAL_INVENTED, GAP_COVER, APPEAL)
               for item in found.evidence)
    assert found.category is IncidentCategory.HEALTH
    assert found.severity is SeverityLevel.MODERATE


def test_the_bracket_recovery_is_never_offered_to_a_claim_about_what_happened():
    found = read({
        "record_kind": "incident",
        "title": in_field("title", TITLE),
        "description": cited(GAP_PARAPHRASE, "the record's own account"),
    })

    assert found.description is None
    finding = next(item for item in found.validation.issues if item.field == "description")
    assert finding.code is IssueCode.UNSUPPORTED_CLAIM
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert GAP_PARAPHRASE in finding.detail
    assert "opening and closing runs" not in " ".join(found.generation.warnings)


def test_a_priority_the_record_neither_states_or_brackets_is_left_out_of_the_reading():
    found = read(answer(priority=cited(PRIORITY_INVENTED, "high")))

    assert found.priority is None
    assert found.claim("priority") is None
    assert not [item for item in found.validation.issues if item.field == "priority"]
    assert found.severity is SeverityLevel.MODERATE
    assert found.validation.state is ReviewState.ACCEPTED

    said = " ".join(found.generation.warnings)
    assert "the answer offered priority='high'" in said
    assert "instead of carrying a claim for it" in said


def test_a_reading_that_named_no_place_says_so_and_keeps_the_declared_places_apart():
    found = read(answer(locations=[]))

    assert found.locations == []
    assert [(item.field, item.value) for item in found.source_geography] == [
        ("location.raw_text", "Madurai"),
        ("location.district", "Madurai"),
        ("location.state", "Tamil Nadu"),
    ]
    assert found.validation.state is ReviewState.ACCEPTED

    said = " ".join(found.generation.warnings)
    assert "the reader named no place so locations is empty" in said
    assert "a feed's own district tag is not a place the account evidenced" in said

    line = describe_incident(found)
    assert "places=none in the account (declared: Madurai;Tamil Nadu)" in line


def test_the_recovered_reading_adds_no_key_and_every_citation_replays_byte_for_byte():
    found = read(answer(entities=[
        {"text": "மாவட்ட நிர்வாகம்", "entity_type": "government_body",
         "role": "decision_maker", "field": CONTENT, "quote": "மாவட்ட நிர்வாகம்",
         "char_start": BODY.index("மாவட்ட நிர்வாகம்"), "confidence": 0.75},
    ]))
    doc = found.to_storage_document()

    assert doc["schema_version"] == SCHEMA_VERSION
    assert set(doc) <= set(Incident.model_fields)
    for name, model in (
        ("provenance", Provenance),
        ("validation", Validation),
        ("generation", Generation),
    ):
        assert set(doc[name]) <= set(model.model_fields), f"{name} grew a key"
    for name, model in (
        ("claims", Claim),
        ("locations", Location),
        ("entities", Entity),
        ("evidence", Evidence),
    ):
        assert doc[name], name
        for item in doc[name]:
            assert set(item) <= set(model.model_fields), f"{name} grew a key"

    fields = holders()
    for item in found.evidence:
        if item.method is ExtractionMethod.SOURCE_METADATA:
            continue
        text = fields[item.field]
        assert text[item.char_start : item.char_end] == item.quote
        assert item.field_text_hash == compute_field_hash(text)
    assert set(doc["locations"][0]) == set(Location.model_fields)
    assert doc["locations"][0]["canonical_location_id"] is None


def test_of_two_brackets_that_both_fit_a_citation_the_one_holding_more_of_it_is_anchored_on():
    found = reading(GAP_PARAPHRASE, TIGHT_BODY)

    assert found.incident_type is EventType.HOSPITAL_SERVICE_GAP
    claim, evidence = recovered(found, "incident_type")
    assert claim.review is ReviewState.ACCEPTED
    assert (evidence.field, evidence.quote) == (CONTENT, TIGHT_COVER)
    assert evidence.char_start == TIGHT_BODY.index(TIGHT_COVER)
    assert TIGHT_BODY[evidence.char_start : evidence.char_end] == evidence.quote
    assert evidence.field_text_hash == compute_field_hash(TIGHT_BODY)
    assert LOOSE_COVER not in [item.quote for item in found.evidence]

    said = " ".join(found.generation.warnings)
    assert "opening and closing runs are" in said
    assert "what the answer changed between them is dropped" in said
    assert TIGHT_COVER in [item.quote for item in found.evidence]


def test_two_brackets_holding_a_citation_with_equal_weight_leave_the_value_refused():
    found = reading(GAP_PARAPHRASE, TWINS_BODY)

    assert found.incident_type is EventType.UNRESOLVED
    finding = next(item for item in found.validation.issues if item.field == "incident_type")
    assert finding.code is IssueCode.AMBIGUOUS_QUOTE
    assert "same weight" in finding.detail
    for cover in (LOOSE_COVER, TWINS_COVER):
        start = TWINS_BODY.index(cover)
        assert f"{CONTENT} {start}:{start + len(cover)}" in finding.detail
    assert [item.quote for item in found.evidence if item.field == CONTENT] == []
    assert "the evidence was anchored" not in " ".join(found.generation.warnings)
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
