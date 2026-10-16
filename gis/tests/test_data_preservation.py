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
        "fetch_saved_documents",
        lambda: (
            incidents,
            {"incidents": {"status": "available", "error": None}},
        ),
    )
    monkeypatch.setattr(
        data_loader,
        "fetch_common_records",
        lambda: ([], {"records": {"status": "available", "error": None}}),
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
        "fetch_saved_documents",
        lambda: (
            [incident],
            {"incidents": {"status": "available", "error": None}},
        ),
    )
    monkeypatch.setattr(
        data_loader,
        "fetch_common_records",
        lambda: ([], {"records": {"status": "available", "error": None}}),
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


def test_common_records_keep_identity_source_values_and_map_separately(monkeypatch):
    tamil_news = {
        "record_id": "NEWS-TA-1",
        "source_id": "dinamalar",
        "source_type": "news",
        "record_type": "article",
        "title": "\u0bae\u0ba4\u0bc1\u0bb0\u0bc8 \u0b9a\u0bc6\u0baf\u0bcd\u0ba4\u0bbf",
        "event_time": "2026-10-14T10:00:00+00:00",
        "location": {"raw_text": "Madurai", "district": "Madurai"},
        "data": {"content": "\u0ba4\u0bae\u0bbf\u0bb4\u0bcd \u0b89\u0bb3\u0bcd\u0bb3\u0b9f\u0b95\u0bcd\u0b95\u0bae\u0bcd"},
        "source_url": "https://example.test/news/1",
    }
    market = {
        "record_id": "MARKET-1",
        "source_id": "tn_agri_marketing",
        "source_type": "agriculture",
        "record_type": "market_price",
        "title": "Tomato price - Anaiyur",
        "event_time": "2026-10-14T00:00:00+00:00",
        "location": {"raw_text": "Anaiyur Uzhavar Santhai, Madurai", "district": "Madurai"},
        "data": {"commodity": "Tomato", "market": "Anaiyur", "min_price": 25, "max_price": 30, "unit": "kg"},
        "source_url": "https://example.test/market/1",
    }
    monkeypatch.setattr(
        data_loader,
        "fetch_saved_documents",
        lambda: (
            [persisted_incident()],
            {"incidents": {"status": "available", "error": None}},
        ),
    )
    monkeypatch.setattr(
        data_loader,
        "fetch_common_records",
        lambda: (
            [tamil_news, market],
            {"records": {"status": "available", "error": None}},
        ),
    )

    dataset = data_loader.load_gis_dataset()
    news_record = next(record for record in dataset["all_records"] if record["id"] == "NEWS-TA-1")
    market_record = next(record for record in dataset["all_records"] if record["id"] == "MARKET-1")

    assert news_record["record_kind"] == "context"
    assert news_record["title"] == tamil_news["title"]
    assert news_record["details"]["content"] == tamil_news["data"]["content"]
    assert news_record["source_url"] == tamil_news["source_url"]
    assert not news_record["is_geocoded"]
    assert news_record["location_precision"] == "area_only"
    assert market_record["record_kind"] == "context"
    assert market_record["is_geocoded"]
    assert market_record["coordinate_origin"] == "registry_approximate"
    assert market_record["location_precision"] == "approximate_named_place"
    assert dataset["stats"]["incidentCount"] == 1
    assert dataset["stats"]["commonRecordCount"] == 2
    assert dataset["stats"]["mapRecordCount"] == 3
    assert dataset["stats"]["mapUnmappedCount"] == 1


def test_common_record_data_coordinates_are_validated_and_preserved(monkeypatch):
    record = {
        "record_id": "COORDINATE-1",
        "source_type": "news",
        "source_id": "district_report",
        "record_type": "article",
        "title": "Source coordinates",
        "location": {"raw_text": "Madurai"},
        "data": {"latitude": 9.91, "longitude": 78.12},
        "source_url": "https://example.test/source",
    }
    monkeypatch.setattr(
        data_loader,
        "fetch_saved_documents",
        lambda: ([], {"incidents": {"status": "available", "error": None}}),
    )
    monkeypatch.setattr(
        data_loader,
        "fetch_common_records",
        lambda: ([record], {"records": {"status": "available", "error": None}}),
    )

    mapped = data_loader.load_gis_dataset()["all_records"][0]
    assert mapped["is_geocoded"]
    assert mapped["base_lat"] == 9.91
    assert mapped["base_lng"] == 78.12
    assert mapped["coordinate_origin"] == "source"
    assert mapped["location_precision"] == "source_coordinates"

    record["data"] = {"latitude": 78.12, "longitude": 9.91}
    swapped = data_loader.load_gis_dataset()["all_records"][0]
    assert not swapped["is_geocoded"]
    assert swapped["resolution_status"] == "INVALID_SOURCE_COORDINATES"


def test_gis_does_not_require_source_sample_json_files():
    data_dir = GIS_ROOT / "data"
    assert (data_dir / "madurai_talukas.geojson").is_file()
    assert not any((data_dir / name).exists() for name in (
        "agriculture.json",
        "weather.json",
        "news.json",
    ))
