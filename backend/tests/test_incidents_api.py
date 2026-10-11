import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.intelligence_incident import IncidentEvidence
from app.main import app
from intelligence.contract import (
    Entity,
    EventStatus,
    Generation,
    IncidentCategory,
    Location,
    PriorityLevel,
    Relationship,
    ReviewState,
    SCHEMA_VERSION,
)
from intelligence.contract import EventType
from intelligence.models.enums import ExtractionMethod
from intelligence.tests.test_contract import claim as contract_claim
from intelligence.tests.test_contract import evidence as contract_evidence
from intelligence.tests.test_contract import incident as contract_incident


client = TestClient(app)


def make_incident():
    title = f"Contract-backed incident {uuid.uuid4().hex}"
    event_time = datetime(2026, 10, 3, 8, 30, tzinfo=timezone.utc)
    generated = contract_incident(
        incident_id=f"INC-API-{uuid.uuid4().hex}",
        title=title,
        event_time=event_time,
        incident_type=EventType.URBAN_WATERLOGGING,
        category=IncidentCategory.WATER,
        priority=PriorityLevel.HIGH,
        event_status=EventStatus.REPORTED,
        claims=[
            contract_claim(),
            contract_claim(
                "incident_type",
                EventType.URBAN_WATERLOGGING.value,
                method=ExtractionMethod.SOURCE_METADATA,
                evidence_ids=[],
                review=ReviewState.ACCEPTED,
            ),
            contract_claim(
                "category",
                IncidentCategory.WATER.value,
                method=ExtractionMethod.SOURCE_METADATA,
                evidence_ids=[],
                review=ReviewState.ACCEPTED,
            ),
            contract_claim(
                "priority",
                PriorityLevel.HIGH.value,
                method=ExtractionMethod.SOURCE_METADATA,
                evidence_ids=[],
                review=ReviewState.ACCEPTED,
            ),
            contract_claim(
                "event_status",
                EventStatus.REPORTED.value,
                method=ExtractionMethod.SOURCE_METADATA,
                evidence_ids=[],
                review=ReviewState.ACCEPTED,
            ),
            contract_claim(
                "title",
                title,
                method=ExtractionMethod.SOURCE_METADATA,
                evidence_ids=[],
                review=ReviewState.ACCEPTED,
            ),
            contract_claim(
                "event_time",
                event_time.isoformat(),
                method=ExtractionMethod.SOURCE_METADATA,
                evidence_ids=[],
                review=ReviewState.ACCEPTED,
            ),
        ],
        evidence=[
            contract_evidence(),
            contract_evidence(
                "ev-2",
                record_id="NEWS-OTHER-0001",
                source_id="regional-feed",
                source_url="https://regional.example.test/report",
            ),
        ],
        locations=[
            Location(
                location_id="loc-madurai",
                text="Madurai",
                district="Madurai",
                review=ReviewState.ACCEPTED,
                evidence_ids=["ev-1"],
            )
        ],
        entities=[
            Entity(
                entity_id="entity-authority",
                text="District administration",
                review=ReviewState.ACCEPTED,
                evidence_ids=["ev-1"],
            )
        ],
        relationships=[
            Relationship(
                relationship_id="relationship-located-at",
                from_ref="entity-authority",
                to_ref="loc-madurai",
                review=ReviewState.ACCEPTED,
                evidence_ids=["ev-1"],
            )
        ],
        generation=Generation(
            provider="test-provider",
            model="contract-test",
            prompt_version="test-v1",
        ),
    )
    return generated


def test_incident_contract_round_trips_with_evidence_and_provenance():
    incident = make_incident()
    payload = incident.to_storage_document()

    created = client.post("/incidents", json=payload)
    assert created.status_code == 201, created.text
    assert created.json() == payload

    listed = client.get("/incidents")
    assert listed.status_code == 200, listed.text
    assert payload in listed.json()

    fetched = client.get(f"/incidents/{incident.incident_id}")
    assert fetched.status_code == 200, fetched.text
    document = fetched.json()
    assert document["incident_id"] == incident.incident_id
    assert document["schema_version"] == SCHEMA_VERSION
    assert document["title"] == incident.title
    assert document["severity"] == "high"
    assert document["priority"] == "high"
    assert document["event_status"] == incident.event_status.value
    assert document["event_time"] == payload["event_time"]
    assert document["provenance"] == payload["provenance"]
    assert document["evidence"] == payload["evidence"]
    assert document["locations"] == payload["locations"]
    assert document["entities"] == payload["entities"]
    assert document["relationships"] == payload["relationships"]
    assert document["claims"] == payload["claims"]
    assert document["validation"] == payload["validation"]
    assert document["generation"] == payload["generation"]
    with SessionLocal() as db:
        stored_evidence = db.execute(
            select(IncidentEvidence)
            .where(IncidentEvidence.incident_id == incident.incident_id)
            .order_by(IncidentEvidence.evidence_id)
        ).scalars().all()
        assert len(stored_evidence) == len(incident.evidence)
        assert {item.record_id for item in stored_evidence} == {
            item.record_id for item in incident.evidence
        }
        assert {item.source_id for item in stored_evidence} == {
            item.source_id for item in incident.evidence
        }
        assert {item.source_url for item in stored_evidence} == {
            item.source_url for item in incident.evidence
        }
        assert {item.source_field for item in stored_evidence} == {
            item.field for item in incident.evidence
        }
        assert {item.quote for item in stored_evidence} == {
            item.quote for item in incident.evidence
        }
        assert {item.char_start for item in stored_evidence} == {
            item.char_start for item in incident.evidence
        }


def test_duplicate_incident_id_returns_conflict_without_overwriting():
    incident = make_incident()
    payload = incident.to_storage_document()

    first = client.post("/incidents", json=payload)
    second = client.post("/incidents", json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == f"Incident {incident.incident_id!r} already exists"


def make_forecast_payload():
    payload = {
        "schema_version": SCHEMA_VERSION,
        "incident_id": f"FORECAST-API-{uuid.uuid4().hex}",
        "record_kind": "forecast",
        "context_type": "weather_forecast",
        "title": "Madurai weather forecast",
        "description": "Scattered showers are expected later in the day.",
        "incident_type": "unresolved",
        "category": "unresolved",
        "department": "unresolved",
        "severity": "info",
        "priority": None,
        "event_status": "unknown",
        "event_time": "2026-10-11T06:00:00+00:00",
        "event_time_precision": "day",
        "context_facts": [
            {"field": "forecast_date", "value": "2026-10-11"},
            {"field": "station_id", "value": "MDU-01"},
            {"field": "sky_description", "value": "cloudy with light rain"},
            {"field": "temperature_min_c", "value": "23"},
            {"field": "temperature_max_c", "value": "31"},
        ],
        "provenance": {
            "record_id": "WEATHER-MDU-11-Oct",
            "source_id": "imd",
            "source_type": "weather",
            "source_url": "https://example.test/weather/WEATHER-MDU-11-Oct",
            "retrieved_at": "2026-10-11T08:00:00+00:00",
            "declared_event_time": "2026-10-11T06:00:00+00:00",
            "declared_event_time_precision": "day",
            "fields_without_value": ["temperature_max_c", "sky_description"],
        },
        "claims": [],
        "evidence": [],
        "validation": {"state": "accepted", "issues": [], "validator": "intelligence.intelligence"},
    }
    return payload


def make_market_context_payload():
    payload = {
        "schema_version": SCHEMA_VERSION,
        "incident_id": f"CONTEXT-API-{uuid.uuid4().hex}",
        "record_kind": "context",
        "context_type": "market_price",
        "title": "Orange market prices",
        "description": "Market reference for orange prices in Madurai.",
        "incident_type": "unresolved",
        "category": "unresolved",
        "department": "unresolved",
        "severity": "info",
        "priority": None,
        "event_status": "unknown",
        "event_time": "2026-10-11T10:30:00+00:00",
        "event_time_precision": "day",
        "context_facts": [
            {"field": "commodity", "value": "orange"},
            {"field": "market", "value": "Madurai"},
            {"field": "unit", "value": "kg"},
            {"field": "minimum_price", "value": "120"},
            {"field": "maximum_price", "value": "140"},
            {"field": "quantity", "value": "500"},
        ],
        "provenance": {
            "record_id": "AGRI-MDU-ANAIYUR-ORANGE-2026-10-11",
            "source_id": "agri-market",
            "source_type": "agriculture",
            "source_url": "https://example.test/agri/AGRI-MDU-ANAIYUR-ORANGE-2026-10-11",
            "retrieved_at": "2026-10-11T10:45:00+00:00",
            "declared_event_time": "2026-10-11T10:30:00+00:00",
            "declared_event_time_precision": "minute",
            "fields_without_value": ["unit"],
        },
        "claims": [],
        "evidence": [],
        "validation": {"state": "accepted", "issues": [], "validator": "intelligence.intelligence"},
    }
    return payload


def test_forecast_and_market_context_documents_round_trip_with_extended_provenance():
    forecast = make_forecast_payload()
    created = client.post("/incidents", json=forecast)
    assert created.status_code == 201, created.text
    assert created.json() == forecast

    fetched = client.get(f"/incidents/{forecast['incident_id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["provenance"] == forecast["provenance"]
    assert fetched.json()["context_facts"] == forecast["context_facts"]
    assert fetched.json()["record_kind"] == "forecast"
    assert fetched.json()["context_type"] == "weather_forecast"

    market = make_market_context_payload()
    created_context = client.post("/incidents", json=market)
    assert created_context.status_code == 201, created_context.text
    assert created_context.json() == market

    fetched_context = client.get(f"/incidents/{market['incident_id']}")
    assert fetched_context.status_code == 200, fetched_context.text
    assert fetched_context.json()["provenance"] == market["provenance"]
    assert fetched_context.json()["context_facts"] == market["context_facts"]
    assert fetched_context.json()["record_kind"] == "context"
    assert fetched_context.json()["context_type"] == "market_price"


def test_invalid_or_unsupported_incident_contract_is_rejected():
    incident = make_incident()
    invalid = incident.to_storage_document()
    invalid["made_up_field"] = True
    assert client.post("/incidents", json=invalid).status_code == 422

    unsupported = incident.to_storage_document()
    unsupported["schema_version"] = "1.0"
    response = client.post("/incidents", json=unsupported)
    assert response.status_code == 422
    assert "Unsupported Incident schema_version" in response.text

    missing_provenance = make_forecast_payload()
    missing_provenance.pop("provenance")
    assert client.post("/incidents", json=missing_provenance).status_code == 422


def test_incident_filters_use_contract_values_and_calendar_dates():
    incident = make_incident()
    payload = incident.to_storage_document()
    created = client.post("/incidents", json=payload)
    assert created.status_code == 201, created.text

    response = client.get(
        "/incidents",
        params={
            "incident_type": "urban_waterlogging",
            "category": "water",
            "severity": "high",
            "priority": "high",
            "event_status": "reported",
            "district": "Madurai",
            "start_date": "2026-10-03",
            "end_date": "2026-10-03",
        },
    )
    assert response.status_code == 200, response.text
    assert payload in response.json()

    no_matches = client.get(
        "/incidents", params={"district": "Other", "start_date": "2026-10-04"}
    )
    assert no_matches.status_code == 200
    assert payload not in no_matches.json()

    assert client.get(
        "/incidents", params={"start_date": "2099-01-01", "end_date": "2000-01-01"}
    ).status_code == 422


def test_missing_incident_returns_not_found():
    response = client.get(f"/incidents/INC-MISSING-{uuid.uuid4().hex}")
    assert response.status_code == 404
