"""The two reading paths: a record of values is copied, a record of prose is asked about."""

from __future__ import annotations

import pytest

from intelligence import pipeline
from intelligence.context import build_context
from intelligence.contract import ContextType, EventStatus, Incident, IncidentCategory, RecordKind
from intelligence.contract import ReviewState
from intelligence.models.enums import (
    Department,
    EventType,
    ExtractionMethod,
    Modality,
    SeverityLevel,
)
from intelligence.router import Mode, route
from intelligence.spans import compute_field_hash
from intelligence.structured import MODEL, PROVIDER, process_record as process_structured
from intelligence.tests.builders import NeverCalled, Scripted, water_answer
from intelligence.tests.record_fixtures import (
    CYCLONE_WARNING_TEXT,
    as_record_model,
    base_record,
    crop_distress_report,
    market_price,
    waterlogging,
    weather_forecast,
    weather_warning,
)




def facts_of(incident: Incident) -> dict[str, str]:
    return {fact.field: fact.value for fact in incident.context_facts}


def test_agriculture_and_weather_records_are_read_without_a_model():
    assert route(market_price()) is Mode.STRUCTURED
    assert route(weather_forecast()) is Mode.STRUCTURED
    assert route(weather_warning()) is Mode.STRUCTURED
    assert route(as_record_model(market_price())) is Mode.STRUCTURED


def test_prose_and_unfamiliar_records_are_read_for_their_meaning():
    assert route(waterlogging()) is Mode.SEMANTIC
    assert route(base_record(record_id="OCR-1", source_type="document",
                             data={"content": "page text"})) is Mode.SEMANTIC

    # Agriculture carries the distress report too: the prose decides, not the feed.
    assert route(crop_distress_report()) is Mode.SEMANTIC
    assert route(dict(market_price(), data={"market": "X", "notes": "line one\nline two"})) \
        is Mode.SEMANTIC
    assert route(dict(market_price(), data={"rows": [{"min_price": 1}]})) is Mode.SEMANTIC
    assert route(dict(market_price(), data={})) is Mode.SEMANTIC
    assert route(dict(market_price(), source_type="")) is Mode.SEMANTIC


def test_a_price_line_keeps_every_value_the_record_holds_without_being_asked_about():
    record = market_price(min_price=55.0, max_price=60.0, commodity="Tomato")
    incident = pipeline.process_record(record, provider=NeverCalled())

    assert incident.record_kind is RecordKind.CONTEXT
    assert incident.context_type is ContextType.MARKET_PRICE
    assert incident.title == record["title"]
    assert facts_of(incident) == {
        "data.market": "Anna nagar",
        "data.market_id": "125",
        "data.commodity": "Tomato",
        "data.quantity": "800.0",
        "data.min_price": "55.0",
        "data.max_price": "60.0",
        "data.unit": "kg",
        "data.district_id": "21",
    }
    assert incident.event_time is not None


def test_a_forecast_keeps_its_own_words_and_numbers():
    record = weather_forecast()
    incident = process_structured(record)

    assert incident.record_kind is RecordKind.FORECAST
    assert incident.context_type is ContextType.WEATHER_FORECAST
    assert facts_of(incident)["data.forecast"] == record["data"]["forecast"]
    assert facts_of(incident)["data.max_temp_c"] == "33.1"
    assert facts_of(incident)["data.station_id"] == "MDU"


def test_a_field_the_record_left_empty_or_null_is_not_filled_in():
    incident = process_structured(weather_warning())

    assert "data.warning" in facts_of(incident)
    assert "data.forecast" not in facts_of(incident)
    assert "data.relative_humidity_0830" not in facts_of(incident)
    assert incident.provenance.declared_severity is None


def test_no_incident_is_read_into_a_record_that_stated_no_event():
    incident = process_structured(market_price(max_price=1004.0))

    assert incident.incident_type is EventType.UNRESOLVED
    assert incident.category is IncidentCategory.UNRESOLVED
    assert incident.department is Department.UNRESOLVED
    assert incident.severity is SeverityLevel.UNRESOLVED
    assert incident.priority is None
    assert incident.event_status is EventStatus.UNKNOWN
    assert incident.description is None
    assert (incident.locations, incident.entities, incident.relationships) == ([], [], [])


def test_every_accepted_fact_points_at_the_field_it_copies():
    incident = process_structured(weather_forecast())

    assert incident.validation.state is ReviewState.ACCEPTED
    assert incident.review_required is False
    assert incident.validation.validator == "intelligence.structured"
    for fact in incident.context_facts:
        assert fact.method is ExtractionMethod.SOURCE_METADATA
        assert fact.review is ReviewState.ACCEPTED
        evidence = incident.resolve_evidence(fact.evidence_ids)
        assert [item.field for item in evidence] == [fact.field]
        assert evidence[0].field_text_hash == compute_field_hash(fact.value)
        assert evidence[0].modality is Modality.STRUCTURED_DATA
    assert incident.provenance.modality is Modality.STRUCTURED_DATA


def test_the_structured_result_is_a_contract_incident_that_survives_the_round_trip():
    incident = process_structured(market_price())

    assert Incident.model_validate(incident.to_storage_document()) == incident


def test_the_generation_record_says_no_model_was_used():
    generation = process_structured(weather_forecast()).generation

    assert generation is not None
    assert (generation.provider, generation.model) == (PROVIDER, MODEL)
    assert PROVIDER == "intelligence.structured"
    assert generation.latency_ms is None
    assert any("no language model" in warning for warning in generation.warnings)
    assert generation.input_hash is not None


@pytest.mark.parametrize(
    "record,kind,context_type",
    [
        (market_price(), RecordKind.CONTEXT, ContextType.MARKET_PRICE),
        (weather_forecast(), RecordKind.FORECAST, ContextType.WEATHER_FORECAST),
        (
            weather_warning(record_type="observation"),
            RecordKind.FORECAST,
            ContextType.WEATHER_FORECAST,
        ),
        (
            dict(market_price(), record_type="soil_health_card"),
            RecordKind.CONTEXT,
            ContextType.OTHER,
        ),
    ],
    ids=["market_price", "forecast", "weather_of_an_unlisted_type", "unlisted_type"],
)
def test_the_kind_is_what_the_record_s_own_type_says_it_is(record, kind, context_type):
    incident = process_structured(record)

    assert (incident.record_kind, incident.context_type) == (kind, context_type)


def test_a_structured_record_is_read_from_the_fields_it_actually_carries():
    """No field name is assumed: whatever the feed printed is what is kept."""
    incident = process_structured(
        dict(
            market_price(),
            record_type="rainfall_reading",
            data={"rainfall_mm": 12.4, "gauge": "Auto weather station", "grade": 3},
        )
    )

    assert facts_of(incident) == {
        "data.rainfall_mm": "12.4",
        "data.gauge": "Auto weather station",
        "data.grade": "3",
    }
    assert (incident.record_kind, incident.context_type) == (RecordKind.CONTEXT, ContextType.OTHER)


def test_the_structured_result_carries_the_hash_of_the_fields_it_read():
    record = market_price()

    incident = process_structured(record)

    assert incident.generation.input_hash == build_context(record).context_hash()
    assert incident.provenance.input_hash is not None


def test_a_record_with_nothing_readable_fails_by_name_without_reaching_a_provider():
    outcome = pipeline.run_record(
        dict(market_price(), title="", data={"station_id": 43360}), provider=NeverCalled()
    )

    assert outcome.mode is Mode.STRUCTURED
    assert outcome.failed
    assert "record unreadable" in outcome.error


def test_the_semantic_path_still_reads_a_narrative_record_through_the_provider():
    record = waterlogging()
    assert route(record) is Mode.SEMANTIC

    outcome = pipeline.run_record(record, provider=Scripted(payload=water_answer()))

    assert outcome.mode is Mode.SEMANTIC
    assert outcome.failed is False
    assert outcome.incident.record_kind is RecordKind.INCIDENT
    assert outcome.incident.incident_type is EventType.URBAN_WATERLOGGING
    assert outcome.incident.generation.model == "gpt-oss-120b"
    assert "path=semantic" in outcome.describe()


def test_a_structured_record_reports_its_path_in_the_run(capsys):
    summary = pipeline.RunSummary(base_url="http://backend.test", fetched=1)
    summary.add(
        pipeline.run_record(weather_warning(text=CYCLONE_WARNING_TEXT), provider=NeverCalled())
    )

    pipeline.print_report(summary)

    out = capsys.readouterr().out
    assert "Structured (no model): 1" in out
    assert "Semantic (model read): 0" in out
    assert "path=structured" in out
