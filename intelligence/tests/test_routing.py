from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

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
    TimePrecision,
)
from intelligence.router import Mode, route
from intelligence.spans import compute_field_hash
from intelligence.structured import DATE_ONLY_NOTE, EVENT_TIME_NOTE, MODEL, PROVIDER
from intelligence.structured import process_record as process_structured
from intelligence.tests.builders import NeverCalled, Scripted, water_answer
from intelligence.tests.record_fixtures import (
    CYCLONE_WARNING_TEXT,
    FORECAST_TEXT,
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


def test_a_price_line_from_the_field_keeps_every_value_and_reference_it_carried():
    price = market_price(commodity="Bajji chilli", market="Anaiyur", market_id="131",
                         min_price=70.0, max_price=80.0)
    record = dict(price, data={**price["data"], "quantity": 200.0})
    incident = pipeline.process_record(record, provider=NeverCalled())
    provenance = incident.provenance

    assert facts_of(incident) == {
        "data.commodity": "Bajji chilli",
        "data.market": "Anaiyur",
        "data.market_id": "131",
        "data.min_price": "70.0",
        "data.max_price": "80.0",
        "data.quantity": "200.0",
        "data.unit": "kg",
        "data.district_id": "21",
    }
    assert incident.title == record["title"]
    assert (provenance.record_id, provenance.source_id, provenance.source_type,
            provenance.record_type) == (
        record["record_id"], record["source_id"], record["source_type"], record["record_type"],
    )
    assert provenance.source_url == record["source_url"]
    assert provenance.raw_reference == record["raw_reference"]
    assert provenance.retrieved_at == as_record_model(record).retrieved_at
    assert provenance.declared_event_time == as_record_model(record).event_time
    assert provenance.declared_event_time_precision is TimePrecision.UNKNOWN
    assert provenance.fields_without_value == []
    assert [(item.field, item.value) for item in incident.source_geography] == [
        ("location.raw_text", "Anaiyur Uzhavar Santhai, Madurai"),
        ("location.district", "Madurai"),
        ("location.state", "Tamil Nadu"),
    ]
    assert (incident.validation.state, incident.review_required) == (ReviewState.ACCEPTED, False)


def test_a_forecast_keeps_the_humidity_it_printed_and_the_day_it_was_for():
    record = weather_forecast(event_time="2026-10-03")
    incident = process_structured(record)

    assert facts_of(incident)["data.relative_humidity_0830"] == "71.0"
    assert facts_of(incident)["data.relative_humidity_1730"] == "63.0"
    assert incident.event_time_precision is TimePrecision.DAY
    assert incident.provenance.declared_event_time == datetime(2026, 10, 3)
    assert incident.provenance.declared_event_time_precision is TimePrecision.DAY
    assert incident.provenance.source_url == "https://mausam.imd.gov.in/chennai/693769"
    assert incident.provenance.fields_without_value == ["data.warning"]
    assert "data.warning" not in facts_of(incident)


def test_a_cyclone_warning_is_a_forecast_because_the_source_said_nothing_happened():
    incident = pipeline.process_record(
        weather_warning(text=CYCLONE_WARNING_TEXT), provider=NeverCalled()
    )

    assert incident.record_kind is RecordKind.FORECAST
    assert incident.context_type is ContextType.WEATHER_FORECAST
    assert incident.incident_type is EventType.UNRESOLVED
    assert incident.severity is SeverityLevel.UNRESOLVED
    assert incident.priority is None
    assert incident.event_status is EventStatus.UNKNOWN
    assert incident.description is None
    assert facts_of(incident)["data.warning"] == CYCLONE_WARNING_TEXT
    assert incident.validation.state is ReviewState.ACCEPTED


def test_a_band_and_state_the_source_printed_are_kept_as_declarations():
    incident = process_structured(market_price(severity="Orange Alert", status="UPDATED"))

    assert incident.provenance.declared_severity == "Orange Alert"
    assert incident.provenance.declared_status == "UPDATED"
    assert incident.severity is SeverityLevel.UNRESOLVED
    assert incident.event_status is EventStatus.UNKNOWN
    assert incident.priority is None


@pytest.mark.parametrize(
    "record",
    [
        market_price(),
        market_price(commodity="Bajji chilli", market="Anaiyur", market_id="131",
                     min_price=70.0, max_price=80.0),
        weather_forecast(),
        weather_warning(),
        weather_warning(text=CYCLONE_WARNING_TEXT),
    ],
    ids=["tomato", "bajji", "forecast", "warning", "cyclone"],
)
def test_no_value_a_field_shaped_record_carries_is_lost_and_no_empty_one_is_invented(record):
    incident = pipeline.process_record(record, provider=NeverCalled())
    declared = as_record_model(record)
    provenance = incident.provenance

    assert route(record) is Mode.STRUCTURED
    assert incident.title == declared.title
    assert (provenance.record_id, provenance.source_id, provenance.source_type,
            provenance.record_type) == (
        declared.record_id, declared.source_id, declared.source_type, declared.record_type,
    )
    assert provenance.source_url == declared.source_url == provenance.raw_reference
    assert provenance.retrieved_at == declared.retrieved_at
    assert provenance.declared_severity == declared.severity
    assert provenance.declared_status == declared.status
    assert provenance.declared_event_time == declared.event_time == incident.event_time
    assert provenance.declared_event_time_precision is incident.event_time_precision

    for path, value in record["data"].items():
        if path == "language":
            continue
        if value in (None, ""):
            assert f"data.{path}" not in facts_of(incident)
            assert f"data.{path}" in provenance.fields_without_value
        else:
            assert facts_of(incident)[f"data.{path}"] == str(value)


def test_a_field_the_record_left_empty_or_null_is_not_filled_in():
    incident = process_structured(weather_warning())

    assert "data.warning" in facts_of(incident)
    assert "data.forecast" not in facts_of(incident)
    assert "data.relative_humidity_0830" not in facts_of(incident)
    assert incident.provenance.declared_severity is None
    assert incident.provenance.fields_without_value == [
        "data.forecast",
        "data.relative_humidity_0830",
        "data.relative_humidity_1730",
    ]
    assert all(fact.value not in ("", "None", "null") for fact in incident.context_facts)


def test_no_incident_is_read_into_a_record_that_stated_no_event():
    incident = process_structured(market_price(max_price=1004.0))

    assert (incident.record_kind, incident.context_type) == (
        RecordKind.CONTEXT,
        ContextType.MARKET_PRICE,
    )
    assert facts_of(incident)["data.max_price"] == "1004.0"
    assert incident.incident_type is EventType.UNRESOLVED
    assert incident.category is IncidentCategory.UNRESOLVED
    assert incident.department is Department.UNRESOLVED
    assert incident.severity is SeverityLevel.UNRESOLVED
    assert incident.priority is None
    assert incident.event_status is EventStatus.UNKNOWN
    assert incident.description is None
    assert (incident.entities, incident.relationships) == ([], [])
    assert [item.text for item in incident.locations] == [
        "Anna nagar Uzhavar Santhai, Madurai",
        "Madurai",
    ]


def test_a_forecast_that_stated_only_a_day_is_not_an_event_at_midnight():
    incident = process_structured(weather_forecast(event_time="2026-10-03"))

    assert incident.event_time == datetime(2026, 10, 3, 0, 0)
    assert incident.event_time_precision is TimePrecision.DAY
    claims = {item.field: item for item in incident.claims}
    assert claims["event_time"].notes == DATE_ONLY_NOTE
    assert claims["event_time"].method is ExtractionMethod.SOURCE_METADATA
    assert (incident.record_kind, incident.context_type) == (
        RecordKind.FORECAST,
        ContextType.WEATHER_FORECAST,
    )
    assert facts_of(incident)["data.forecast"] == FORECAST_TEXT


def test_a_forecast_handed_over_as_a_date_object_is_read_as_the_same_day():
    incident = process_structured(weather_forecast(event_time=date(2026, 10, 3)))

    assert incident.event_time == datetime(2026, 10, 3)
    assert incident.event_time_precision is TimePrecision.DAY


def test_a_stamp_that_carries_a_clock_time_is_copied_as_it_stands_and_stays_unasserted():
    incident = process_structured(weather_forecast(event_time="2026-10-03T14:32:05+05:30"))

    assert incident.event_time == datetime(
        2026, 10, 3, 14, 32, 5, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    assert incident.event_time.isoformat() == "2026-10-03T14:32:05+05:30"
    assert incident.event_time_precision is TimePrecision.UNKNOWN
    claims = {item.field: item for item in incident.claims}
    assert claims["event_time"].notes == EVENT_TIME_NOTE

    # what the live feed sends for a forecast day it wrote as a date; guessing it back from a midnight is inference
    flattened = process_structured(weather_forecast(event_time="2026-10-03T00:00:00"))

    assert flattened.event_time == datetime(2026, 10, 3)
    assert flattened.event_time_precision is TimePrecision.UNKNOWN
    assert {item.field: item for item in flattened.claims}["event_time"].notes == EVENT_TIME_NOTE


def test_a_record_that_stated_no_time_is_left_without_one():
    incident = process_structured(weather_forecast(event_time=None))

    assert incident.event_time is None
    assert incident.event_time_precision is TimePrecision.UNKNOWN
    assert incident.provenance.declared_event_time is None
    assert incident.provenance.declared_event_time_precision is None
    assert [item.field for item in incident.claims] == ["title"]
    assert incident.validation.state is ReviewState.ACCEPTED


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


@pytest.mark.parametrize("record", [market_price(), weather_forecast()],
                         ids=["price", "forecast"])
def test_the_structured_result_is_a_contract_incident_that_survives_the_round_trip(record):
    incident = process_structured(record)

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
    summary = pipeline.RunSummary(base_url="http://backend.test", fetched=1, selected=1)
    summary.add(
        pipeline.run_record(weather_warning(text=CYCLONE_WARNING_TEXT), provider=NeverCalled())
    )

    pipeline.print_report(summary)

    out = capsys.readouterr().out
    assert "Structured (no model): 1" in out
    assert "Narrative (model needed): 0" in out
    assert "path=structured" in out
