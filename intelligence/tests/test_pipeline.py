"""Stage 1 pipeline: a CommonRecord in, one LLM call, an Incident that only says what the record does."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import pytest

from intelligence.context import build_context
from intelligence.contract import (
    ContextType,
    Department,
    EventStatus,
    IncidentCategory,
    IssueCode,
    PriorityLevel,
    RecordKind,
    ReviewState,
)
from intelligence.intelligence import (
    DEFAULT_LLM_CONFIDENCE,
    MAX_ANSWER_ITEMS,
    ExtractionDraft,
    InvalidExtraction,
    extraction_request,
)
from intelligence.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderNotConfigured,
    StructuredOutputError,
)
from intelligence.models.enums import EventType, ExtractionMethod, SeverityLevel, TimePrecision
from intelligence.pipeline import process_record
from intelligence.tests.record_fixtures import (
    CYCLONE_WARNING_TEXT,
    base_record,
    crop_distress_report,
    events_listing,
    market_price,
    mixed_language_article,
    weather_forecast,
    weather_warning,
    waterlogging,
)

CONTENT = "data.content"


@dataclass
class Scripted(LLMProvider):
    """Answers with exactly what the test wrote. No determinism is hiding behind it."""

    payload: Any
    name: str = "scripted"
    model_id: str = "gpt-oss-120b"

    @property
    def model(self) -> str:
        return self.model_id

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = json.dumps(self.payload) if not isinstance(self.payload, str) else self.payload
        return LLMResponse(
            provider=self.name,
            model=self.model_id,
            text=body,
            data=None if isinstance(self.payload, str) else self.payload,
            latency_ms=12.5,
        )


def quote(content: str, text: str, occurrence: int = 0) -> dict[str, Any]:
    """A real span: the offset of the nth copy of the text, taken from the field itself."""
    position = -1
    for _ in range(occurrence + 1):
        position = content.index(text, position + 1)
    return {
        "value": text,
        "field": CONTENT,
        "quote": text,
        "char_start": position,
        "confidence": 0.85,
    }


def synth(content: str, *, title: str = "District report", language: str = "en", **fields) -> dict:
    payload = base_record(
        record_id="NEWS-MDU-SYNTH-0001",
        title=title,
        event_time=None,
        data={"content": content, "language": language},
        source_url="https://example.test/report",
    )
    payload.update(fields)
    return payload


RAIN = "நேற்று மாலை பெய்த கனமழையால் சில பகுதிகளில் சாலைகளில் தண்ணீர் தேங்கியது"
STILL_WATCHING = "கண்காணித்து வருகின்றனர்"
OFFICIALS = "அதிகாரிகள்"


def water_answer() -> dict[str, Any]:
    content = waterlogging()["data"]["content"]
    title = waterlogging()["title"]
    return {
        "record_kind": "incident",
        "title": {"value": title, "field": "title", "quote": title, "char_start": 0,
                  "confidence": 0.9},
        "description": quote(content, RAIN),
        "incident_type": {"value": "urban_waterlogging", "field": CONTENT,
                          "quote": "தண்ணீர் தேங்கியது",
                          "char_start": content.index("தண்ணீர் தேங்கியது"), "confidence": 0.8},
        "severity": {"value": "moderate", "field": CONTENT, "quote": "கனமழையால்",
                     "char_start": content.index("கனமழையால்"), "confidence": 0.7},
        "event_status": {"value": "ongoing", "field": CONTENT, "quote": STILL_WATCHING,
                         "char_start": content.index(STILL_WATCHING), "confidence": 0.9},
        "event_time": {"value": "2026-10-03T08:30:00", "precision": "minute", "confidence": 0.8},
        "locations": [{"text": "மதுரை", "normalized_name": "Madurai", "location_type": "district",
                       "role": "event_container", "district": "Madurai",
                       "field": CONTENT, "quote": "மதுரை மாவட்டத்தில்",
                       "char_start": content.index("மதுரை மாவட்டத்தில்"), "confidence": 0.85}],
        "entities": [{"text": OFFICIALS, "entity_type": "government_official",
                      "role": "responding_authority", "field": CONTENT, "quote": OFFICIALS,
                      "char_start": content.index(OFFICIALS), "confidence": 0.8}],
        "relationships": [{"kind": "responds_to", "subject": OFFICIALS, "object": "Madurai",
                           "field": CONTENT, "quote": STILL_WATCHING,
                           "char_start": content.index(STILL_WATCHING), "confidence": 0.7}],
    }


def run(payload: Any, record: Optional[dict] = None):
    subject = record if record is not None else waterlogging()
    return process_record(subject, provider=Scripted(payload=payload))


def test_a_grounded_answer_becomes_an_incident_that_keeps_its_source():
    found = run(water_answer())
    assert found.incident_id == "INC-NEWS-MDU-WLR-0001"
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.review_required is False
    assert found.record_kind is RecordKind.INCIDENT
    assert found.context_type is None
    assert found.severity is SeverityLevel.MODERATE
    assert found.event_status.value == "ongoing"
    assert found.incident_type is EventType.URBAN_WATERLOGGING
    assert found.description == RAIN
    assert found.provenance.source_id == "dinamalar"
    assert found.provenance.source_url == waterlogging()["source_url"]
    assert found.provenance.retrieved_at is not None
    assert found.provenance.has_view_source is True
    assert found.generation.model == "gpt-oss-120b"
    assert found.generation.input_hash is not None


def test_every_accepted_claim_cites_a_span_that_replays_from_the_field():
    found = run(water_answer())
    sources = {item.field: item.text for item in build_context(waterlogging()).fields}
    index = found.evidence_by_id()
    for claim in found.claims:
        if claim.review is not ReviewState.ACCEPTED:
            continue
        if claim.method is ExtractionMethod.SOURCE_METADATA:
            continue
        assert claim.evidence_ids, claim.field
        for evidence_id in claim.evidence_ids:
            cited = index[evidence_id]
            assert cited.record_id == found.provenance.record_id
            assert sources[cited.field][cited.char_start : cited.char_end] == cited.quote


def test_a_quote_the_record_does_not_contain_is_dropped_not_repaired():
    payload = water_answer()
    payload["severity"] = {"value": "critical", "field": CONTENT,
                           "quote": "எல்லோரும் இறந்தனர்", "char_start": 0}
    found = run(payload)
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert any(issue.code is IssueCode.SPAN_MISMATCH for issue in found.validation.issues)
    assert all(evidence.quote != "எல்லோரும் இறந்தனர்" for evidence in found.evidence)


def test_a_value_outside_the_taxonomy_leaves_the_field_unresolved():
    payload = water_answer()
    payload["category"] = {"value": "very urgent indeed", "field": CONTENT,
                           "quote": "கனமழையால்", "char_start": 40}
    found = run(payload)
    assert found.category.value == "unresolved"
    finding = next(issue for issue in found.validation.issues
                   if issue.code is IssueCode.INVALID_VALUE)
    assert "category" in finding.describe()
    assert found.claim("category").review is ReviewState.REVIEW_REQUIRED


def test_the_model_cannot_hand_in_coordinates_canonical_ids_or_its_own_evidence():
    payload = water_answer()
    payload["locations"][0]["latitude"] = 9.92
    payload["locations"][0]["longitude"] = 78.12
    payload["locations"][0]["canonical_location_id"] = "GAZ-999"
    payload["evidence"] = [{"evidence_id": "trust-me"}]
    payload["incident_id"] = "INC-FROM-THE-MODEL"
    found = run(payload)
    location = found.locations[0]
    assert (location.latitude, location.longitude, location.canonical_location_id) == (None, None, None)
    assert location.resolution_state.value == "pending_gis"
    assert found.incident_id == "INC-NEWS-MDU-WLR-0001"
    codes = {issue.code for issue in found.validation.issues}
    assert IssueCode.UNAUTHORISED_GIS_VALUE in codes
    assert IssueCode.PROVIDER_WARNING in codes
    assert all(evidence.method is not ExtractionMethod.UNRESOLVED for evidence in found.evidence)


def test_a_quote_from_a_field_the_record_does_not_carry_is_a_finding():
    payload = water_answer()
    payload["description"] = {"value": "a summary", "field": "data.transcript", "quote": "a summary"}
    found = run(payload)
    assert any(issue.code is IssueCode.UNKNOWN_FIELD for issue in found.validation.issues)
    assert found.claim("description").review is ReviewState.UNRESOLVED


def test_a_repeated_quote_needs_an_offset_and_gets_one_honoured():
    content = ("Waterlogging on the Main Road. Waterlogging cleared after one pump failed on "
               "the Main Road.")
    record = synth(content)
    loose = {"title": {"value": "District report", "field": "title", "quote": "District report",
                       "char_start": 0},
             "description": {"value": "the Main Road", "field": CONTENT, "quote": "the Main Road"}}
    found = run(loose, record)
    assert any(issue.code is IssueCode.AMBIGUOUS_QUOTE for issue in found.validation.issues)
    assert found.description is None

    second = content.index("the Main Road", content.index("the Main Road") + 1)
    loose["description"]["char_start"] = second
    pinned = run(loose, record)
    assert pinned.description == "the Main Road"
    cited = pinned.evidence_by_id()[pinned.claim("description").evidence_ids[0]]
    assert cited.char_start == second
    assert content[cited.char_start : cited.char_end] == "the Main Road"


def test_a_title_may_be_grounded_by_repeating_the_record_s_own():
    payload = water_answer()
    del payload["title"]["quote"]
    payload["title"]["field"] = "title"
    found = run(payload)
    assert found.title == waterlogging()["title"]
    assert found.claim("title").review is ReviewState.ACCEPTED


def test_the_record_s_own_timestamp_is_accepted_without_a_span():
    payload = {"event_time": {"value": "2026-10-03T08:30:00", "precision": "minute"}}
    found = run(payload)
    assert found.event_time is not None
    assert found.event_time_precision is TimePrecision.MINUTE
    claim = found.claim("event_time")
    assert claim.method is ExtractionMethod.SOURCE_METADATA
    assert claim.evidence_ids == []
    assert claim.review is ReviewState.ACCEPTED


def test_a_stamp_the_record_never_declared_still_needs_a_quote():
    record = synth("Heat index crossed 44C at the university campus.", event_time=None)
    found = run({"event_time": {"value": "2026-05-01T12:00:00", "precision": "hour"}}, record)
    assert found.event_time is None
    assert any(issue.code is IssueCode.UNSUPPORTED_CLAIM for issue in found.validation.issues)


def test_relationship_sides_resolve_to_the_ids_the_pipeline_assigned():
    found = run(water_answer())
    relationship = found.relationships[0]
    assert relationship.from_ref == found.entities[0].entity_id
    assert relationship.to_ref == found.locations[0].location_id
    assert relationship.review is ReviewState.ACCEPTED


def test_a_link_to_something_the_answer_never_named_is_not_a_fact():
    payload = water_answer()
    payload["relationships"] = [{"kind": "involves", "subject": "WHO", "object": "Madurai"}]
    found = run(payload)
    assert found.relationships[0].review is ReviewState.UNRESOLVED
    assert any(issue.code is IssueCode.UNRESOLVED_REFERENCE for issue in found.validation.issues)


def test_an_ungrounded_location_stays_a_candidate_and_asks_for_review():
    payload = water_answer()
    payload["locations"].append({"text": "Tallam town", "location_type": "locality",
                                 "field": CONTENT, "quote": "தேங்கிய பகுதிகள்"})
    found = run(payload)
    candidate = found.locations[-1]
    assert (candidate.location_id, candidate.review) == ("loc-2", ReviewState.UNRESOLVED)
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert "locations[loc-2]" in found.unaccepted_fields()


def test_an_empty_answer_is_an_unresolved_incident_not_a_fabricated_one():
    found = run({})
    assert found.is_empty is True
    assert found.validation.state is ReviewState.UNRESOLVED
    assert found.claims == [] and found.evidence == []
    assert found.title is None and found.severity is SeverityLevel.UNRESOLVED
    assert found.provenance.record_id == "NEWS-MDU-WLR-0001"


def test_an_answer_that_is_not_shaped_right_fails_instead_of_becoming_an_incident():
    with pytest.raises(InvalidExtraction):
        run({"locations": "Madurai"})
    with pytest.raises(StructuredOutputError):
        run("this was never JSON at all")
    with pytest.raises(InvalidExtraction):
        run({"severity": {"value": ["high", "critical"]}})


def test_no_provider_configured_is_a_failure_the_caller_sees():
    with pytest.raises(ProviderNotConfigured, match="INTELLIGENCE_LLM_MODEL"):
        process_record(waterlogging(), environ={})


def test_the_same_answer_over_the_same_record_gives_the_same_incident():
    first, second = run(water_answer()), run(water_answer())
    stable = lambda incident: {**incident.to_storage_document(),
                               "validation": {**incident.to_storage_document()["validation"],
                                              "checked_at": None},
                               "generation": {**incident.to_storage_document()["generation"],
                                              "generated_at": None}}
    assert stable(first) == stable(second)


def test_an_english_record_goes_through_the_same_chain():
    content = "Madurai Corporation will widen East Madurai road after the July flooding."
    record = synth(content, title="East Madurai road widening", language="en")
    found = run({
        "title": {"value": "East Madurai road widening", "field": "title",
                  "quote": "East Madurai road widening", "char_start": 0},
        "description": quote(content, "will widen East Madurai road"),
        "incident_type": quote(content, "widen East Madurai road") | {"value": "road_damage"},
        "locations": [quote(content, "East Madurai road") | {"text": "East Madurai road",
                                                             "location_type": "street",
                                                             "role": "event_location"}],
    }, record)
    assert found.incident_type is EventType.ROAD_DAMAGE
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.locations[0].text == "East Madurai road"


def test_the_prompt_asks_for_tokens_from_the_contract_and_nothing_else():
    request = extraction_request(build_context(waterlogging()))
    assert "data.content" in request.user
    assert RAIN[:20] in request.user
    assert ", ".join(member.value for member in SeverityLevel) in request.system
    assert "urban_waterlogging" in request.system
    assert "latitude" not in request.system.split("Tokens per enumerated field")[0]
    assert set(ExtractionDraft.model_fields) >= {"locations", "entities", "relationships"}


def test_every_source_offers_the_fields_it_actually_carries():
    weather = build_context(weather_forecast())
    assert "data.content" not in weather.paths
    assert {"data.forecast", "data.max_temp_c", "data.min_temp_c", "data.station_id"} <= set(
        weather.paths
    )
    assert weather.text_of("data.max_temp_c") == "33.1"
    assert weather.text_of("data.warning") is None

    market = build_context(market_price())
    assert {"data.commodity", "data.market", "data.unit", "data.min_price"} <= set(market.paths)
    assert market.text_of("data.unit") == "kg"
    assert build_context(waterlogging()).text_of("data.language") is None


def test_a_price_line_grounds_on_its_own_numbers_and_stays_a_contextual_record():
    record = market_price(min_price=55.0, max_price=60.0)
    payload = {
        "record_kind": "context",
        "context_type": "market_price",
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "55.0", "field": "data.min_price", "quote": "55.0",
                        "char_start": 0},
        "category": {"value": "agriculture", "field": "data.commodity", "quote": "Tomato",
                     "char_start": 0},
    }
    found = run(payload, record)
    assert found.description == "55.0"
    assert found.record_kind is RecordKind.CONTEXT
    assert found.context_type is ContextType.MARKET_PRICE
    assert found.category is IncidentCategory.AGRICULTURE
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.incident_type is EventType.UNRESOLVED
    assert found.event_status is EventStatus.UNKNOWN
    assert found.priority is None
    assert found.locations == [] and found.entities == []
    assert found.validation.state is ReviewState.ACCEPTED
    cited = found.evidence_by_id()[found.claim("description").evidence_ids[0]]
    assert (cited.field, cited.quote) == ("data.min_price", "55.0")


def test_record_metadata_is_never_a_field_the_model_may_quote_from():
    payload = water_answer() | {
        "event_status": {"value": "reported", "field": "record metadata", "quote": "reported"}
    }
    found = run(payload)
    assert found.claim("event_status").review is ReviewState.UNRESOLVED
    issue = next(item for item in found.validation.issues if item.field == "event_status")
    assert issue.code is IssueCode.UNKNOWN_FIELD


def test_the_prompt_names_the_item_enums_and_says_a_record_need_not_be_an_incident():
    request = extraction_request(build_context(market_price()))
    assert "field data.commodity" in request.user
    assert "does not exist here unless it is printed" in request.system
    assert "Not every record is an incident" in request.system
    assert "'record metadata'" in request.system
    assert "- entities[].entity_type: person, government_official" in request.system
    assert "- locations[].role: event_location, event_container" in request.system
    assert "- relationships[].kind: located_at, involves" in request.system


FRAUD = (
    "The economic offences wing searched a rented office in Anna Nagar on Tuesday and took away "
    "ledgers, three sim cards and two laptops after depositors said the scheme had stopped paying "
    "the money it promised."
)
FRAUD_TITLE = "Investment fraud probe"


def test_a_classification_is_accepted_from_the_event_it_describes_not_its_token():
    """The Melur case: nothing in the text says fraud, police or investigation in English."""
    record = synth(FRAUD, title=FRAUD_TITLE, language="en")
    found = run({
        "title": {"value": FRAUD_TITLE, "field": "title"},
        "description": quote(FRAUD, "searched a rented office in Anna Nagar on Tuesday"),
        "incident_type": quote(FRAUD, "had stopped paying") | {"value": "cyber_or_financial_fraud"},
        "category": quote(FRAUD, "took away") | {"value": "law_and_order"},
        "department": quote(FRAUD, "economic offences wing") | {"value": "police"},
        "severity": quote(FRAUD, "ledgers, three sim cards and two laptops")
                    | {"value": "moderate"},
        "event_status": quote(FRAUD, "searched") | {"value": "under_investigation"},
        "locations": [quote(FRAUD, "Anna Nagar") | {"text": "Anna Nagar",
                                                   "location_type": "locality",
                                                   "role": "event_location"}],
        "entities": [
            quote(FRAUD, "economic offences wing") | {"text": "economic offences wing",
                                                      "entity_type": "government_body",
                                                      "role": "responding_authority"},
            quote(FRAUD, "depositors") | {"text": "depositors", "entity_type": "community_group",
                                          "role": "affected_party"},
        ],
        "relationships": [quote(FRAUD, "after depositors said")
                          | {"kind": "responds_to", "subject": "economic offences wing",
                             "object": "depositors"}],
    }, record)

    assert found.incident_type is EventType.CYBER_OR_FINANCIAL_FRAUD
    assert found.category is IncidentCategory.LAW_AND_ORDER
    assert found.department is Department.POLICE
    assert found.event_status is EventStatus.UNDER_INVESTIGATION
    assert found.severity is SeverityLevel.MODERATE
    assert found.priority is None
    assert [item.text for item in found.locations] == ["Anna Nagar"]
    assert [item.text for item in found.entities] == ["economic offences wing", "depositors"]
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.validation.issues == []

    cited = found.evidence_by_id()[found.claim("incident_type").evidence_ids[0]]
    assert (cited.field, cited.quote) == (CONTENT, "had stopped paying")
    assert "fraud" not in cited.quote.lower()
    assert found.claim("description").evidence_ids != found.claim("event_status").evidence_ids


def test_the_prompt_reads_an_event_from_the_body_instead_of_a_token_from_the_headline():
    rules = extraction_request(build_context(waterlogging())).system.split("Rules:\n")[1]
    assert "A quote proves what happened; it does not have to contain the word for it" in rules
    assert "Read the event from the body, not from the headline" in rules
    assert "never because its text is in another language" in rules
    assert "Metadata never supports a classification" in rules
    assert "Navigation links, related-story listings and date stamps describe no event" in rules
    assert "stay null unless the record" not in rules
    assert "do not read furniture as an event" in rules
    assert "does not exist here unless it is printed" in rules


def test_an_answer_that_names_too_much_is_cut_to_the_ceiling_the_prompt_promised():
    """One completion has a size. The overflow is dropped, and the drop is said out loud."""
    record = synth(FRAUD, title=FRAUD_TITLE, language="en")
    found = run({
        "title": {"value": FRAUD_TITLE, "field": "title"},
        "locations": [quote(FRAUD, "Anna Nagar") | {"text": f"place {index}",
                                                    "location_type": "locality",
                                                    "role": "event_location"}
                      for index in range(MAX_ANSWER_ITEMS + 3)],
    }, record)

    assert len(found.locations) == MAX_ANSWER_ITEMS
    assert [item.text for item in found.locations][:2] == ["place 0", "place 1"]
    assert found.locations[0].review is ReviewState.ACCEPTED
    issue = next(item for item in found.validation.issues
                 if item.code is IssueCode.PROVIDER_WARNING and item.field == "locations")
    assert f"the first {MAX_ANSWER_ITEMS} were kept" in issue.detail
    assert found.validation.state is ReviewState.REVIEW_REQUIRED

    rules = extraction_request(build_context(record)).system.split("Rules:\n")[1]
    assert f"never more than {MAX_ANSWER_ITEMS} in any list" in rules


def test_a_tamil_incident_is_classified_from_its_own_words_and_keeps_a_short_description():
    """Nothing here says crop, agriculture or flood in English, and the description is a restatement."""
    record = crop_distress_report()
    c = record["data"]["content"]
    found = run({
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "Flooding destroyed the crop in Virangi; farmers sat in protest.",
                        "field": CONTENT, "quote": "வெள்ளத்தில் பயிர் சேதமடைந்தது",
                        "char_start": c.index("வெள்ளத்தில் பயிர் சேதமடைந்தது"), "confidence": 0.8},
        "incident_type": quote(c, "பயிர் சேதமடைந்தது") | {"value": "crop_damage"},
        "category": quote(c, "விவசாயிகள்") | {"value": "agriculture"},
        "department": quote(c, "நட்ட ஈடு") | {"value": "agriculture"},
        "severity": quote(c, "வெள்ளத்தில்") | {"value": "moderate"},
        "priority": quote(c, "மறியலில்") | {"value": "high"},
        "event_status": quote(c, "மறியலில்") | {"value": "ongoing"},
        "locations": [quote(c, "விராங்கி") | {"text": "விராங்கி", "location_type": "village",
                                             "role": "event_location"}],
        "entities": [quote(c, "விவசாயிகள்") | {"text": "விவசாயிகள்",
                                               "entity_type": "community_group",
                                               "role": "affected_party"}],
    }, record)

    assert found.incident_type is EventType.CROP_DAMAGE
    assert found.category is IncidentCategory.AGRICULTURE
    assert found.department is Department.AGRICULTURE
    assert found.severity is SeverityLevel.MODERATE
    assert found.priority is not None and found.priority.value == "high"
    assert found.event_status is EventStatus.ONGOING
    assert found.description.startswith("Flooding destroyed")
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.validation.issues == []

    index = found.evidence_by_id()
    cited = index[found.claim("description").evidence_ids[0]]
    assert (cited.field, cited.quote) == (CONTENT, "வெள்ளத்தில் பயிர் சேதமடைந்தது")
    assert [item.text for item in found.locations] == ["விராங்கி"]
    assert [item.text for item in found.entities] == ["விவசாயிகள்"]


def test_a_body_quote_named_as_the_title_is_refused_and_points_at_the_field_that_carries_it():
    record = crop_distress_report()
    found = run({
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "Flood water destroyed the crop.", "field": "title",
                        "quote": "வெள்ளத்தில் பயிர் சேதமடைந்தது"},
    }, record)

    assert found.description is None
    assert found.claim("description").review is ReviewState.UNRESOLVED
    issue = next(item for item in found.validation.issues
                 if item.code is IssueCode.SPAN_MISMATCH)
    assert "is not verbatim in title" in issue.detail
    assert "it is verbatim in data.content" in issue.detail
    assert found.validation.state is ReviewState.REVIEW_REQUIRED


def test_a_mixed_tamil_and_english_record_separates_the_people_from_the_places():
    record = mixed_language_article()
    content = record["data"]["content"]
    found = run({
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "A ten hour power cut closed tailor shops in two Madurai areas.",
                        "field": CONTENT, "quote": "power outage தொடர்ந்தது",
                        "char_start": content.index("power outage தொடர்ந்தது")},
        "incident_type": quote(content, "மின்தடை") | {"value": "power_supply_disruption"},
        "severity": quote(content, "தையல் கடைகள் மூடப்பட்டன") | {"value": "low"},
        "event_status": quote(content, "வியாபாரிகள் தெரிவித்தனர்") | {"value": "reported"},
        "locations": [
            quote(content, "K.K. Nagar") | {"text": "K.K. Nagar", "normalized_name": "K.K. Nagar",
                                            "location_type": "locality", "role": "event_location"},
            quote(content, "அண்ணா நகர்") | {"text": "அண்ணா நகர்", "normalized_name": "Anna Nagar",
                                            "location_type": "locality", "role": "event_location"},
        ],
        "entities": [quote(content, "வியாபாரிகள்") | {"text": "வியாபாரிகள்",
                                                      "entity_type": "community_group",
                                                      "role": "affected_party"}],
        "relationships": [quote(content, "வியாபாரிகள் தெரிவித்தனர்")
                          | {"kind": "involves", "subject": "வியாபாரிகள்", "object": "K.K. Nagar"}],
    }, record)

    assert found.incident_type is EventType.POWER_SUPPLY_DISRUPTION
    assert found.severity is SeverityLevel.LOW
    assert [item.text for item in found.locations] == ["K.K. Nagar", "அண்ணா நகர்"]
    assert [item.text for item in found.entities] == ["வியாபாரிகள்"]
    assert found.relationships[0].from_ref == found.entities[0].entity_id
    assert found.relationships[0].to_ref == found.locations[0].location_id
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.validation.issues == []


def test_the_same_span_cannot_be_a_place_in_one_list_and_a_party_in_the_other():
    record = mixed_language_article()
    content = record["data"]["content"]
    span = {"field": CONTENT, "quote": "K.K. Nagar", "char_start": content.index("K.K. Nagar")}
    found = run({
        "incident_type": quote(content, "power outage") | {"value": "power_supply_disruption"},
        "locations": [span | {"text": "K.K. Nagar", "location_type": "locality",
                              "role": "event_location"}],
        "entities": [span | {"text": "K.K. Nagar", "entity_type": "organization",
                             "role": "action_subject"}],
    }, record)

    issue = next(item for item in found.validation.issues
                 if item.code is IssueCode.PROVIDER_WARNING)
    assert "locations[loc-1]" in issue.detail and "entities[ent-1]" in issue.detail
    assert "cannot be both" in issue.detail
    assert found.incident_type is EventType.POWER_SUPPLY_DISRUPTION
    assert found.validation.state is ReviewState.REVIEW_REQUIRED


def test_a_forecast_that_reports_no_event_stays_context_and_carries_no_classification():
    record = weather_forecast()
    forecast = record["data"]["forecast"]
    found = run({
        "record_kind": "forecast",
        "context_type": "weather_forecast",
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "Cloud with light rain expected over the district.",
                        "field": "data.forecast", "quote": "light rain over Madurai district",
                        "char_start": forecast.index("light rain over Madurai district")},
    }, record)

    assert found.description == "Cloud with light rain expected over the district."
    assert found.claim("description").value == found.description
    assert found.record_kind is RecordKind.FORECAST
    assert found.context_type is ContextType.WEATHER_FORECAST
    assert found.incident_type is EventType.UNRESOLVED
    assert found.category is IncidentCategory.UNRESOLVED
    assert found.department is Department.UNRESOLVED
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.event_status is EventStatus.UNKNOWN
    assert found.validation.state is ReviewState.ACCEPTED
    cited = found.evidence_by_id()[found.claim("description").evidence_ids[0]]
    assert (cited.field, cited.quote) == ("data.forecast", "light rain over Madurai district")


CYCLONE_DAMAGE = (
    "புயலால் மதுரை மாவட்டத்தில் பதினொரு மரங்கள் சாய்ந்தன; நான்கு வீடுகளின் கூரைகள் "
    "பறந்தன. மின்சார கம்பம் விழுந்து நான்கு மணி நேரம் மின்தடை ஏற்பட்டது."
)


def test_weather_text_that_reports_the_damage_is_an_incident_even_if_named_a_forecast():
    """A forecast is what a record promises; text that states damage already happened is an event."""
    record = synth(CYCLONE_DAMAGE, title="புயலில் மரம், வீடு சேதம்", language="ta")
    found = run({
        "record_kind": "forecast",
        "context_type": "weather_forecast",
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "A cyclone toppled eleven trees and tore four roofs off in the district.",
                        "field": CONTENT, "quote": "புயலால் மதுரை மாவட்டத்தில் பதினொரு மரங்கள் சாய்ந்தன",
                        "char_start": CYCLONE_DAMAGE.index("புயலால் மதுரை மாவட்டத்தில் பதினொரு மரங்கள் சாய்ந்தன")},
        "incident_type": quote(CYCLONE_DAMAGE, "நான்கு வீடுகளின் கூரைகள் பறந்தன")
                         | {"value": "cyclone_storm_damage"},
        "severity": quote(CYCLONE_DAMAGE, "பதினொரு மரங்கள் சாய்ந்தன") | {"value": "high"},
    }, record)

    assert found.record_kind is RecordKind.INCIDENT
    assert found.context_type is None
    assert found.incident_type is EventType.CYCLONE_STORM_DAMAGE
    assert found.severity is SeverityLevel.HIGH
    assert found.validation.state is ReviewState.ACCEPTED
    assert any("the context label is dropped" in w for w in found.generation.warnings)


def test_the_prompt_holds_the_stage_2_5_rules_and_names_the_fields_a_quote_may_cite():
    context = build_context(mixed_language_article())
    request = extraction_request(context)
    rules = request.system.split("Rules:\n")[1]
    assert "An incident says something happened and mattered" in rules
    assert "A forecast says something may happen" in rules
    assert "is the concise factual account of that event" in rules
    assert "A name belongs to one list, decided by the words around it" in rules
    assert "related, never copied" in rules
    assert "not proof by itself" in rules
    assert "Every quote names the field whose text it copies" in rules
    assert "never cite the title for what the body says" in rules
    assert "Offsets count from the start of the field you named" in rules

    assert "Every `field` names one of these, exactly as printed above" in request.user
    for path in context.paths:
        assert path in request.user
    assert "data.transcript" not in request.user


POLICE_TAMIL = (
    "மதுரை: கார்ப்பநேரி கிராமத்தின் நீர்ப்பாசனக் குழாயை மர்ம நபர்கள் திருடிச் சென்றனர். "
    "இதுகுறித்து லொக்கேட்டர் போலீசார் விசாரித்து வருகின்றனர்."
)
INVEST_TAMIL = (
    "மதுரை: மாதம் ரூ.2,000 செலுத்தினால் இரட்டிப்பு பணம் கிடைக்கும் என்று கூறி 150 "
    "குடும்பங்களிடமிருந்து ரூ.45 லட்சத்தை ஒரு தனியார் நிறுவனம் மோசம் செய்தது. புகாரின் பேரில் "
    "கண்காணிப்பு குழுவினர் வழக்கு பதிவு செய்து விசாரிக்கின்றனர்."
)
CASUALTY_TAMIL = (
    "மதுரை: கனமழையால் சாய்ந்த மரத்தில் சிக்கி இருவர் உயிரிழந்தனர். வெள்ள நீரில் 300 "
    "குடும்பங்கள் பாதிக்கப்பட்டுள்ளன."
)


def test_a_tamil_police_probe_gets_its_department_and_its_status_from_the_sentence():
    """Nothing in this record says police or investigation in English, and harm is never stated."""
    record = synth(POLICE_TAMIL, title="கார்ப்பநேரி குழாய் திருட்டு விசாரணை", language="ta")
    found = run({
        "record_kind": "incident",
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "Unknown people carried off Karpaneri village's irrigation pipe.",
                        "field": CONTENT, "quote": "நீர்ப்பாசனக் குழாயை மர்ம நபர்கள் திருடிச் சென்றனர்",
                        "char_start": POLICE_TAMIL.index(
                            "நீர்ப்பாசனக் குழாயை மர்ம நபர்கள் திருடிச் சென்றனர்"),
                        "confidence": 0.75},
        "incident_type": quote(POLICE_TAMIL, "மர்ம நபர்கள் திருடிச் சென்றனர்")
                         | {"value": "property_crime"},
        "category": quote(POLICE_TAMIL, "மர்ம நபர்கள் திருடிச் சென்றனர்")
                    | {"value": "law_and_order"},
        "department": quote(POLICE_TAMIL, "லொக்கேட்டர் போலீசார்") | {"value": "police"},
        "event_status": quote(POLICE_TAMIL, "விசாரித்து வருகின்றனர்")
                        | {"value": "under_investigation"},
        "locations": [quote(POLICE_TAMIL, "கார்ப்பநேரி கிராமத்தின்")
                      | {"text": "Karpaneri", "normalized_name": "Karpaneri",
                         "location_type": "village", "role": "event_location"}],
        "entities": [quote(POLICE_TAMIL, "லொக்கேட்டர் போலீசார்")
                     | {"text": "லொக்கேட்டர் போலீசார்", "entity_type": "government_body",
                        "role": "responding_authority"}],
    }, record)

    assert found.record_kind is RecordKind.INCIDENT
    assert found.department is Department.POLICE
    assert found.event_status is EventStatus.UNDER_INVESTIGATION
    assert found.incident_type is EventType.PROPERTY_CRIME
    assert found.category is IncidentCategory.LAW_AND_ORDER
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.priority is None
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.validation.issues == []

    index = found.evidence_by_id()
    assert index[found.claim("department").evidence_ids[0]].quote == "லொக்கேட்டர் போலீசார்"
    assert index[found.claim("event_status").evidence_ids[0]].quote == "விசாரித்து வருகின்றனர்"
    assert found.claim("category").evidence_ids == found.claim("incident_type").evidence_ids
    assert found.locations[0].text == "Karpaneri"
    assert index[found.locations[0].evidence_ids[0]].quote == "கார்ப்பநேரி கிராமத்தின்"


def test_a_tamil_investment_fraud_is_an_incident_with_an_account_the_body_supports():
    record = synth(INVEST_TAMIL, title="ரூ.45 லட்சம் முதலீட்டு மோசடி", language="ta")
    found = run({
        "record_kind": "incident",
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "A private firm took Rs 45 lakh from 150 families on a promise "
                                 "of doubling their money.",
                        "field": CONTENT, "quote": "ரூ.45 லட்சத்தை ஒரு தனியார் நிறுவனம் மோசம் செய்தது",
                        "char_start": INVEST_TAMIL.index(
                            "ரூ.45 லட்சத்தை ஒரு தனியார் நிறுவனம் மோசம் செய்தது")},
        "incident_type": quote(INVEST_TAMIL, "மோசம் செய்தது")
                         | {"value": "cyber_or_financial_fraud"},
        "category": quote(INVEST_TAMIL, "வழக்கு பதிவு செய்து") | {"value": "law_and_order"},
        "department": quote(INVEST_TAMIL, "கண்காணிப்பு குழுவினர்") | {"value": "police"},
        "severity": quote(INVEST_TAMIL, "150 குடும்பங்களிடமிருந்து") | {"value": "moderate"},
        "event_status": quote(INVEST_TAMIL, "விசாரிக்கின்றனர்") | {"value": "under_investigation"},
        "locations": [quote(INVEST_TAMIL, "மதுரை") | {"text": "மதுரை",
                                                     "location_type": "district",
                                                     "role": "event_container"}],
        "entities": [
            quote(INVEST_TAMIL, "ஒரு தனியார் நிறுவனம்") | {"text": "ஒரு தனியார் நிறுவனம்",
                                                          "entity_type": "organization",
                                                          "role": "action_subject"},
            quote(INVEST_TAMIL, "கண்காணிப்பு குழுவினர்") | {"text": "கண்காணிப்பு குழுவினர்",
                                                            "entity_type": "government_body",
                                                            "role": "responding_authority"},
        ],
    }, record)

    assert found.record_kind is RecordKind.INCIDENT
    assert found.incident_type is EventType.CYBER_OR_FINANCIAL_FRAUD
    assert found.category is IncidentCategory.LAW_AND_ORDER
    assert found.event_status is EventStatus.UNDER_INVESTIGATION
    assert found.description is not None
    assert found.description.startswith("A private firm took Rs 45 lakh")
    assert [item.text for item in found.entities] == ["ஒரு தனியார் நிறுவனம்",
                                                     "கண்காணிப்பு குழுவினர்"]
    assert found.validation.state is ReviewState.ACCEPTED


def test_a_normalised_place_survives_only_when_the_quote_stays_the_source_s_words():
    record = synth(POLICE_TAMIL, title="கார்ப்பநேரி குழாய் திருட்டு விசாரணை", language="ta")
    body = {
        "record_kind": "incident",
        "incident_type": quote(POLICE_TAMIL, "திருடிச் சென்றனர்") | {"value": "property_crime"},
        "description": quote(POLICE_TAMIL, "நீர்ப்பாசனக் குழாயை"),
    }

    clean = run(body | {"locations": [quote(POLICE_TAMIL, "கார்ப்பநேரி கிராமத்தின்")
                                      | {"text": "Karpaneri", "normalized_name": "Karpaneri",
                                         "location_type": "village", "role": "event_location"}]},
                record)
    assert clean.locations[0].review is ReviewState.ACCEPTED
    cited = clean.evidence_by_id()[clean.locations[0].evidence_ids[0]]
    assert (clean.locations[0].text, cited.quote) == ("Karpaneri", "கார்ப்பநேரி கிராமத்தின்")

    invented = run(body | {"locations": [{"text": "Karpaneri", "normalized_name": "Karpaneri",
                                          "location_type": "village", "role": "event_location",
                                          "field": CONTENT, "quote": "கார்ப்பநேரி கிராமத்தில்"}]},
                   record)
    assert invented.locations[0].review is ReviewState.UNRESOLVED
    assert invented.locations[0].evidence_ids == []
    issue = next(item for item in invented.validation.issues
                 if item.code is IssueCode.SPAN_MISMATCH)
    assert "locations[loc-1]" in issue.describe()
    assert all("Karpaneri" not in str(item.quote) for item in invented.evidence)

    bare = run(body | {"locations": [{"text": "Karpaneri", "location_type": "village"}]}, record)
    assert bare.locations[0].review is ReviewState.UNRESOLVED
    assert any(item.code is IssueCode.UNSUPPORTED_CLAIM and item.field == "locations[loc-1]"
               for item in bare.validation.issues)


def test_each_controlled_token_is_read_from_the_meaning_of_a_tamil_span():
    record = synth(CASUALTY_TAMIL, title="கனமழையில் இருவர் பலி", language="ta")
    found = run({
        "record_kind": "incident",
        "description": quote(CASUALTY_TAMIL, "கனமழையால் சாய்ந்த மரத்தில்"),
        "incident_type": quote(CASUALTY_TAMIL, "சாய்ந்த மரத்தில் சிக்கி")
                         | {"value": "tree_fall"},
        "category": quote(CASUALTY_TAMIL, "கனமழையால்") | {"value": "disaster"},
        "department": quote(CASUALTY_TAMIL, "வெள்ள நீரில்")
                      | {"value": "district_disaster_management"},
        "severity": quote(CASUALTY_TAMIL, "இருவர் உயிரிழந்தனர்") | {"value": "high"},
        "event_status": quote(CASUALTY_TAMIL, "பாதிக்கப்பட்டுள்ளன") | {"value": "ongoing"},
    }, record)

    assert found.incident_type is EventType.TREE_FALL
    assert found.category is IncidentCategory.DISASTER
    assert found.department is Department.DISTRICT_DISASTER_MANAGEMENT
    assert found.validation.state is ReviewState.ACCEPTED
    index = found.evidence_by_id()
    for name in ("incident_type", "category", "department", "severity", "event_status"):
        cited = index[found.claim(name).evidence_ids[0]]
        assert getattr(found, name).value not in cited.quote
        assert cited.field == CONTENT


def test_a_severity_nobody_quoted_for_stays_unresolved_however_likely_it_seems():
    record = synth(INVEST_TAMIL, title="ரூ.45 லட்சம் முதலீட்டு மோசடி", language="ta")
    body = {
        "record_kind": "incident",
        "incident_type": quote(INVEST_TAMIL, "மோசம் செய்தது")
                         | {"value": "cyber_or_financial_fraud"},
        "description": quote(INVEST_TAMIL, "ரூ.45 லட்சத்தை"),
    }

    silent = run(body | {"severity": {"value": "critical", "field": CONTENT}}, record)
    assert silent.severity is SeverityLevel.UNRESOLVED
    assert silent.claim("severity").review is ReviewState.UNRESOLVED
    assert any(item.code is IssueCode.UNSUPPORTED_CLAIM and item.field == "severity"
               for item in silent.validation.issues)
    assert silent.validation.state is ReviewState.REVIEW_REQUIRED

    invented = run(body | {"severity": {"value": "critical", "field": CONTENT,
                                        "quote": "ஐந்து பேர் இறந்தனர்"}}, record)
    assert invented.severity is SeverityLevel.UNRESOLVED
    assert any(item.code is IssueCode.SPAN_MISMATCH and item.field == "severity"
               for item in invented.validation.issues)


def test_lives_lost_in_the_text_are_the_evidence_a_band_is_answered_with():
    record = synth(CASUALTY_TAMIL, title="கனமழையில் இருவர் பலி", language="ta")
    found = run({
        "record_kind": "incident",
        "incident_type": quote(CASUALTY_TAMIL, "சாய்ந்த மரத்தில்") | {"value": "tree_fall"},
        "description": quote(CASUALTY_TAMIL, "கனமழையால் சாய்ந்த மரத்தில் சிக்கி"),
        "severity": quote(CASUALTY_TAMIL, "இருவர் உயிரிழந்தனர்") | {"value": "high"},
        "priority": quote(CASUALTY_TAMIL, "300 குடும்பங்கள் பாதிக்கப்பட்டுள்ளன")
                    | {"value": "high"},
    }, record)

    assert found.severity is SeverityLevel.HIGH
    assert found.priority is PriorityLevel.HIGH
    cited = found.evidence_by_id()[found.claim("severity").evidence_ids[0]]
    assert cited.quote == "இருவர் உயிரிழந்தனர்"
    assert found.claim("priority").evidence_ids != found.claim("severity").evidence_ids
    assert found.validation.state is ReviewState.ACCEPTED


STATUS_CASES = (
    pytest.param("மதுரை: கடையில் பணம் மோசம்; போலீசார் விசாரித்து வருகின்றனர்",
                 "விசாரித்து வருகின்றனர்", "property_crime", EventStatus.UNDER_INVESTIGATION,
                 id="under-investigation"),
    pytest.param("மதுரை: நான்கு மணி நேர மின்தடை தொடர்கிறது; கடைகள் மூடப்பட்டன",
                 "மின்தடை தொடர்கிறது", "power_supply_disruption", EventStatus.ONGOING,
                 id="ongoing"),
    pytest.param("மதுரை: குப்பை மேட்டுக்கு நகராட்சி நடவடிக்கை எடுத்தது",
                 "நடவடிக்கை எடுத்தது", "waste_management", EventStatus.ACTION_TAKEN,
                 id="action-taken"),
    pytest.param("மதுரை: ஊரின் குடிநீர் பிரச்னைக்கு தீர்வு காணப்பட்டது",
                 "தீர்வு காணப்பட்டது", "drinking_water_shortage", EventStatus.RESOLVED,
                 id="resolved"),
)


@pytest.mark.parametrize("content,verb,token,expected", STATUS_CASES)
def test_the_verb_the_source_uses_now_is_what_the_status_is_answered_from(
    content, verb, token, expected
):
    record = synth(content, title="மதுரை செய்தி", language="ta")
    found = run({
        "record_kind": "incident",
        "incident_type": quote(content, verb) | {"value": token},
        "description": quote(content, verb),
        "event_status": quote(content, verb) | {"value": expected.value},
    }, record)

    assert found.event_status is expected
    assert found.record_kind is RecordKind.INCIDENT
    assert found.validation.state is ReviewState.ACCEPTED


TRANSFER_TAMIL = (
    "மதுரை: விவசாயக் குழும அலுவலர் ஒருவரை மாநில நிவாரணப் பணிக்கு பணியிடம் மாற்றி "
    "உத்தரவு பிறப்பிக்கப்பட்டுள்ளது."
)


def test_an_administrative_act_the_record_states_is_an_incident_of_its_own_kind():
    record = synth(TRANSFER_TAMIL, title="பணியிட மாற்ற உத்தரவு", language="ta")
    found = run({
        "record_kind": "incident",
        "title": {"value": record["title"], "field": "title"},
        "description": quote(TRANSFER_TAMIL, "பணியிடம் மாற்றி உத்தரவு பிறப்பிக்கப்பட்டுள்ளது"),
        "incident_type": quote(TRANSFER_TAMIL, "உத்தரவு பிறப்பிக்கப்பட்டுள்ளது")
                         | {"value": "personnel_transfer"},
        "category": quote(TRANSFER_TAMIL, "விவசாயக் குழும அலுவலர்") | {"value": "public_service"},
        "department": quote(TRANSFER_TAMIL, "விவசாயக் குழும அலுவலர்") | {"value": "agriculture"},
        "event_status": quote(TRANSFER_TAMIL, "உத்தரவு பிறப்பிக்கப்பட்டுள்ளது")
                        | {"value": "action_taken"},
        "entities": [quote(TRANSFER_TAMIL, "விவசாயக் குழும அலுவலர்")
                     | {"text": "விவசாயக் குழும அலுவலர்",
                        "entity_type": "government_official", "role": "action_subject"}],
    }, record)

    assert found.record_kind is RecordKind.INCIDENT
    assert found.incident_type is EventType.PERSONNEL_TRANSFER
    assert found.category is IncidentCategory.PUBLIC_SERVICE
    assert found.event_status is EventStatus.ACTION_TAKEN
    assert found.context_type is None
    assert found.validation.state is ReviewState.ACCEPTED


def test_a_diary_of_programmes_that_have_not_happened_is_context_not_a_hollow_case():
    record = events_listing()
    body = record["data"]["content"]
    found = run({
        "record_kind": "context",
        "context_type": "administrative_notice",
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "The temple's annadhanam for the Purattasi Saturdays, at noon.",
                        "field": CONTENT, "quote": "ஆஞ்சநேயர் கோயில் சார்பில் புரட்டாசி சனி அன்னதானம்",
                        "char_start": body.index(
                            "ஆஞ்சநேயர் கோயில் சார்பில் புரட்டாசி சனி அன்னதானம்")},
    }, record)

    assert found.record_kind is RecordKind.CONTEXT
    assert found.context_type is ContextType.ADMINISTRATIVE_NOTICE
    assert found.incident_type is EventType.UNRESOLVED
    assert found.event_status is EventStatus.UNKNOWN
    assert found.validation.state is ReviewState.ACCEPTED


def test_an_account_read_from_the_body_cites_the_body_and_never_the_headline():
    record = crop_distress_report()
    body = record["data"]["content"]
    found = run({
        "record_kind": "incident",
        "title": {"value": record["title"], "field": "title"},
        "description": {"value": "Flood water destroyed the crop in the Virangi area.",
                        "field": CONTENT, "quote": "வெள்ளத்தில் பயிர் சேதமடைந்தது",
                        "char_start": body.index("வெள்ளத்தில் பயிர் சேதமடைந்தது")},
        "incident_type": quote(body, "பயிர் சேதமடைந்தது") | {"value": "crop_damage"},
    }, record)

    cited = found.evidence_by_id()[found.claim("description").evidence_ids[0]]
    assert cited.field == CONTENT
    assert cited.quote not in record["title"]
    assert body[cited.char_start : cited.char_end] == cited.quote
    title_claim = found.evidence_by_id()[found.claim("title").evidence_ids[0]]
    assert title_claim.field == "title"
    assert found.validation.state is ReviewState.ACCEPTED


def test_an_incident_nobody_could_describe_is_reported_instead_of_accepted():
    """A genuine event still owes an account: silence on description is said, not swallowed."""
    record = synth(POLICE_TAMIL, title="கார்ப்பநேரி குழாய் திருட்டு விசாரணை", language="ta")
    event = {
        "record_kind": "incident",
        "incident_type": quote(POLICE_TAMIL, "திருடிச் சென்றனர்") | {"value": "property_crime"},
    }

    missing = run(event, record)
    assert missing.record_kind is RecordKind.INCIDENT
    assert missing.description is None
    assert missing.validation.state is ReviewState.REVIEW_REQUIRED
    issue = next(item for item in missing.validation.issues
                 if item.code is IssueCode.PROVIDER_WARNING and item.field == "description")
    assert "stays unresolved for a person" in issue.detail

    refused = run(event | {"description": {"value": "A pipe was stolen.", "field": CONTENT,
                                           "quote": "குழாயை மர்ம மனிதர்கள்"}}, record)
    assert refused.description is None
    grounding = [item for item in refused.validation.issues if item.field == "description"]
    assert [item.code for item in grounding] == [IssueCode.SPAN_MISMATCH]


def test_an_incident_whose_text_never_said_whether_it_goes_on_is_marked_unknown_loudly():
    record = synth(POLICE_TAMIL, title="கார்ப்பநேரி குழாய் திருட்டு விசாரணை", language="ta")
    found = run({
        "record_kind": "incident",
        "incident_type": quote(POLICE_TAMIL, "திருடிச் சென்றனர்") | {"value": "property_crime"},
        "description": quote(POLICE_TAMIL, "நீர்ப்பாசனக் குழாயை"),
    }, record)

    assert found.event_status is EventStatus.UNKNOWN
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.validation.issues == []
    assert any("event_status" in warning for warning in found.generation.warnings)


def test_an_answer_cannot_call_itself_an_incident_that_no_quote_established():
    record = synth(POLICE_TAMIL, title="கார்ப்பநேரி குழாய் திருட்டு விசாரணை", language="ta")
    claim = {"record_kind": "incident",
             "title": {"value": record["title"], "field": "title"},
             "incident_type": {"value": "property_crime", "field": CONTENT,
                               "quote": "குழாயை மர்ம மனிதர்கள்"}}

    bare = run(claim, record)
    assert bare.record_kind is RecordKind.UNRESOLVED
    assert bare.context_type is None
    assert bare.incident_type is EventType.UNRESOLVED
    assert any(item.code is IssueCode.PROVIDER_WARNING and item.field == "record_kind"
               for item in bare.validation.issues)

    filed = run(claim | {"context_type": "general_information"}, record)
    assert filed.record_kind is RecordKind.CONTEXT
    assert filed.context_type is ContextType.GENERAL_INFORMATION
    assert any(item.code is IssueCode.PROVIDER_WARNING and item.field == "record_kind"
               for item in filed.validation.issues)


def test_a_number_the_model_never_gave_is_attributed_to_the_pipeline_and_said_so():
    record = synth(POLICE_TAMIL, title="கார்ப்பநேரி குழாய் திருட்டு விசாரணை", language="ta")
    found = run({
        "record_kind": "incident",
        "incident_type": {"value": "property_crime", "field": CONTENT,
                          "quote": "மர்ம நபர்கள் திருடிச் சென்றனர்",
                          "char_start": POLICE_TAMIL.index("மர்ம நபர்கள் திருடிச் சென்றனர்")},
        "description": {"value": "நீர்ப்பாசனக் குழாயை", "field": CONTENT,
                        "quote": "நீர்ப்பாசனக் குழாயை",
                        "char_start": POLICE_TAMIL.index("நீர்ப்பாசனக் குழாயை")},
    }, record)

    claim = found.claim("incident_type")
    assert claim.confidence == DEFAULT_LLM_CONFIDENCE
    assert claim.confidence != 1.0
    assert "confidence was not reported" in claim.notes
    cited = found.evidence_by_id()[claim.evidence_ids[0]]
    assert "confidence was not reported" in cited.notes
    assert any("reported no confidence" in warning for warning in found.generation.warnings)

    stated = run({
        "record_kind": "incident",
        "incident_type": {"value": "property_crime", "field": CONTENT,
                          "quote": "மர்ம நபர்கள் திருடிச் சென்றனர்",
                          "char_start": POLICE_TAMIL.index("மர்ம நபர்கள் திருடிச் சென்றனர்"),
                          "confidence": 0.4},
    }, record)
    assert stated.claim("incident_type").confidence == 0.4
    assert stated.claim("incident_type").notes is None


def test_the_prompt_decides_kind_status_normalisation_and_confidence_by_its_own_rules():
    rules = extraction_request(build_context(waterlogging())).system.split("Rules:\n")[1]
    assert "Not every record is an incident" in rules
    assert "Answer record_kind as incident for the first kind" in rules
    assert "context_type naming what the record does carry" in rules
    assert "an act of the administration and an event at once" in rules
    assert "informs without stating anything that took place" in rules
    assert "unknown only when the record truly says nothing about now" in rules
    assert "a rewritten name is not evidence" in rules
    assert "nothing is 1.0 just because it was found" in rules
    assert "`other` is for an event the record states plainly" in rules
    assert "null when nothing says the clock matters" in rules

    vocabulary = extraction_request(build_context(waterlogging())).system
    assert "- record_kind: incident, forecast, context, unresolved" in vocabulary
    assert "- context_type: market_price, weather_forecast" in vocabulary
    assert {"record_kind", "context_type"} <= set(ExtractionDraft.model_fields)


def test_the_rules_the_model_is_given_are_the_same_for_every_source():
    """No per-source branch: only the record's own fields and metadata ever change."""
    def rules_for(record: dict) -> str:
        system = extraction_request(build_context(record)).system
        return system.split("Rules:\n")[1].split("Tokens per enumerated field")[0]

    for record in (waterlogging(), market_price(), weather_forecast(), crop_distress_report()):
        assert rules_for(record) == rules_for(waterlogging())
        assert record["record_id"] not in rules_for(record)


def test_a_price_line_keeps_the_values_its_own_fields_hold():
    record = market_price(min_price=55.0, max_price=1004.0)
    context = build_context(record)
    found = run(
        {
            "record_kind": "context",
            "context_type": "market_price",
            "context_facts": [
                {"field": "data.commodity", "value": "Tomato", "quote": "Tomato",
                 "char_start": 0, "confidence": 0.9},
                {"field": "data.min_price", "value": "55", "quote": "55.0", "char_start": 0,
                 "confidence": 0.9},
                {"field": "data.max_price", "value": "1,004", "quote": "1004.0", "char_start": 0,
                 "confidence": 0.85},
                {"field": "data.unit", "value": "kg", "quote": "kg", "char_start": 0,
                 "confidence": 0.9},
                {"field": "data.market", "value": "Anna nagar", "quote": "Anna nagar",
                 "char_start": 0, "confidence": 0.9},
            ],
        },
        record,
    )

    assert found.record_kind is RecordKind.CONTEXT
    assert found.context_type is ContextType.MARKET_PRICE
    assert found.incident_type is EventType.UNRESOLVED
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.validation.state is ReviewState.ACCEPTED
    assert found.validation.issues == []
    assert [item.field for item in found.context_facts] == [
        "data.commodity", "data.min_price", "data.max_price", "data.unit", "data.market"
    ]
    assert [item.value for item in found.context_facts] == [
        "Tomato", "55", "1,004", "kg", "Anna nagar"
    ]
    assert [item.review for item in found.context_facts] == [ReviewState.ACCEPTED] * 5
    for fact in found.context_facts:
        cited = found.evidence_by_id()[fact.evidence_ids[0]]
        assert cited.field == fact.field
        assert cited.quote == context.text_of(fact.field)


def test_a_forecast_keeps_its_warning_and_temperatures_as_the_same_kind_of_fact():
    record = weather_warning(text=CYCLONE_WARNING_TEXT)
    warning = record["data"]["warning"]
    found = run(
        {
            "record_kind": "forecast",
            "context_type": "weather_forecast",
            "context_facts": [
                {"field": "data.warning", "value": warning, "quote": warning, "char_start": 0,
                 "confidence": 0.9},
                {"field": "data.max_temp_c", "value": "36.0", "quote": "36.0", "char_start": 0,
                 "confidence": 0.95},
                {"field": "data.min_temp_c", "value": "26.0", "quote": "26.0", "char_start": 0,
                 "confidence": 0.95},
                {"field": "data.station_id", "value": "43360", "quote": "43360", "char_start": 0,
                 "confidence": 0.9},
            ],
        },
        record,
    )

    assert found.record_kind is RecordKind.FORECAST
    assert found.context_type is ContextType.WEATHER_FORECAST
    assert found.incident_type is EventType.UNRESOLVED
    assert found.event_status is EventStatus.UNKNOWN
    assert [item.field for item in found.context_facts] == [
        "data.warning", "data.max_temp_c", "data.min_temp_c", "data.station_id"
    ]
    assert all(item.review is ReviewState.ACCEPTED for item in found.context_facts)
    assert found.validation.state is ReviewState.ACCEPTED
    assert {type(item).__name__ for item in found.context_facts} == {"ContextFact"}


def test_a_fact_that_states_a_value_its_field_does_not_hold_is_left_for_a_person():
    record = market_price(min_price=55.0, max_price=60.0)
    found = run(
        {
            "record_kind": "context",
            "context_type": "market_price",
            "context_facts": [
                {"field": "data.min_price", "value": "55.0", "quote": "55.0", "char_start": 0,
                 "confidence": 0.9},
                {"field": "data.max_price", "value": "₹60 per kilogram", "quote": "60.0",
                 "char_start": 0, "confidence": 0.9},
            ],
        },
        record,
    )

    assert found.context_facts[0].review is ReviewState.ACCEPTED
    assert found.context_facts[1].review is ReviewState.REVIEW_REQUIRED
    assert found.context_facts[1].evidence_ids
    assert "the value is not what the field it names holds" in found.context_facts[1].notes
    issue = next(
        item for item in found.validation.issues if item.field == "context_facts[fact-2]"
    )
    assert issue.code is IssueCode.UNSUPPORTED_CLAIM
    assert "is not a value 'data.max_price' carries" in issue.detail
    assert found.validation.state is ReviewState.REVIEW_REQUIRED
    assert "context_facts[fact-2]" in found.unaccepted_fields()
    assert found.record_kind is RecordKind.CONTEXT


def test_a_fact_naming_a_field_the_record_does_not_print_cites_no_evidence():
    record = market_price()
    found = run(
        {
            "record_kind": "context",
            "context_type": "market_price",
            "context_facts": [
                {"field": "data.avg_price", "value": "48.0", "quote": "48.0", "char_start": 0},
                {"field": "data.commodity", "quote": "Tomato", "char_start": 0},
            ],
        },
        record,
    )

    assert found.context_facts[0].review is ReviewState.UNRESOLVED
    assert found.context_facts[0].evidence_ids == []
    unknown = next(
        item for item in found.validation.issues if item.field == "context_facts[fact-1]"
    )
    assert unknown.code is IssueCode.UNKNOWN_FIELD
    assert len(found.context_facts) == 1
    valueless = next(
        item for item in found.validation.issues
        if item.field == "context_facts[fact-2]" and item.code is IssueCode.UNSUPPORTED_CLAIM
    )
    assert "with no value preserves nothing" in valueless.detail
    assert found.validation.state is ReviewState.UNRESOLVED


def test_quoted_field_values_with_no_evidenced_event_are_read_as_a_contextual_record():
    record = market_price(min_price=55.0)
    found = run(
        {
            "context_facts": [
                {"field": "data.commodity", "value": "Tomato", "quote": "Tomato",
                 "char_start": 0, "confidence": 0.9},
                {"field": "data.min_price", "value": "55.0", "quote": "55.0", "char_start": 0,
                 "confidence": 0.9},
            ]
        },
        record,
    )

    assert found.record_kind is RecordKind.CONTEXT
    assert found.context_type is None
    assert found.validation.state is ReviewState.ACCEPTED
    assert any(
        "named no record_kind" in warning and "filed as context" in warning
        for warning in found.generation.warnings
    )


def test_one_classification_the_evidence_could_not_support_leaves_an_incident_an_incident():
    """Rule 8: the kind is settled by the event, not by how every field after it read."""
    record = waterlogging()
    content = record["data"]["content"]
    account = water_answer()
    refused = run(
        account
        | {
            "incident_type": {"value": "urban_waterlogging", "field": CONTENT,
                              "quote": "வெள்ள பாதுகாப்பு நடவடிக்கை", "char_start": 0,
                              "confidence": 0.6},
            "context_facts": [{"field": CONTENT, "value": "நேற்று மாலை",
                               "quote": "நேற்று மாலை",
                               "char_start": content.index("நேற்று மாலை"), "confidence": 0.9}],
        },
        record,
    )

    assert refused.record_kind is RecordKind.INCIDENT
    assert refused.incident_type is EventType.UNRESOLVED
    assert refused.description == RAIN
    assert refused.severity is SeverityLevel.MODERATE
    assert refused.event_status is EventStatus.ONGOING
    assert refused.claim("incident_type").review is ReviewState.UNRESOLVED
    assert refused.context_facts[0].review is ReviewState.ACCEPTED
    mismatch = next(
        item for item in refused.validation.issues if item.field == "incident_type"
    )
    assert mismatch.code is IssueCode.SPAN_MISMATCH
    assert refused.validation.state is ReviewState.REVIEW_REQUIRED

    omitted = run(
        {key: value for key, value in account.items() if key != "incident_type"}, record
    )
    assert omitted.record_kind is RecordKind.INCIDENT
    assert omitted.incident_type is EventType.UNRESOLVED
    assert omitted.validation.state is ReviewState.REVIEW_REQUIRED
    warning = next(
        item
        for item in omitted.validation.issues
        if item.field == "incident_type" and item.code is IssueCode.PROVIDER_WARNING
    )
    assert "the kind stands and the type is left for a person" in warning.detail


def test_an_incident_without_its_clock_or_its_urgency_says_why_both_stay_null():
    """A null the dashboard reads is a finding the pipeline owns, not a silent gap."""
    record = waterlogging()
    answer = {
        key: value
        for key, value in water_answer().items()
        if key not in ("event_time", "priority", "event_status")
    }
    found = run(answer, record)

    assert found.event_time is None
    assert found.priority is None
    assert found.event_status is EventStatus.UNKNOWN
    assert found.validation.state is ReviewState.ACCEPTED
    said = " ".join(found.generation.warnings)
    assert "event_time stays unresolved" in said
    assert "priority is left" in said


def test_the_prompt_holds_the_stage_2_7_rules_for_facts_and_for_field_independence():
    request = extraction_request(build_context(waterlogging()))
    rules = request.system.split("Rules:\n")[1]
    assert "Read the three classification fields as three different questions" in rules
    assert "One body sentence may carry all three and one quote may support all three" in rules
    assert "a subject being police, fraud or disaster carries no band" in rules
    assert "`info` is for a record that itself presents the matter as informational" in rules
    assert "priority is how soon the district must act" in rules
    assert "null when nothing says the clock matters" in rules
    assert "not from the feed that carried the record, and not from the kind of event" in rules
    assert "The kind is settled by the event, not by the fields that follow it" in rules
    assert "one unreadable field does not undo an evidenced happening" in rules
    assert "A fact copies and never interprets" in rules
    assert "Two to eight, the most informative first" in rules
    assert "Abstain one field at a time" in rules

    assert "context_facts" in rules
    assert "context_facts" in ExtractionDraft.model_fields


PROBE_TITLE = "ரூ.45 லட்சம் முதலீட்டு மோசடி"
FRAUD_ACT = "ரூ.45 லட்சத்தை ஒரு தனியார் நிறுவனம் மோசம் செய்தது"
PROBE_WATCH = "கண்காணிப்பு குழுவினர்"
PROBE_CASE = "வழக்கு பதிவு செய்து"


def probe_record() -> dict[str, Any]:
    return synth(INVEST_TAMIL, title=PROBE_TITLE, language="ta")


def probe_answer() -> dict[str, Any]:
    """The Melur economic-offences case as one live answer read it: the fraud, the case registered
    and the wing still investigating are all in the body, yet the answer filed the record as an
    administrative notice and kept the money as a value."""
    return {
        "record_kind": "context",
        "context_type": "administrative_notice",
        "title": {"value": PROBE_TITLE, "field": "title"},
        "description": {
            "value": "A private firm took Rs 45 lakh from 150 families on a promise of doubling it.",
            "field": CONTENT,
            "quote": FRAUD_ACT,
            "char_start": INVEST_TAMIL.index(FRAUD_ACT),
            "confidence": 0.8,
        },
        "event_status": quote(INVEST_TAMIL, "விசாரிக்கின்றனர்") | {"value": "under_investigation"},
        "department": quote(INVEST_TAMIL, PROBE_WATCH) | {"value": "police"},
        "context_facts": [quote(INVEST_TAMIL, "150 குடும்பங்களிடமிருந்து")],
    }


def test_a_happening_the_answer_labelled_context_is_still_read_as_an_incident():
    """Stage 2.8 ordering: an evidenced occurrence outranks quoted field values and a wrong label."""
    found = run(probe_answer(), probe_record())

    assert found.record_kind is RecordKind.INCIDENT
    assert found.context_type is None
    assert found.event_status is EventStatus.UNDER_INVESTIGATION
    assert found.department is Department.POLICE
    assert found.incident_type is EventType.UNRESOLVED
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.priority is None
    assert found.locations == [] and found.entities == []
    assert found.description.startswith("A private firm took Rs 45 lakh")
    assert [item.field for item in found.context_facts] == [CONTENT]
    assert found.context_facts[0].review is ReviewState.ACCEPTED
    assert any("the context label is dropped" in w for w in found.generation.warnings)
    assert any(
        item.field == "incident_type" and item.code is IssueCode.PROVIDER_WARNING
        for item in found.validation.issues
    )
    assert found.validation.state is ReviewState.REVIEW_REQUIRED


def test_an_evidenced_happening_needs_no_kind_token_from_the_answer_at_all():
    answer = {
        key: value
        for key, value in probe_answer().items()
        if key not in ("record_kind", "context_type")
    }
    found = run(answer, probe_record())

    assert found.record_kind is RecordKind.INCIDENT
    assert found.context_type is None
    assert found.event_status is EventStatus.UNDER_INVESTIGATION
    assert found.context_facts[0].review is ReviewState.ACCEPTED
    assert not any("filed as context" in w for w in found.generation.warnings)


def test_a_happening_keeps_its_kind_when_its_place_and_its_bands_came_back_empty():
    found = run(
        probe_answer()
        | {
            "record_kind": "incident",
            "context_type": None,
            "incident_type": quote(INVEST_TAMIL, "மோசம் செய்தது")
                             | {"value": "cyber_or_financial_fraud"},
            "category": quote(INVEST_TAMIL, PROBE_CASE) | {"value": "law_and_order"},
            "locations": [{"text": "Melur", "location_type": "taluk", "role": "event_location",
                           "field": CONTENT, "quote": "மேலூர்", "char_start": 0,
                           "confidence": 0.7}],
        },
        probe_record(),
    )

    assert found.record_kind is RecordKind.INCIDENT
    assert found.incident_type is EventType.CYBER_OR_FINANCIAL_FRAUD
    assert found.category is IncidentCategory.LAW_AND_ORDER
    assert found.event_status is EventStatus.UNDER_INVESTIGATION
    assert found.locations[0].review is not ReviewState.ACCEPTED
    assert found.severity is SeverityLevel.UNRESOLVED
    assert found.priority is None
    assert found.validation.state is ReviewState.REVIEW_REQUIRED


def test_a_price_line_and_a_forecast_keep_their_kinds_when_their_prose_is_summarised():
    price = market_price(min_price=55.0, max_price=60.0)
    kept = run(
        {
            "record_kind": "context",
            "context_type": "market_price",
            "description": {"value": "Tomato sold at Rs 55 a kg in Anna nagar.",
                            "field": "data.commodity", "quote": "Tomato", "char_start": 0,
                            "confidence": 0.6},
            "category": {"value": "agriculture", "field": "data.commodity", "quote": "Tomato",
                         "char_start": 0, "confidence": 0.7},
            "context_facts": [{"field": "data.min_price", "value": "55.0", "quote": "55.0",
                               "char_start": 0, "confidence": 0.9}],
        },
        price,
    )

    assert kept.record_kind is RecordKind.CONTEXT
    assert kept.context_type is ContextType.MARKET_PRICE
    assert kept.event_status is EventStatus.UNKNOWN
    assert kept.validation.state is ReviewState.ACCEPTED

    forecast = weather_forecast()
    text = forecast["data"]["forecast"]
    said = run(
        {
            "record_kind": "forecast",
            "context_type": "weather_forecast",
            "description": {"value": "Cloud with thunder and light rain over Madurai.",
                            "field": "data.forecast", "quote": text, "char_start": 0,
                            "confidence": 0.7},
            "context_facts": [{"field": "data.max_temp_c", "value": "33.1", "quote": "33.1",
                               "char_start": 0, "confidence": 0.9}],
        },
        forecast,
    )

    assert said.record_kind is RecordKind.FORECAST
    assert said.context_type is ContextType.WEATHER_FORECAST
    assert said.event_status is EventStatus.UNKNOWN


def test_an_event_whose_quotes_are_not_in_the_record_establishes_no_incident():
    found = run(
        {
            "record_kind": "incident",
            "context_type": "general_information",
            "description": {"value": "Police searched the office and seized two laptops.",
                            "field": CONTENT, "quote": "seized two laptops", "char_start": 0,
                            "confidence": 0.8},
            "event_status": {"value": "under_investigation", "field": CONTENT,
                             "quote": "are investigating", "char_start": 0, "confidence": 0.8},
            "incident_type": {"value": "cyber_or_financial_fraud", "field": CONTENT,
                              "quote": "investment fraud", "char_start": 0, "confidence": 0.8},
        },
        probe_record(),
    )

    assert found.record_kind is RecordKind.CONTEXT
    assert found.description is None
    assert found.event_status is EventStatus.UNKNOWN
    assert found.incident_type is EventType.UNRESOLVED
    assert found.context_facts == []
    assert any(
        item.field == "record_kind" and item.code is IssueCode.PROVIDER_WARNING
        for item in found.validation.issues
    )
    assert found.validation.state is ReviewState.UNRESOLVED


def test_the_prompt_asks_whether_something_happened_before_it_asks_for_a_label():
    rules = extraction_request(build_context(waterlogging())).system.split("Rules:\n")[1]

    assert "does this record state that something happened" in rules
    assert "a happening stays a happening whoever wrote about it" in rules
    assert "never file a happening as context" in rules
    assert "never leave an event's own fields blank" in rules
    assert "Nor does the label you print prove anything" in rules
    assert "a price or a temperature written into an account is still a state" in rules
    assert "never a headline and never the body's prose" in rules
    assert "searching into it or has filed a case about it" in rules
    assert "A record that states no happening has no status to report" in rules
    assert "leave the event fields null for those" not in rules
