from __future__ import annotations

import io
import json
from typing import Any

from intelligence.contract import (
    EventStatus,
    Incident,
    IssueCode,
    RecordKind,
    ReviewState,
    SCHEMA_VERSION,
)
from intelligence.context import build_context
from intelligence.intelligence import PROMPT_VERSION, extract_incident
from intelligence.linking import (
    EVENTS_FILENAME,
    LINKS_FILENAME,
    EventGroup,
    EventLink,
    LinkStore,
    link_incidents,
)
from intelligence.models.enums import ExtractionMethod, ResolutionState, SeverityLevel
from intelligence.models.enums import SpanValidation, TimePrecision
from intelligence.pipeline import POSTABLE_KINDS, Outcome, RunSummary, print_report
from intelligence.router import Mode
from intelligence.structured import MAPPING_VERSION, process_record
from intelligence.tests.builders import CONTENT, STILL_WATCHING, Scripted, water_answer
from intelligence.tests.record_fixtures import market_price, weather_forecast, waterlogging
from intelligence.tests.test_linking import NOW, flood_en, flood_ta, judged, verdict
from intelligence.tests.test_locations import FIRE_TA, VILLAGE, place
from intelligence.tests.test_locations import read as read_place

OUTPUT_FIELDS = (
    "category",
    "claims",
    "context_facts",
    "context_type",
    "department",
    "description",
    "entities",
    "event_status",
    "event_time",
    "event_time_precision",
    "evidence",
    "generation",
    "incident_id",
    "incident_type",
    "locations",
    "priority",
    "provenance",
    "record_kind",
    "relationships",
    "schema_version",
    "severity",
    "source_geography",
    "title",
    "validation",
)

READER = "intelligence.intelligence"
COPIER = "intelligence.structured"

EVENT_FIELDS = ("incident_type", "category", "department", "severity", "event_status", "priority")

ABSENCE = ("unresolved", "unknown")


def unreadable(field: str, value: str) -> dict[str, Any]:
    return {
        "value": value,
        "field": CONTENT,
        "quote": "no such words appear in this article",
        "char_start": 0,
        "confidence": 0.4,
    }


def read(**changes: Any) -> Incident:
    return extract_incident(waterlogging(), provider=Scripted(water_answer() | changes))


def document(incident: Incident) -> dict[str, Any]:
    return incident.to_storage_document()


def outcome(incident: Incident) -> Outcome:
    copied = incident.generation is not None and incident.generation.provider == COPIER
    return Outcome(
        incident.provenance.record_id,
        incident.provenance.source_type,
        str(incident.provenance.record_type),
        incident=incident,
        mode=Mode.STRUCTURED if copied else Mode.SEMANTIC,
    )


def run_of(*incidents: Incident) -> RunSummary:
    items = [outcome(incident) for incident in incidents]
    return RunSummary(
        base_url="http://backend.test",
        fetched=len(items),
        selected=len(items),
        outcomes=items,
    )


def test_no_review_state_in_the_contract_means_confirmed_by_a_person():
    assert {state.value for state in ReviewState} == {
        "accepted",
        "review_required",
        "unresolved",
    }
    assert ExtractionMethod.HUMAN.value == "human"
    assert "human" not in json.dumps(document(read()))
    assert "human" not in json.dumps(document(process_record(market_price())))


def test_an_accepted_reading_says_who_read_it_and_who_checked_it():
    read_doc = document(read())
    assert read_doc["validation"]["validator"] == READER
    assert read_doc["generation"]["provider"] == "scripted"
    assert read_doc["generation"]["model"] == "gpt-oss-120b"
    assert read_doc["generation"]["prompt_version"] == PROMPT_VERSION

    copied = document(process_record(weather_forecast()))
    assert copied["validation"]["validator"] == COPIER
    assert copied["generation"]["provider"] == COPIER
    assert copied["generation"]["model"] == "none"
    assert copied["generation"]["prompt_version"] == MAPPING_VERSION


def test_one_document_shape_carries_an_incident_a_forecast_and_a_price_line():
    for incident in (read(), process_record(weather_forecast()), process_record(market_price())):
        assert sorted(document(incident)) == sorted(OUTPUT_FIELDS)
        assert document(incident)["schema_version"] == SCHEMA_VERSION


def test_an_accepted_incident_and_one_waiting_for_a_person_differ_in_one_field():
    accepted = read()
    waiting = read(severity=unreadable("severity", "high"))

    assert (accepted.review_required, waiting.review_required) == (False, True)
    assert (accepted.validation.state, waiting.validation.state) == (
        ReviewState.ACCEPTED,
        ReviewState.REVIEW_REQUIRED,
    )
    assert accepted.validation.issues == []
    assert [(issue.field, issue.code) for issue in waiting.validation.issues] == [
        ("severity", IssueCode.UNSUPPORTED_CLAIM)
    ]
    assert waiting.severity is SeverityLevel.UNRESOLVED
    assert set(waiting.unaccepted_fields()) - set(accepted.unaccepted_fields()) == {"severity"}


def test_a_field_left_unanswered_does_not_put_the_whole_record_in_review():
    incident = read()
    claim = incident.claim("event_time")

    assert claim is not None and claim.review is ReviewState.UNRESOLVED
    assert incident.validation.state is ReviewState.ACCEPTED
    assert incident.event_time is None


def test_classification_and_severity_appear_only_where_a_quote_supports_them():
    supported = read()
    assert supported.incident_type.value == "urban_waterlogging"
    assert supported.severity is SeverityLevel.MODERATE
    assert supported.claim("severity").review is ReviewState.ACCEPTED

    forecast = process_record(weather_forecast())
    for name in EVENT_FIELDS:
        value = getattr(forecast, name)
        assert value is None or value.value in ABSENCE, name
    assert forecast.validation.state is ReviewState.ACCEPTED


def test_a_forecast_and_a_price_line_are_kept_out_of_the_incident_total():
    incident, forecast, price = read(), process_record(weather_forecast()), process_record(market_price())
    summary = run_of(incident, forecast, price)

    assert [item.incident.record_kind for item in summary.accepted_incidents] == [
        RecordKind.INCIDENT
    ]
    assert [item.incident.record_kind.value for item in summary.accepted_context] == [
        "forecast",
        "context",
    ]
    counts = summary.counts()
    assert counts["Accepted incidents"] == 1
    assert counts["Accepted context"] == 2
    assert sum(summary.buckets().values()) == summary.attempted == 3

    assert [item.incident.incident_id for item in summary.postable] == [
        incident.incident_id,
    ]
    assert RecordKind.UNRESOLVED not in POSTABLE_KINDS


def test_the_report_never_moves_an_accepted_forecast_into_the_incident_block():
    incident, forecast = read(), process_record(weather_forecast())
    stream = io.StringIO()

    print_report(run_of(incident, forecast, read(severity=unreadable("severity", "high"))), stream=stream)

    printed = stream.getvalue()
    assert f"Accepted incidents (1)\n  {incident.incident_id}" in printed
    assert f"Accepted context/forecast (1)\n  {forecast.incident_id}" in printed
    assert "Review-required (1)" in printed
    incident_block = printed.split("Accepted incidents (1)\n")[1].split("\n\n")[0]
    assert forecast.incident_id not in incident_block


def test_every_accepted_claim_resolves_to_a_quote_that_replays_in_the_field_it_cites():
    incident = read()
    context = build_context(waterlogging())

    accepted = [claim for claim in incident.claims if claim.review is ReviewState.ACCEPTED]
    assert accepted
    for claim in accepted:
        rows = incident.resolve_evidence(claim.evidence_ids)
        assert rows, claim.field
        for row in rows:
            assert row.span_validation is SpanValidation.VALIDATED
            text = context.text_of(row.field)
            assert text is not None and text[row.char_start:row.char_end] == row.quote
            assert row.source_url == incident.provenance.source_url
            assert row.retrieved_at == incident.provenance.retrieved_at
            assert row.raw_reference == incident.provenance.raw_reference


def test_a_copied_field_value_is_cited_by_path_url_and_timestamp_instead_of_a_quote():
    price = process_record(market_price())

    assert price.context_facts
    for fact in price.context_facts:
        row = price.evidence_by_id()[fact.evidence_ids[0]]
        assert (row.method, row.quote, row.span_validation) == (
            ExtractionMethod.SOURCE_METADATA,
            None,
            SpanValidation.NOT_APPLICABLE,
        )
        assert row.field == fact.field
        assert fact.confidence is None
        assert row.source_url == price.provenance.source_url
        assert row.retrieved_at == price.provenance.retrieved_at


def test_a_band_or_a_completion_the_feed_printed_about_itself_stays_a_declaration():
    banded = process_record(weather_forecast(severity="Orange Alert", status="COMPLETED"))

    assert (banded.provenance.declared_severity, banded.provenance.declared_status) == (
        "Orange Alert",
        "COMPLETED",
    )
    assert (banded.severity, banded.event_status) == (
        SeverityLevel.UNRESOLVED,
        EventStatus.UNKNOWN,
    )
    assert banded.validation.state is ReviewState.ACCEPTED


def test_a_status_is_reported_only_when_the_record_s_own_words_support_it():
    reported = read()
    claim = reported.claim("event_status")

    assert (claim.review, reported.event_status) == (ReviewState.ACCEPTED, EventStatus.ONGOING)
    assert reported.evidence_by_id()[claim.evidence_ids[0]].quote == STILL_WATCHING

    quiet = read(event_status=unreadable("event_status", "action_taken"))
    assert quiet.event_status is EventStatus.UNKNOWN
    assert [issue.field for issue in quiet.validation.issues] == ["event_status"]


def test_a_day_the_wire_still_wrote_as_a_day_keeps_its_precision_into_the_document():
    day = process_record(weather_forecast(event_time="2026-10-03"))
    flattened = process_record(weather_forecast())

    assert (day.event_time_precision, day.provenance.declared_event_time_precision) == (
        TimePrecision.DAY,
        TimePrecision.DAY,
    )
    assert (
        flattened.event_time_precision,
        flattened.provenance.declared_event_time_precision,
    ) == (TimePrecision.UNKNOWN, TimePrecision.UNKNOWN)
    assert document(day)["event_time_precision"] == "day"
    assert str(day.event_time).startswith("2026-10-03")


def test_a_place_keeps_only_the_levels_the_source_named_and_waits_for_gis():
    supported = read_place([place(FIRE_TA, VILLAGE, village="Karungalakudi")])
    waiting = read_place([place(FIRE_TA, VILLAGE, village="Karungalakudi", taluk="Vadipatti")])

    assert [(item.district, item.taluk, item.village) for item in supported.locations] == [
        (None, None, "Karungalakudi")
    ]
    assert supported.validation.state is ReviewState.ACCEPTED
    assert [(issue.field, issue.code) for issue in waiting.validation.issues] == [
        ("locations[loc-1].taluk", IssueCode.UNSUPPORTED_CLAIM)
    ]
    for incident in (supported, waiting):
        found = incident.locations[0]
        assert found.resolution_state is ResolutionState.PENDING_GIS
        assert (found.latitude, found.longitude, found.canonical_location_id) == (None, None, None)


def test_duplicate_links_come_back_as_documents_of_the_incident_contract_they_linked(tmp_path):
    a, b = flood_ta(), flood_en()
    before = [a.model_dump(mode="json"), b.model_dump(mode="json")]
    _, chain = judged(verdict())

    link_incidents([a, b], chain_factory=chain, store=LinkStore(tmp_path).load(), now=NOW)

    lines = [
        EventLink.model_validate(json.loads(line))
        for line in (tmp_path / LINKS_FILENAME).read_text(encoding="utf-8").splitlines()
    ]
    events = json.loads((tmp_path / EVENTS_FILENAME).read_text(encoding="utf-8"))
    groups = [EventGroup.model_validate(row) for row in events["events"]]

    assert [link.schema_version for link in lines] == [SCHEMA_VERSION]
    assert events["schema_version"] == SCHEMA_VERSION
    assert [group.schema_version for group in groups] == [SCHEMA_VERSION]
    assert groups[0].incident_ids == [a.incident_id, b.incident_id]
    assert groups[0].source_urls == sorted([a.provenance.source_url, b.provenance.source_url])
    assert [a.model_dump(mode="json"), b.model_dump(mode="json")] == before


def test_a_link_decided_under_another_incident_contract_is_decided_again(tmp_path):
    _, chain = judged(verdict())
    found = link_incidents(
        [flood_ta(), flood_en()], chain_factory=chain, store=LinkStore(tmp_path).load(), now=NOW
    )
    stale = json.loads((tmp_path / LINKS_FILENAME).read_text(encoding="utf-8"))
    stale["schema_version"] = "1.0"
    (tmp_path / LINKS_FILENAME).write_text(json.dumps(stale) + "\n", encoding="utf-8")

    _, again = judged(verdict())
    second = link_incidents(
        [flood_ta(), flood_en()], chain_factory=again, store=LinkStore(tmp_path).load(), now=NOW
    )

    assert (second.reused, second.asked) == (0, 1)
    assert [link.link_id for link in second.links] == [link.link_id for link in found.links]
    saved = json.loads((tmp_path / LINKS_FILENAME).read_text(encoding="utf-8"))
    assert saved["schema_version"] == SCHEMA_VERSION
