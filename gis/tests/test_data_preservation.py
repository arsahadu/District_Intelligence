"""Tests for preserving fields from persisted intelligence incidents."""

from pathlib import Path
import sys

GIS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GIS_ROOT))

from services import data_loader


def persisted_incident():
    return {
        "schema_version": "2.0",
        "incident_id": "incident-42",
        "record_kind": "incident",
        "provenance": {
            "record_id": "source-42",
            "source_id": "district_report",
            "source_type": "news",
            "source_url": "https://example.test/report/42",
            "retrieved_at": "2026-10-14T12:00:00+00:00",
        },
        "title": "Road flooding reported in Madurai",
        "description": "Water is blocking the eastern road.",
        "incident_type": "flood",
        "category": "disaster",
        "department": "revenue",
        "severity": "high",
        "priority": "high",
        "event_status": "reported",
        "event_time": "2026-10-14T10:00:00+00:00",
        "locations": [{
            "location_id": "location-42",
            "text": "Anna Nagar",
            "latitude": 9.9179,
            "longitude": 78.1504,
            "resolution_state": "resolved",
        }],
        "evidence": [{
            "evidence_id": "evidence-42",
            "record_id": "source-42",
            "quote": "Water is blocking the eastern road.",
            "source_url": "https://example.test/report/42",
        }],
        "validation": {"state": "accepted", "issues": []},
    }


def test_dataset_contains_only_persisted_incident_documents(monkeypatch):
    forecast = {**persisted_incident(), "incident_id": "forecast-1", "record_kind": "forecast"}
    context = {**persisted_incident(), "incident_id": "context-1", "record_kind": "context"}
    incidents = [persisted_incident()]

    monkeypatch.setattr(
        data_loader,
        "fetch_saved_incidents",
        lambda: (
            incidents,
            {"incidents": {"status": "available", "error": None}},
        ),
    )
    dataset = data_loader.load_gis_dataset()

    assert len(dataset["all_records"]) == 1
    assert dataset["all_records"][0]["record_kind"] == "incident"
    assert dataset["stats"]["totalRecords"] == 1
    assert dataset["stats"]["incidentCount"] == 1
    assert dataset["stats"]["dataSourceType"] == "live"
    assert forecast["record_kind"] != "incident"
    assert context["record_kind"] != "incident"


def test_incident_fields_and_source_evidence_are_preserved(monkeypatch):
    incident = persisted_incident()
    monkeypatch.setattr(
        data_loader,
        "fetch_saved_incidents",
        lambda: (
            [incident],
            {"incidents": {"status": "available", "error": None}},
        ),
    )

    record = data_loader.load_gis_dataset()["all_records"][0]
    assert record["id"] == "incident-42"
    assert record["record_id"] == "source-42"
    assert record["title"] == incident["title"]
    assert record["summary"] == incident["description"]
    assert record["event_time"] == incident["event_time"]
    assert record["severity"] == "high"
    assert record["source_url"] == incident["provenance"]["source_url"]
    assert record["evidence"][0]["evidence_id"] == "evidence-42"
    assert record["base_lat"] == 9.9179
    assert record["base_lng"] == 78.1504


def test_gis_does_not_require_source_sample_json_files():
    data_dir = GIS_ROOT / "data"
    assert (data_dir / "madurai_talukas.geojson").is_file()
    assert not any((data_dir / name).exists() for name in (
        "agriculture.json",
        "weather.json",
        "news.json",
    ))
