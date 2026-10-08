"""The GIS resolution boundary: a source's own geography stays source geography until GIS says so."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from intelligence import pipeline
from intelligence.context import build_context
from intelligence.contract import Incident
from intelligence.contract import RecordKind, ReviewState, SourceGeography
from intelligence.models.enums import ExtractionMethod, ResolutionState, SpanValidation
from intelligence.spans import revalidate
from intelligence.structured import MODEL, PROVIDER
from intelligence.structured import process_record as process_structured
from intelligence.tests.builders import NeverCalled, Scripted, water_answer
from intelligence.tests.record_fixtures import (
    market_price,
    weather_forecast,
    waterlogging,
)

GEOGRAPHY_PATHS = ["location.raw_text", "location.district", "location.state"]


def geography_of(incident: Incident) -> dict[str, str]:
    return {item.field: item.value for item in incident.source_geography}


def facts_of(incident: Incident) -> dict[str, str]:
    return {fact.field: fact.value for fact in incident.context_facts}


def test_a_price_line_keeps_the_place_name_and_the_place_identifier_apart():
    incident = pipeline.process_record(market_price(), provider=NeverCalled())

    assert geography_of(incident) == {
        "location.raw_text": "Anna nagar Uzhavar Santhai, Madurai",
        "location.district": "Madurai",
        "location.state": "Tamil Nadu",
    }
    assert facts_of(incident)["data.district_id"] == "21"
    assert facts_of(incident)["data.market_id"] == "125"
    assert facts_of(incident)["data.market"] == "Anna nagar"
    assert incident.locations == []


def test_nothing_is_renamed_and_no_value_is_quietly_changed():
    record = market_price()
    incident = process_structured(record)

    assert geography_of(incident) == dict(zip(GEOGRAPHY_PATHS, record["location"].values()))
    for key, value in record["data"].items():
        assert facts_of(incident)[f"data.{key}"] == str(value)
    assert (incident.generation.provider, incident.generation.model) == (PROVIDER, MODEL)


def test_a_forecast_is_still_read_with_no_model_and_keeps_its_geography():
    record = weather_forecast()
    incident = process_structured(record)

    assert incident.record_kind is RecordKind.FORECAST
    assert incident.validation.state is ReviewState.ACCEPTED
    assert set(geography_of(incident)) == set(GEOGRAPHY_PATHS)
    assert facts_of(incident)["data.station_id"] == "MDU"


def test_a_narrative_record_carries_its_source_geography_beside_pending_locations():
    incident = pipeline.run_record(waterlogging(), provider=Scripted(payload=water_answer())).incident

    assert incident.record_kind is RecordKind.INCIDENT
    assert [item.field for item in incident.source_geography] == GEOGRAPHY_PATHS
    assert [item.value for item in incident.source_geography] == ["Madurai", "Madurai", "Tamil Nadu"]
    for location in incident.locations:
        assert location.resolution_state is ResolutionState.PENDING_GIS
        assert (location.canonical_location_id, location.latitude) == (None, None)


def test_the_record_s_own_geography_is_never_sent_to_the_model():
    context = build_context(market_price())

    assert [item.field for item in context.geography] == GEOGRAPHY_PATHS
    assert not [path for path in context.paths if path.startswith("location.")]
    assert "location.district" not in context.prompt_block()


def test_an_identifier_with_no_place_named_stays_a_bare_identifier():
    incident = process_structured(market_price(district=None))

    assert [item.field for item in incident.source_geography] == ["location.state"]
    assert facts_of(incident)["data.district_id"] == "21"
    assert "Madurai" not in json.dumps(incident.to_storage_document(), ensure_ascii=False)

    assert process_structured(dict(market_price(), location={})).source_geography == []


def test_the_boundary_holds_no_canonical_side_and_no_model_may_fill_it():
    assert set(SourceGeography.model_fields) == {
        "geography_id",
        "field",
        "value",
        "method",
        "confidence",
        "review",
        "evidence_ids",
        "notes",
    }

    for extra in ("canonical_location_id", "latitude", "resolution_state"):
        with pytest.raises(ValidationError):
            SourceGeography.model_validate(
                {
                    "geography_id": "geo-1",
                    "field": "location.district",
                    "value": "Madurai",
                    "evidence_ids": ["ev-1"],
                    extra: "GAZ-1",
                }
            )

    with pytest.raises(ValidationError):
        SourceGeography(
            geography_id="geo-1",
            field="location.district",
            value="Madurai",
            method=ExtractionMethod.LLM,
            confidence=0.9,
            evidence_ids=["ev-1"],
        )


def test_every_source_geography_entry_points_at_the_field_it_copies():
    incident = process_structured(market_price())
    context = build_context(market_price())

    for entry in incident.source_geography:
        evidence = incident.resolve_evidence(entry.evidence_ids)
        assert [item.field for item in evidence] == [entry.field]
        assert evidence[0].method is ExtractionMethod.SOURCE_METADATA
        assert evidence[0].has_span is False
        held = next(field.text for field in context.geography if field.field == entry.field)
        assert entry.value == held
        assert revalidate(evidence[0], held)[1].validation is SpanValidation.NOT_APPLICABLE

    assert Incident.model_validate(incident.to_storage_document()) == incident


def test_the_boundary_is_the_same_on_every_run():
    first = process_structured(market_price())
    second = process_structured(market_price())

    assert [item.model_dump() for item in first.source_geography] == [
        item.model_dump() for item in second.source_geography
    ]
    assert first.generation.input_hash == second.generation.input_hash


def test_a_boundary_entry_that_cites_nothing_or_nothing_real_is_refused():
    with pytest.raises(ValidationError):
        SourceGeography(geography_id="geo-1", field="location.district", value="Madurai")

    payload = process_structured(market_price()).model_dump()
    payload["source_geography"] = [
        dict(payload["source_geography"][0], evidence_ids=["ev-absent"])
    ]
    with pytest.raises(ValidationError):
        Incident.model_validate(payload)
