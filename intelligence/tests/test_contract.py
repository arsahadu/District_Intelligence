"""Stage 1 contract: what may be stated, and only with what behind it."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from intelligence.contract import (
    Claim,
    ContextFact,
    ContextType,
    Entity,
    Incident,
    Issue,
    IssueCode,
    Location,
    PriorityLevel,
    Provenance,
    RecordKind,
    Relationship,
    ReviewState,
    SCHEMA_VERSION,
    Validation,
)
from intelligence.models.enums import (
    EventType,
    ExtractionMethod,
    Modality,
    ResolutionState,
    SeverityLevel,
    SpanValidation,
)
from intelligence.models.evidence import Evidence

HASH = "a" * 64
QUOTE = "தண்ணீர் தேங்கியது"


def provenance(**fields) -> Provenance:
    payload = {
        "record_id": "NEWS-MDU-0001",
        "source_id": "dinamalar",
        "source_type": "news",
        "source_url": "https://example.test/4338980",
        "raw_reference": "https://example.test/4338980",
        "retrieved_at": datetime(2026, 10, 3, 8, 52, 26),
        "input_hash": HASH,
    }
    payload.update(fields)
    return Provenance(**payload)


def evidence(evidence_id: str = "ev-1", **fields) -> Evidence:
    payload = {
        "evidence_id": evidence_id,
        "record_id": "NEWS-MDU-0001",
        "source_id": "dinamalar",
        "source_type": "news",
        "field": "data.content",
        "method": ExtractionMethod.LLM,
        "quote": QUOTE,
        "char_start": 100,
        "char_end": 100 + len(QUOTE),
        "field_text_hash": HASH,
        "span_validation": SpanValidation.VALIDATED,
        "confidence": 0.8,
    }
    payload.update(fields)
    return Evidence(**payload)


def claim(field: str = "severity", value: str = "high", **fields) -> Claim:
    payload = {
        "field": field,
        "value": value,
        "method": ExtractionMethod.LLM,
        "confidence": 0.8,
        "evidence_ids": ["ev-1"],
        "review": ReviewState.ACCEPTED,
    }
    payload.update(fields)
    return Claim(**payload)


def incident(**fields) -> Incident:
    payload = {
        "incident_id": "INC-NEWS-MDU-0001",
        "provenance": provenance(),
        "severity": SeverityLevel.HIGH,
        "claims": [claim()],
        "evidence": [evidence()],
        "validation": Validation(state=ReviewState.ACCEPTED, checked_at=datetime(2026, 10, 4)),
    }
    payload.update(fields)
    return Incident(**payload)


def test_a_bare_incident_is_legal_and_says_so():
    subject = Incident(incident_id="INC-1", provenance=provenance())
    assert subject.schema_version == SCHEMA_VERSION
    assert subject.record_kind is RecordKind.UNRESOLVED
    assert subject.context_type is None
    assert subject.severity is SeverityLevel.UNRESOLVED
    assert subject.event_status.value == "unknown"
    assert subject.incident_type.value == "unresolved"
    assert subject.priority is None
    assert subject.validation.state is ReviewState.UNRESOLVED
    assert subject.review_required is True
    assert subject.is_empty is True


def an_event() -> dict:
    """The same Incident with a flood the evidence established, ready for the kind checks."""
    return {
        "incident_type": EventType.FLOOD,
        "claims": [claim(), claim("incident_type", "flood")],
    }


def an_account() -> dict:
    """A quoted account of what happened with no type token behind it — kind's second route."""
    return {
        "description": QUOTE,
        "claims": [claim(), claim("description", QUOTE)],
    }


def test_an_incident_is_only_an_incident_while_an_event_is_established():
    with pytest.raises(ValidationError, match="nothing evidencing an event"):
        incident(record_kind=RecordKind.INCIDENT)
    established = incident(record_kind=RecordKind.INCIDENT, **an_event())
    assert established.record_kind is RecordKind.INCIDENT


def test_one_unreadable_classification_field_does_not_demote_an_evidenced_happening():
    accounted = incident(record_kind=RecordKind.INCIDENT, **an_account())
    assert accounted.incident_type is EventType.UNRESOLVED
    assert accounted.claim("description").review is ReviewState.ACCEPTED

    quiet = incident(record_kind=RecordKind.CONTEXT, **an_account())
    assert quiet.record_kind is RecordKind.CONTEXT


def test_context_cannot_be_stated_while_the_quotes_establish_an_event():
    with pytest.raises(ValidationError, match="an evidenced event is not context"):
        incident(record_kind=RecordKind.CONTEXT, **an_event())
    with pytest.raises(ValidationError, match="an evidenced event is not context"):
        incident(record_kind=RecordKind.FORECAST, **an_event())

    quiet = incident(record_kind=RecordKind.FORECAST, context_type=ContextType.WEATHER_FORECAST)
    assert quiet.context_type is ContextType.WEATHER_FORECAST
    assert quiet.incident_type is EventType.UNRESOLVED


def test_a_context_type_only_rides_a_record_that_states_no_event():
    with pytest.raises(ValidationError, match="context_type is only carried"):
        incident(context_type=ContextType.MARKET_PRICE)
    with pytest.raises(ValidationError, match="context_type is only carried"):
        incident(record_kind=RecordKind.INCIDENT, context_type=ContextType.MARKET_PRICE,
                 **an_event())

    priced = incident(record_kind=RecordKind.CONTEXT, context_type=ContextType.MARKET_PRICE)
    assert priced.to_storage_document()["context_type"] == "market_price"
    assert Incident.model_validate(priced.to_storage_document()).record_kind is RecordKind.CONTEXT


def test_a_context_fact_is_a_quoted_value_and_is_checked_like_one():
    fact = ContextFact(
        fact_id="fact-1",
        field="data.min_price",
        value="55.0",
        method=ExtractionMethod.LLM,
        confidence=0.8,
        review=ReviewState.ACCEPTED,
        evidence_ids=["ev-1"],
    )
    carried = incident(record_kind=RecordKind.CONTEXT, context_type=ContextType.MARKET_PRICE,
                       context_facts=[fact])
    assert carried.context_facts[0].field == "data.min_price"
    document = carried.to_storage_document()
    assert document["context_facts"][0]["value"] == "55.0"
    reread = Incident.model_validate(document)
    assert reread.context_facts[0].review is ReviewState.ACCEPTED
    assert reread.resolve_evidence(reread.context_facts[0].evidence_ids)[0].quote == QUOTE

    with pytest.raises(ValidationError, match="cites no evidence"):
        ContextFact(fact_id="fact-1", field="data.min_price", value="55.0",
                    review=ReviewState.ACCEPTED)
    with pytest.raises(ValidationError, match="unknown evidence_id"):
        incident(context_facts=[ContextFact(fact_id="fact-1", field="data.min_price",
                                           value="55.0", method=ExtractionMethod.LLM,
                                           confidence=0.8, evidence_ids=["ev-nope"])])
    with pytest.raises(ValidationError, match="duplicate context fact"):
        incident(context_facts=[fact, fact])

    unsure = ContextFact(fact_id="fact-1", field="data.min_price", value="55.0",
                         method=ExtractionMethod.LLM, confidence=0.8)
    open_one = incident(
        record_kind=RecordKind.CONTEXT,
        context_facts=[unsure],
        validation=Validation(state=ReviewState.REVIEW_REQUIRED, issues=[
            Issue(code=IssueCode.SPAN_MISMATCH, field="data.min_price", detail="not verbatim")]),
    )
    assert open_one.unaccepted_fields() == ["context_facts[fact-1]"]


def test_provenance_is_required_and_carries_the_view_source_handle():
    found = Incident(incident_id="INC-1", provenance=provenance())
    assert found.provenance.record_id == "NEWS-MDU-0001"
    assert found.provenance.has_view_source is True
    assert found.provenance.modality is Modality.TEXT
    with pytest.raises(ValidationError, match="record_id"):
        Provenance(source_id="dinamalar", source_type="news")
    assert provenance(source_url=None, raw_reference=None).has_view_source is False


def test_priority_is_nullable_because_it_may_not_be_calculable():
    assert incident().priority is None
    raised = incident(priority=PriorityLevel.HIGH, claims=[claim(), claim("priority", "high")])
    assert raised.priority is PriorityLevel.HIGH


def test_an_accepted_claim_needs_a_quote_except_for_what_the_record_already_carried():
    with pytest.raises(ValidationError, match="cites no evidence"):
        claim(evidence_ids=[])
    stamp = claim(
        "event_time", "2026-10-03T08:30:00", method=ExtractionMethod.SOURCE_METADATA, evidence_ids=[]
    )
    assert stamp.review is ReviewState.ACCEPTED


def test_a_value_with_no_claim_behind_it_is_rejected():
    with pytest.raises(ValidationError, match="no claim behind it"):
        incident(claims=[])


def test_evidence_references_must_resolve_and_ids_must_be_unique():
    with pytest.raises(ValidationError, match="unknown evidence_id"):
        incident(claims=[claim(evidence_ids=["ev-nope"])])
    with pytest.raises(ValidationError, match="duplicate evidence_id"):
        incident(evidence=[evidence(), evidence()])
    with pytest.raises(ValidationError, match="duplicate claim field"):
        incident(claims=[claim(), claim()])


def test_coordinates_and_canonical_ids_only_appear_once_gis_has_resolved():
    with pytest.raises(ValidationError, match="come as a pair"):
        Location(location_id="loc-1", text="Madurai", latitude=9.92)
    with pytest.raises(ValidationError, match="GIS has resolved"):
        Location(location_id="loc-1", text="Madurai", latitude=9.92, longitude=78.12)
    with pytest.raises(ValidationError, match="GIS has resolved"):
        Location(location_id="loc-1", text="Madurai", canonical_location_id="GAZ-1")
    pending = Location(location_id="loc-1", text="Madurai")
    assert pending.resolution_state is ResolutionState.PENDING_GIS
    resolved = Location(
        location_id="loc-1",
        text="Madurai",
        latitude=9.92,
        longitude=78.12,
        canonical_location_id="GAZ-1",
        resolution_state=ResolutionState.RESOLVED,
        review=ReviewState.ACCEPTED,
        evidence_ids=["ev-1"],
    )
    assert incident(locations=[resolved]).locations[0].latitude == 9.92


def test_an_accepted_item_without_evidence_is_not_accepted_at_all():
    with pytest.raises(ValidationError, match="cites no evidence"):
        Entity(entity_id="ent-1", text="அதிகாரிகள்", review=ReviewState.ACCEPTED)
    with pytest.raises(ValidationError, match="cites no evidence"):
        Location(location_id="loc-1", text="Madurai", review=ReviewState.ACCEPTED)


def test_a_relationship_cannot_link_an_item_to_itself():
    with pytest.raises(ValidationError, match="to itself"):
        Relationship(relationship_id="rel-1", from_ref="ent-1", to_ref="ent-1")


def test_validation_state_cannot_be_silently_accepting_findings():
    with pytest.raises(ValidationError, match="silent acceptance"):
        Validation(state=ReviewState.ACCEPTED, issues=[Issue(
            code=IssueCode.SPAN_MISMATCH, detail="the quote is not in the field")])
    with pytest.raises(ValidationError, match="not grounded"):
        incident(validation=Validation(state=ReviewState.ACCEPTED),
                 claims=[claim(review=ReviewState.REVIEW_REQUIRED)])
    with pytest.raises(ValidationError, match="says nothing is wrong"):
        incident(validation=Validation(state=ReviewState.REVIEW_REQUIRED))


def test_unresolved_validation_cannot_harbour_accepted_claims():
    with pytest.raises(ValidationError, match="claims are accepted"):
        incident(validation=Validation(state=ReviewState.UNRESOLVED))


def test_no_field_is_allowed_that_the_contract_does_not_declare():
    with pytest.raises(ValidationError, match="Extra inputs"):
        Incident(incident_id="INC-1", provenance=provenance(), ai_confidence=0.99)


def test_review_helpers_point_at_the_parts_a_person_should_read():
    found = incident(
        claims=[
            claim(),
            claim("status", "ongoing", review=ReviewState.REVIEW_REQUIRED, evidence_ids=[]),
        ],
        locations=[Location(location_id="loc-1", text="Madurai")],
        validation=Validation(state=ReviewState.REVIEW_REQUIRED, issues=[
            Issue(code=IssueCode.UNSUPPORTED_CLAIM, field="status", detail="no quote")]),
    )
    assert found.unaccepted_fields() == ["status", "locations[loc-1]"]
    assert found.claim("severity").review is ReviewState.ACCEPTED
    assert found.resolve_evidence(["ev-1", "ev-missing"])[0].evidence_id == "ev-1"
    assert found.is_empty is False


def test_the_document_survives_a_json_round_trip():
    document = incident(
        locations=[Location(location_id="loc-1", text="Madurai", review=ReviewState.ACCEPTED,
                            evidence_ids=["ev-1"])],
        entities=[Entity(entity_id="ent-1", text="அதிகாரிகள்", review=ReviewState.ACCEPTED,
                         evidence_ids=["ev-1"])],
        relationships=[Relationship(relationship_id="rel-1", from_ref="ent-1", to_ref="loc-1",
                                    review=ReviewState.ACCEPTED, evidence_ids=["ev-1"])],
    ).to_storage_document()
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["locations"][0]["text"] == "Madurai"
    assert Incident.model_validate(document).entities[0].text == "அதிகாரிகள்"
