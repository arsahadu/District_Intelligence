from __future__ import annotations

from typing import Any

from intelligence.contract import (
    Claim,
    ContextFact,
    ContextType,
    Department,
    Entity,
    EventStatus,
    Generation,
    Incident,
    IncidentCategory,
    Location,
    Provenance,
    RecordKind,
    Relationship,
    ReviewState,
    SCHEMA_VERSION,
    SourceGeography,
    Validation,
)
from intelligence.models.enums import (
    EventType,
    ExtractionMethod,
    Modality,
    SeverityLevel,
    SpanValidation,
)
from intelligence.models.evidence import Evidence
from intelligence.pipeline import describe_incident, process_record
from intelligence.spans import compute_field_hash
from intelligence.tests.builders import NeverCalled
from intelligence.tests.record_fixtures import market_price, weather_forecast

HEAVY_RAIN = "Generally cloudy sky with Heavy rain"

LINE_LABELS = (
    "title",
    "kind",
    "context",
    "type",
    "category",
    "department",
    "severity",
    "priority",
    "status",
    "event_time",
    "facts",
    "values",
    "places",
    "review",
)


def beetroot(**changes: Any) -> dict[str, Any]:
    payload = market_price(
        market="Anaiyur",
        market_id="126",
        commodity="Beetroot",
        min_price=40.0,
        max_price=50.0,
        district="Madurai",
    )
    payload["data"]["quantity"] = 100.0
    payload.update(changes)
    return payload


def forecast_18_oct(**changes: Any) -> dict[str, Any]:
    payload = weather_forecast(
        record_id="WEATHER-MDU-18-Oct",
        title="Madurai Weather Forecast - 18-Oct",
        event_time="2026-10-18T00:00:00+00:00",
    )
    payload["data"] = {
        "min_temp_c": 25.0,
        "max_temp_c": 35.0,
        "forecast": "",
        "warning": HEAVY_RAIN,
        "relative_humidity_0830": None,
        "relative_humidity_1730": None,
        "station_id": "43360",
    }
    payload.update(changes)
    return payload


def read(record: dict[str, Any]) -> Incident:
    return process_record(record, provider=NeverCalled())


def labels(line: str) -> list[str]:
    return [part.split("=")[0] for part in line.split(" | ")[1:]]


def test_the_price_summary_names_every_value_the_market_feed_declared():
    found = read(beetroot())
    line = describe_incident(found)

    assert "kind=context | context=market_price" in line
    assert "facts=8" in line
    for pair in (
        "commodity=Beetroot",
        "market=Anaiyur",
        "market_id=126",
        "district_id=21",
        "min_price=40.0",
        "max_price=50.0",
        "quantity=100.0",
        "unit=kg",
    ):
        assert pair in line, pair
    assert "places=Anaiyur Uzhavar Santhai, Madurai;Madurai" in line
    assert "review=accepted(0)" in line
    assert [item.value for item in found.context_facts] == [
        "Beetroot", "21", "Anaiyur", "126", "kg", "50.0", "40.0", "100.0",
    ]


def test_the_forecast_summary_names_the_date_both_temperatures_and_the_warning():
    found = read(forecast_18_oct())
    line = describe_incident(found)

    assert "kind=forecast | context=weather_forecast" in line
    assert "event_time=2026-10-18" in line
    assert found.event_time.strftime("%Y-%m-%d") == "2026-10-18"
    assert f"warning={HEAVY_RAIN}" in line
    assert "max_temp_c=35.0" in line
    assert "min_temp_c=25.0" in line
    assert "station_id=43360" in line
    assert found.provenance.declared_event_time.strftime("%Y-%m-%d") == "2026-10-18"


def test_the_six_fields_a_happening_needs_are_printed_as_not_applicable():
    price = read(beetroot())
    forecast = read(forecast_18_oct())

    for found in (price, forecast):
        line = describe_incident(found)
        assert "type=n/a | category=n/a | department=n/a | severity=n/a | priority=n/a" in line
        assert "status=n/a" in line
        assert "unresolved" not in line
        assert "unknown" not in line
        assert "review=accepted(0)" in line
    assert price.incident_type is EventType.UNRESOLVED
    assert price.category is IncidentCategory.UNRESOLVED
    assert price.department is Department.UNRESOLVED
    assert price.severity is SeverityLevel.UNRESOLVED
    assert price.priority is None
    assert price.event_status is EventStatus.UNKNOWN


def test_an_empty_forecast_and_two_null_humidities_erase_none_of_the_other_facts():
    found = read(forecast_18_oct())
    line = describe_incident(found)

    assert [item.field for item in found.context_facts] == [
        "data.station_id", "data.warning", "data.max_temp_c", "data.min_temp_c",
    ]
    assert sorted(found.provenance.fields_without_value) == [
        "data.forecast", "data.relative_humidity_0830", "data.relative_humidity_1730",
    ]
    assert "facts=4" in line
    assert "humidity" not in line
    assert all(item.review is ReviewState.ACCEPTED for item in found.context_facts)
    assert found.validation.issues == []
    assert found.review_required is False


def test_the_warning_keeps_the_declared_value_evidence_the_structured_path_gives_it():
    found = read(forecast_18_oct())
    fact = next(item for item in found.context_facts if item.field == "data.warning")

    assert (fact.value, fact.review, fact.method) == (
        HEAVY_RAIN, ReviewState.ACCEPTED, ExtractionMethod.SOURCE_METADATA,
    )
    evidence = found.evidence_by_id()[fact.evidence_ids[0]]
    assert evidence.field == "data.warning"
    assert evidence.method is ExtractionMethod.SOURCE_METADATA
    assert evidence.modality is Modality.STRUCTURED_DATA
    assert evidence.quote == HEAVY_RAIN
    assert (evidence.char_start, evidence.char_end) == (0, len(HEAVY_RAIN))
    assert evidence.span_validation is SpanValidation.VALIDATED
    assert evidence.field_text_hash == compute_field_hash(HEAVY_RAIN)


def test_the_market_record_carries_its_district_as_a_declared_place_not_an_evidenced_one():
    found = read(beetroot())

    assert [(item.text, item.district) for item in found.locations] == [
        ("Anaiyur Uzhavar Santhai, Madurai", None), ("Madurai", "Madurai"),
    ]
    for place in found.locations:
        assert place.review is ReviewState.ACCEPTED
        assert place.method is ExtractionMethod.SOURCE_METADATA
        assert place.canonical_location_id is None
        assert (place.latitude, place.longitude) == (None, None)
    assert [(item.field, item.value) for item in found.source_geography] == [
        ("location.raw_text", "Anaiyur Uzhavar Santhai, Madurai"),
        ("location.district", "Madurai"),
        ("location.state", "Tamil Nadu"),
    ]


def test_the_summary_prints_only_labels_the_contract_fields_already_answer_to():
    for record in (beetroot(), forecast_18_oct()):
        line = describe_incident(read(record))

        assert labels(line) == list(LINE_LABELS)
        assert all("=" in part for part in line.split(" | ")[1:])


def test_the_summary_line_prints_the_facts_it_counted_and_nothing_else_the_record_lacks():
    found = read(beetroot())
    line = describe_incident(found)

    printed = line.split(" | values=")[1].split(" | ")[0].split(";")
    assert len(printed) == len(found.context_facts)
    for item, pair in zip(found.context_facts, printed):
        name, _, value = pair.partition("=")
        assert (name, value) == (item.field.rsplit(".", 1)[-1], item.value)
    assert "n/a" in line
    assert "places=Anaiyur Uzhavar Santhai, Madurai;Madurai" in line


def test_printing_a_structured_record_adds_no_key_the_contract_did_not_carry():
    for record in (beetroot(), forecast_18_oct()):
        found = read(record)
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
            ("context_facts", ContextFact),
            ("locations", Location),
            ("source_geography", SourceGeography),
            ("evidence", Evidence),
            ("claims", Claim),
            ("entities", Entity),
            ("relationships", Relationship),
        ):
            for item in doc[name]:
                assert set(item) <= set(model.model_fields), f"{name} grew a key"
        assert doc["context_facts"] and doc["locations"] and doc["evidence"]


def test_a_structured_record_still_needs_no_provider_and_is_filed_as_what_it_is():
    price = read(beetroot())
    forecast = read(forecast_18_oct())

    assert (price.record_kind, price.context_type) == (RecordKind.CONTEXT, ContextType.MARKET_PRICE)
    assert (forecast.record_kind, forecast.context_type) == (
        RecordKind.FORECAST, ContextType.WEATHER_FORECAST,
    )
    for found in (price, forecast):
        assert found.generation.provider == "intelligence.structured"
        assert found.generation.model == "none"
        assert found.validation.state is ReviewState.ACCEPTED
