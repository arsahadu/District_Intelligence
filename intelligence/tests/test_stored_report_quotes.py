from __future__ import annotations

from typing import Any

from intelligence.contract import Department, EventStatus, IncidentCategory, IssueCode, RecordKind, ReviewState
from intelligence.intelligence import extract_incident
from intelligence.models.enums import (
    ExtractionMethod,
    EventType,
    SeverityLevel,
    SpanValidation,
    TimePrecision,
)
from intelligence.tests.builders import CONTENT, Scripted, quote
from intelligence.tests.record_fixtures import DISTRICT, STATE, base_record

SHOCK_ID = "NEWS-MDU-caf0c933c1a793e2"
SHOCK_URL = "https://www.dinamalar.com/news/tamil-nadu-district-news-madurai/one-dead/4345173"
SHOCK_RETRIEVED_AT = "2026-10-10T07:46:34.988674Z"
SHOCK_TITLE = "மின்சாரம் தாக்கி ஒருவர் பலி"
SHOCK_DEATH = "மின்சாரம் தாக்கி சம்பவ இடத்திலேயே உயிரிழந்தார்"
SHOCK_POLICE = "போலீசார் விசாரிக்கின்றனர்"
SHOCK_PLACE = "தேனுார்ரோடு டாஸ்மாக் மதுக்கடை அருகே தனியார் தோப்பில்"
STORED_QUOTES_ONLY = f"{SHOCK_PLACE} {SHOCK_DEATH}. {SHOCK_POLICE}."


def shock_quotes_record(**fields: Any) -> dict[str, Any]:
    payload = base_record(
        record_id=SHOCK_ID,
        title=SHOCK_TITLE,
        event_time=None,
        source_url=SHOCK_URL,
        raw_reference=SHOCK_URL,
        retrieved_at=SHOCK_RETRIEVED_AT,
        data={"content": STORED_QUOTES_ONLY, "language": "ta"},
    )
    payload.update(fields)
    return payload


def shock_answer() -> dict[str, Any]:
    body = STORED_QUOTES_ONLY
    return {
        "record_kind": "incident",
        "title": {"value": SHOCK_TITLE, "field": "title", "quote": SHOCK_TITLE, "char_start": 0},
        "description": quote(body, SHOCK_DEATH),
        "incident_type": quote(body, "மின்சாரம் தாக்கி") | {"value": "other"},
        "department": quote(body, SHOCK_POLICE) | {"value": "police"},
        "severity": quote(body, "உயிரிழந்தார்") | {"value": "high"},
        "event_status": quote(body, SHOCK_POLICE) | {"value": "under_investigation"},
        "locations": [quote(body, SHOCK_PLACE) | {"text": SHOCK_PLACE, "location_type": "locality",
                                                  "role": "event_location"}],
    }


def read(payload: dict[str, Any], record: dict[str, Any] | None = None):
    return extract_incident(record or shock_quotes_record(), provider=Scripted(payload=payload))


def field_text(record: dict[str, Any], path: str) -> str:
    held: Any = record
    for part in path.split("."):
        held = held.get(part) if isinstance(held, dict) else None
    return "" if held is None else str(held)


def test_the_electric_shock_quotes_ground_verbatim_in_tamil_and_borrow_no_clock():
    record = shock_quotes_record()
    found = read(shock_answer(), record)

    assert found.validation.state is ReviewState.ACCEPTED, found.validation.issues
    assert found.record_kind is RecordKind.INCIDENT
    assert found.title == SHOCK_TITLE
    assert found.description == SHOCK_DEATH
    assert found.incident_type is EventType.OTHER
    assert found.category is IncidentCategory.UNRESOLVED
    assert found.department is Department.POLICE
    assert found.severity is SeverityLevel.HIGH
    assert found.event_status is EventStatus.UNDER_INVESTIGATION
    assert found.event_time is None
    assert found.event_time_precision is TimePrecision.UNKNOWN
    assert found.provenance.source_url == SHOCK_URL
    assert found.provenance.retrieved_at.isoformat().startswith("2026-10-10T07:46:34")
    for item in found.evidence:
        if item.quote is None or item.char_start is None:
            continue
        text = field_text(record, item.field)
        assert text[item.char_start:item.char_end] == item.quote, item.describe()
    declared = [item for item in found.evidence if item.method is ExtractionMethod.SOURCE_METADATA]
    assert declared and all(item.span_validation is SpanValidation.VALIDATED for item in declared)
    assert {item.quote for item in declared} == {DISTRICT, STATE}
    place = found.locations[0]
    assert place.text == SHOCK_PLACE
    assert place.canonical_location_id is None
    assert place.latitude is None and place.longitude is None


def test_a_reinflected_tamil_quote_of_the_death_is_refused_rather_than_repaired():
    payload = shock_answer()
    payload["description"] = {"value": "மின்சாரம் தாக்கியதில் உயிரிழந்தார்", "field": CONTENT,
                              "quote": "மின்சாரம் தாக்கியதில் உயிரிழந்தார்", "confidence": 0.8}
    found = read(payload)

    assert found.description is None
    assert any(item.code is IssueCode.UNSUPPORTED_CLAIM and item.field == "description"
               for item in found.validation.issues)
    assert found.review_required is True


def test_a_type_with_no_token_for_electrocution_is_answered_other_not_widened_into_a_power_cut():
    assert "electrocution" not in {member.value for member in EventType}
    payload = shock_answer()
    payload["incident_type"] = quote(STORED_QUOTES_ONLY, "மின்சாரம் தாக்கி") | {"value": "electrocution"}
    found = read(payload)

    assert found.incident_type is EventType.UNRESOLVED
    assert any(item.code is IssueCode.INVALID_VALUE and item.field == "incident_type"
               for item in found.validation.issues)
