from __future__ import annotations

from typing import Any, Optional

from intelligence.contract import (
    Claim,
    ContextType,
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
    ResolutionState,
    SeverityLevel,
    TimePrecision,
)
from intelligence.models.evidence import Evidence
from intelligence.pipeline import process_record
from intelligence.spans import compute_field_hash
from intelligence.structured import MAPPING_VERSION
from intelligence.tests.builders import CONTENT, NeverCalled, Scripted, quote, water_answer
from intelligence.tests.record_fixtures import (
    RETRIEVED_AT,
    base_record,
    market_price,
    weather_forecast,
    waterlogging,
)

PHC_TITLE = "அரசு மருத்துவமனையில் டாக்டர், செவிலியர் பணியிடங்கள் காலி: நோயாளிகள் அவதி"
PHC_CONTENT = (
    "மதுரை அருகே உள்ள திருவாதவூர் ஆரம்ப சுகாதார நிலையத்தில் டாக்டர் மற்றும் செவிலியர் "
    "பணியிடங்கள் நீண்ட காலமாக காலியாக உள்ளன. இதனால் சிகிச்சைக்கு வரும் நோயாளிகள் "
    "அவதியுறுகின்றனர். காலி பணியிடங்களை நிரப்ப மாவட்ட நிர்வாகத்திற்கு கோரிக்கை "
    "விடுக்கப்பட்டுள்ளது. பணியிடங்களை நிரப்பினால் அப்புறம் நோயாளிகள் நேரத்தில் சிகிச்சை "
    "பெற முடியும்."
)

AT_THE_PHC = "ஆரம்ப சுகாதார நிலையத்தில்"
STAFF_OUT = "டாக்டர் மற்றும் செவிலியர் பணியிடங்கள் நீண்ட காலமாக காலியாக உள்ளன"
VACANT_FOR_YEARS = "நீண்ட காலமாக காலியாக உள்ளன"
PATIENTS_SUFFER = "அவதியுறுகின்றனர்"
WHO_COME_FOR_TREATMENT = "சிகிச்சைக்கு வரும் நோயாளிகள்"
HEADLINE_GAP = "டாக்டர், செவிலியர் பணியிடங்கள் காலி"
GOVERNMENT_HOSPITAL = "அரசு மருத்துவமனையில்"
REPEATED_WORD = "பணியிடங்களை"
DEATHS_NOT_STATED = "நோயாளிகள் உயிரிழந்தனர்"

HARD_SHIP = "பொதுமக்கள் சிரமத்திற்கு உள்ளாகினர்"
URGENCY_NOT_STATED = "உடனடி நடவடிக்கை தேவை"


def phc_record(**fields: Any) -> dict[str, Any]:
    url = "https://example.test/self-control-patients-woes-at-government-health-centre"
    payload = base_record(
        record_id="NEWS-MDU-PHC-0001",
        title=PHC_TITLE,
        event_time="2026-10-02T18:20:00",
        data={"content": PHC_CONTENT, "language": "ta"},
        source_url=url,
        raw_reference=url,
        retrieved_at=RETRIEVED_AT,
    )
    payload.update(fields)
    return payload


def in_field(field: str, text: str, *, value: Optional[str] = None) -> dict[str, Any]:
    source = PHC_TITLE if field == "title" else PHC_CONTENT
    return {
        "value": value if value is not None else text,
        "field": field,
        "quote": text,
        "char_start": source.index(text),
        "confidence": 0.85,
    }


def misnamed(field: str, text: str, value: str) -> dict[str, Any]:
    return {"value": value, "field": field, "quote": text, "char_start": 0, "confidence": 0.75}


def phc_answer(**changes: Any) -> dict[str, Any]:
    answer = {
        "record_kind": "incident",
        "title": in_field("title", PHC_TITLE),
        "description": in_field(CONTENT, WHO_COME_FOR_TREATMENT),
        "incident_type": misnamed(CONTENT, HEADLINE_GAP, "hospital_service_gap"),
        "category": misnamed(CONTENT, GOVERNMENT_HOSPITAL, "health"),
        "department": in_field(CONTENT, AT_THE_PHC, value="public_health"),
        "severity": misnamed("title", PATIENTS_SUFFER, "moderate"),
        "event_status": in_field(CONTENT, VACANT_FOR_YEARS, value="ongoing"),
    }
    return answer | changes


def read(answer: Any, record: Optional[dict] = None) -> Incident:
    return extract_incident(
        record if record is not None else phc_record(), provider=Scripted(payload=answer)
    )


def test_three_claims_naming_the_wrong_field_are_anchored_where_their_words_actually_sit():
    found = read(phc_answer())

    assert found.validation.issues == []
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.review_required is False
    assert found.record_kind is RecordKind.INCIDENT
    assert found.incident_type is EventType.HOSPITAL_SERVICE_GAP
    assert found.category is IncidentCategory.HEALTH
    assert found.department is Department.PUBLIC_HEALTH
    assert found.severity is SeverityLevel.MODERATE
    assert found.event_status is EventStatus.ONGOING

    moved = [warning for warning in found.generation.warnings if "verbatim only in" in warning]
    assert len(moved) == 2
    assert any("cited 'data.content'" in w and "only in 'title'" in w for w in moved)
    assert any("cited 'title'" in w and "only in 'data.content'" in w for w in moved)
    assert all("the evidence was anchored in the field that really holds them" in w for w in moved)

    index = found.evidence_by_id()
    holders = {item.field: item.text for item in build_context(phc_record()).fields}
    for name, field in (
        ("incident_type", "title"),
        ("category", "title"),
        ("severity", CONTENT),
        ("description", CONTENT),
        ("department", CONTENT),
        ("event_status", CONTENT),
    ):
        claim = found.claim(name)
        assert claim.review is ReviewState.ACCEPTED, name
        cited = index[claim.evidence_ids[0]]
        assert cited.field == field, f"{name} cites {cited.field}"
        assert holders[cited.field][cited.char_start : cited.char_end] == cited.quote
        assert cited.field_text_hash == compute_field_hash(holders[cited.field])


def test_a_headline_claim_cites_the_headline_and_a_body_claim_cites_the_body():
    answer = {
        "record_kind": "incident",
        "title": in_field("title", PHC_TITLE),
        "description": in_field(CONTENT, WHO_COME_FOR_TREATMENT),
        "incident_type": in_field("title", HEADLINE_GAP, value="hospital_service_gap"),
        "category": in_field(CONTENT, AT_THE_PHC, value="health"),
    }
    found = read(answer)

    assert found.validation.issues == []
    assert found.validation.state is ReviewState.ACCEPTED
    assert not [w for w in found.generation.warnings if "verbatim only in" in w]

    index = found.evidence_by_id()
    holders = {item.field: item.text for item in build_context(phc_record()).fields}
    for name, field, text in (
        ("incident_type", "title", HEADLINE_GAP),
        ("category", CONTENT, AT_THE_PHC),
    ):
        cited = index[found.claim(name).evidence_ids[0]]
        assert (cited.field, cited.quote, cited.char_start) == (field, text, holders[field].index(text))
        assert holders[cited.field][cited.char_start : cited.char_end] == cited.quote


def test_a_quote_that_is_in_no_field_the_record_carries_is_refused_as_unsupported():
    found = read(phc_answer(severity=misnamed(CONTENT, DEATHS_NOT_STATED, "critical")))

    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    finding = next(issue for issue in found.validation.issues if issue.field == "severity")
    assert finding.code is IssueCode.UNSUPPORTED_CLAIM
    assert "verbatim in no field this record carries" in finding.detail
    assert all(evidence.quote != DEATHS_NOT_STATED for evidence in found.evidence)

    kept = [found.claim(name) for name in ("category", "department", "event_status")]
    assert all(claim.review is ReviewState.ACCEPTED for claim in kept)


def test_a_word_the_body_repeats_cannot_be_handed_to_the_headline():
    found = read(phc_answer(category=misnamed("title", REPEATED_WORD, "health")))

    assert found.category is IncidentCategory.UNRESOLVED
    finding = next(issue for issue in found.validation.issues if issue.field == "category")
    assert finding.code is IssueCode.AMBIGUOUS_QUOTE
    assert "picks one of its copies" in finding.detail
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert len([w for w in found.generation.warnings if "verbatim only in" in w]) == 2


def test_an_unsupported_priority_is_omitted_instead_of_quarantining_the_reading():
    answer = water_answer() | {
        "priority": misnamed(CONTENT, URGENCY_NOT_STATED, "high"),
    }
    found = read(answer, waterlogging())

    assert found.priority is None
    assert found.claim("priority") is None
    assert not [issue for issue in found.validation.issues if issue.field == "priority"]
    assert found.validation.issues == []
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.review_required is False
    assert found.severity is SeverityLevel.MODERATE

    said = " ".join(found.generation.warnings)
    assert "the answer offered priority='high'" in said
    assert "instead of carrying a claim for it" in said
    assert "priority is left null rather than copied from the severity" not in said


def test_a_priority_the_record_does_state_is_kept_with_its_own_span():
    content = waterlogging()["data"]["content"]
    answer = water_answer() | {"priority": quote(content, HARD_SHIP) | {"value": "high"}}
    found = read(answer, waterlogging())

    assert found.priority is PriorityLevel.HIGH
    claim = found.claim("priority")
    assert claim.review is ReviewState.ACCEPTED
    cited = found.evidence_by_id()[claim.evidence_ids[0]]
    assert (cited.field, cited.char_start, cited.quote) == (
        CONTENT,
        content.index(HARD_SHIP),
        HARD_SHIP,
    )
    assert not [w for w in found.generation.warnings if "priority" in w]


def test_severity_and_priority_are_two_different_questions_with_two_different_spans():
    content = waterlogging()["data"]["content"]
    answer = water_answer() | {"priority": quote(content, HARD_SHIP) | {"value": "critical"}}
    found = read(answer, waterlogging())

    assert (found.severity, found.priority) == (SeverityLevel.MODERATE, PriorityLevel.CRITICAL)
    index = found.evidence_by_id()
    spans = {name: index[found.claim(name).evidence_ids[0]] for name in ("severity", "priority")}
    assert spans["severity"].evidence_id != spans["priority"].evidence_id
    assert spans["priority"].char_start == content.index(HARD_SHIP)
    assert found.claim("priority").evidence_ids != found.claim("severity").evidence_ids


def test_a_priority_outside_the_taxonomy_is_still_a_finding_a_person_sees():
    answer = water_answer() | {
        "priority": {"value": "urgent", "field": CONTENT, "quote": HARD_SHIP,
                     "char_start": 0, "confidence": 0.6},
    }
    found = read(answer, waterlogging())

    assert found.priority is None
    assert found.claim("priority") is None
    finding = next(issue for issue in found.validation.issues if issue.field == "priority")
    assert finding.code is IssueCode.INVALID_VALUE
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert found.severity is SeverityLevel.MODERATE


def test_the_tamil_place_keeps_its_own_words_and_leaves_the_map_to_gis():
    answer = phc_answer() | {
        "locations": [
            {"text": "திருவாதவூர்", "normalized_name": "Thiruvadhavur", "location_type": "town",
             "role": "event_location", "district": "Madurai", "field": CONTENT,
             "quote": AT_THE_PHC, "char_start": PHC_CONTENT.index(AT_THE_PHC), "confidence": 0.8},
            {"text": "மதுரை", "normalized_name": "Madurai", "location_type": "district",
             "role": "event_container", "district": "Madurai", "field": CONTENT,
             "quote": "மதுரை அருகே", "char_start": 0, "confidence": 0.85},
        ],
    }
    found = read(answer)

    assert found.validation.state is ReviewState.ACCEPTED
    assert [item.text for item in found.locations] == ["திருவாதவூர்", "மதுரை"]
    assert [item.normalized_name for item in found.locations] == ["Thiruvadhavur", "Madurai"]
    for place in found.locations:
        assert place.canonical_location_id is None
        assert (place.latitude, place.longitude) == (None, None)
        assert place.resolution_state is ResolutionState.PENDING_GIS
        assert place.district == "Madurai"
        assert place.review is ReviewState.ACCEPTED
    index = found.evidence_by_id()
    village = index[found.locations[0].evidence_ids[0]]
    assert village.field == CONTENT
    assert PHC_CONTENT[village.char_start : village.char_end] == village.quote
    assert found.provenance.record_id == "NEWS-MDU-PHC-0001"


def test_an_event_time_a_news_page_stamps_is_never_taken_for_the_moment_the_shortage_began():
    answer = phc_answer() | {
        "event_time": {"value": "2026-10-02T18:20:00", "precision": "minute",
                       "field": CONTENT, "quote": AT_THE_PHC,
                       "char_start": PHC_CONTENT.index(AT_THE_PHC), "confidence": 0.8},
    }
    found = read(answer)

    assert found.event_time is None
    assert found.event_time_precision is TimePrecision.UNKNOWN
    assert found.provenance.declared_event_time.isoformat() == "2026-10-02T18:20:00"
    assert found.claim("event_time").review is ReviewState.UNRESOLVED
    assert found.validation.issues == []
    assert "which is its publication time" in " ".join(found.generation.warnings)


def test_a_capture_stamp_offered_as_the_event_moment_is_named_as_the_fault():
    answer = phc_answer() | {
        "event_time": {"value": RETRIEVED_AT, "precision": "second", "field": CONTENT,
                       "quote": WHO_COME_FOR_TREATMENT,
                       "char_start": PHC_CONTENT.index(WHO_COME_FOR_TREATMENT),
                       "confidence": 0.7},
    }
    found = read(answer)

    assert found.event_time is None
    finding = next(issue for issue in found.validation.issues if issue.field == "event_time")
    assert finding.code is IssueCode.PROVIDER_WARNING
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert found.incident_type is EventType.HOSPITAL_SERVICE_GAP
    assert found.category is IncidentCategory.HEALTH


def test_an_answer_that_says_nothing_about_the_clock_leaves_it_unclaimed():
    found = read(phc_answer(event_time=None))

    assert found.event_time is None
    assert found.claim("event_time") is None
    assert found.validation.state is ReviewState.ACCEPTED
    assert "no span and no stamp of the record" in " ".join(found.generation.warnings)


def test_the_serialised_reading_adds_no_key_the_contract_did_not_already_carry():
    answer = phc_answer() | {
        "locations": [
            {"text": "திருவாதவூர்", "normalized_name": "Thiruvadhavur", "location_type": "town",
             "role": "event_location", "district": "Madurai", "field": CONTENT,
             "quote": AT_THE_PHC, "char_start": PHC_CONTENT.index(AT_THE_PHC), "confidence": 0.8},
        ],
        "entities": [
            {"text": "மாவட்ட நிர்வாகத்திற்கு", "entity_type": "government_body",
             "role": "decision_maker", "field": CONTENT, "quote": "மாவட்ட நிர்வாகத்திற்கு",
             "char_start": PHC_CONTENT.index("மாவட்ட நிர்வாகத்திற்கு"), "confidence": 0.75},
        ],
    }
    doc = read(answer).to_storage_document()

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
    assert set(doc["locations"][0]) == set(Location.model_fields)
    assert doc["locations"][0]["latitude"] is None
    assert doc["locations"][0]["canonical_location_id"] is None


def test_the_structured_price_and_forecast_readings_still_need_no_provider():
    price = process_record(market_price(market="Usilampatti", commodity="Tapioca"),
                           provider=NeverCalled())
    forecast = process_record(weather_forecast(), provider=NeverCalled())

    assert (price.record_kind, price.context_type) == (
        RecordKind.CONTEXT,
        ContextType.MARKET_PRICE,
    )
    assert (forecast.record_kind, forecast.context_type) == (
        RecordKind.FORECAST,
        ContextType.WEATHER_FORECAST,
    )
    for found in (price, forecast):
        assert found.validation.state is ReviewState.ACCEPTED
        assert found.review_required is False
        assert found.priority is None
        assert found.severity.value == "unresolved"
        assert found.generation.provider == "intelligence.structured"
        assert found.generation.model == "none"
        assert found.generation.prompt_version == MAPPING_VERSION
