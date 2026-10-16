"""Regression tests for the live GIS/backend data contract."""

from pathlib import Path
import sys

import pytest

GIS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GIS_ROOT))

import services.data_loader as data_loader
from services.geojson_builder import build_geojson_feature_collection
from services.gis_processor import assign_dispersed_coordinates, normalize_record
from services.location_resolver import resolve_record_location


def common_record(record_id="record-1", source_type="weather", **overrides):
    record = {
        "record_id": record_id,
        "source_id": "imd_station",
        "source_type": source_type,
        "record_type": "forecast",
        "title": "Forecast for Madurai",
        "event_time": "2026-10-15T09:00:00+00:00",
        "location": {"raw_text": "Madurai", "district": "Madurai", "state": "Tamil Nadu"},
        "data": {"station_id": "43360", "warning": "Cloudy"},
        "severity": None,
        "status": None,
        "source_url": "https://example.test/source",
        "retrieved_at": "2026-10-15T10:00:00+00:00",
        "raw_reference": "raw-1",
    }
    record.update(overrides)
    return record


def saved_incident():
    return {
        "schema_version": "2.0",
        "incident_id": "incident-1",
        "record_kind": "incident",
        "provenance": {
            "record_id": "record-1",
            "source_id": "imd_station",
            "source_type": "weather",
            "source_url": "https://example.test/source",
        },
        "title": "Saved rainfall incident",
        "description": "Rainfall disruption reported.",
        "incident_type": "flood",
        "category": "disaster",
        "department": "revenue",
        "severity": "high",
        "priority": "high",
        "event_status": "reported",
        "event_time": "2026-10-15T09:00:00+00:00",
        "locations": [{
            "location_id": "loc-1",
            "text": "Madurai",
            "latitude": 9.91,
            "longitude": 78.12,
            "resolution_state": "resolved",
        }],
        "evidence": [{
            "evidence_id": "ev-1",
            "record_id": "record-1",
            "source_id": "imd_station",
            "source_type": "weather",
            "field": "data.warning",
            "quote": "Cloudy with heavy rain.",
            "source_url": "https://example.test/evidence",
        }],
        "validation": {"state": "review_required", "issues": []},
    }


def test_gis_fetches_persisted_incidents_only_and_preserves_provenance(monkeypatch):
    calls = []
    payload = [
        saved_incident(),
        {**saved_incident(), "incident_id": "forecast-1", "record_kind": "forecast"},
        {**saved_incident(), "incident_id": "context-1", "record_kind": "context"},
    ]

    class Response:
        status_code = 200

        def json(self):
            return payload

    def backend_get(url, timeout):
        calls.append((url, timeout))
        if url.endswith("/incidents"):
            return Response()
        assert url.endswith("/records")

        class RecordsResponse:
            status_code = 200

            @staticmethod
            def json():
                return [common_record()]

        return RecordsResponse()

    monkeypatch.setattr(data_loader.requests, "get", backend_get)
    incidents, status = data_loader.fetch_saved_incidents()
    assert [item["incident_id"] for item in incidents] == ["incident-1"]
    assert len(calls) == 1
    assert all(timeout == data_loader.BACKEND_TIMEOUT_SECONDS for _, timeout in calls)
    assert status["incidents"]["status"] == "available"

    monkeypatch.setattr(data_loader, "fetch_saved_documents", lambda: (payload, status))
    monkeypatch.setattr(
        data_loader,
        "fetch_common_records",
        lambda: ([common_record()], {"records": {"status": "available", "error": None}}),
    )
    dataset = data_loader.load_gis_dataset()
    incident = dataset["incident_records"][0]
    assert incident["id"] == "incident-1"
    assert incident["record_id"] == "record-1"
    assert incident["source_url"] == "https://example.test/source"
    assert incident["evidence"][0]["evidence_id"] == "ev-1"
    assert incident["severity"] == "high"
    assert incident["status"] == "reported"
    assert incident["review_required"] is True
    assert dataset["stats"]["incidentCount"] == 1
    assert dataset["stats"]["totalRecords"] == 1
    assert dataset["stats"]["highSeverityCount"] == 1
    assert len(dataset["all_records"]) == 2
    assert [record["record_kind"] for record in dataset["all_records"]].count("incident") == 1
    assert dataset["context_records"][0]["intelligence_outcomes"] == [
        {
            "status": "needs_review",
            "incident_id": "incident-1",
            "title": "Saved rainfall incident",
            "record_kind": "incident",
        },
        {
            "status": "information_only",
            "incident_id": "context-1",
            "title": "Saved rainfall incident",
            "record_kind": "context",
        },
    ]
    assert dataset["stats"]["commonRecordCount"] == 1
    assert dataset["stats"]["incidentCount"] == 1


def test_empty_incident_api_results_produce_empty_gis_dataset(monkeypatch):
    monkeypatch.setattr(
        data_loader,
        "fetch_saved_documents",
        lambda: ([], {"incidents": {"status": "available", "error": None}}),
    )
    monkeypatch.setattr(
        data_loader,
        "fetch_common_records",
        lambda: ([], {"records": {"status": "available", "error": None}}),
    )

    dataset = data_loader.load_gis_dataset()
    assert dataset["all_records"] == []
    assert dataset["stats"]["totalRecords"] == 0
    assert dataset["stats"]["dataSourceType"] == "live"


def test_unavailable_incident_api_is_reported_without_fallback(monkeypatch):
    monkeypatch.setattr(
        data_loader,
        "fetch_saved_documents",
        lambda: (_ for _ in ()).throw(
            data_loader.GISDataUnavailable("backend is not reachable")
        ),
    )

    with pytest.raises(data_loader.GISDataUnavailable, match="not reachable"):
        data_loader.load_gis_dataset()


def test_unknown_source_type_is_retained_without_becoming_news():
    record = normalize_record(common_record(
        source_type="irrigation_board",
        record_type="notice",
        data={"notice": "Canal maintenance"},
    ))
    assert record["source_type"] == "irrigation_board"
    assert record["category"] == "other"
    assert record["record_kind"] == "context"
    assert record["severity"] is None


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        ("north", 78.1),
        (float("nan"), 78.1),
        (91, 78.1),
        (9.9, 181),
    ],
)
def test_invalid_coordinates_are_unmapped(latitude, longitude):
    resolution = resolve_record_location({
        "source_type": "news",
        "location": {
            "raw_text": "Anna Nagar",
            "latitude": latitude,
            "longitude": longitude,
        },
    })
    assert resolution["is_geocoded"] is False
    assert resolution["resolution_status"] == "INVALID_SOURCE_COORDINATES"


def test_taluk_only_location_does_not_receive_a_centroid():
    result = resolve_record_location({
        "source_type": "news",
        "location": {"raw_text": "Melur Taluk"},
    })
    assert result["is_geocoded"] is False
    assert result["resolution_status"] == "UNRESOLVED_AREA_ONLY"
    assert result["lat"] is None and result["lng"] is None


def test_geojson_uses_longitude_latitude_and_stable_visual_offsets():
    records = [
        normalize_record(common_record(
            record_id=record_id,
            location={
                "raw_text": "Anna Nagar",
                "latitude": 9.9179,
                "longitude": 78.1504,
            },
        ))
        for record_id in ("record-b", "record-a")
    ]
    assign_dispersed_coordinates(records)
    initial = {
        item["id"]: (item["display_lat"], item["display_lng"], item["base_lat"], item["base_lng"])
        for item in records
    }
    assign_dispersed_coordinates(records)
    assert initial == {
        item["id"]: (item["display_lat"], item["display_lng"], item["base_lat"], item["base_lng"])
        for item in records
    }
    assert all(item["base_lat"] == 9.9179 and item["base_lng"] == 78.1504 for item in records)
    collection = build_geojson_feature_collection(records)
    assert collection["type"] == "FeatureCollection"
    for feature in collection["features"]:
        longitude, latitude = feature["geometry"]["coordinates"]
        assert longitude == feature["properties"]["display_lng"]
        assert latitude == feature["properties"]["display_lat"]
        assert feature["id"] in {"context:record-a", "context:record-b"}


def test_backend_list_contract_rejects_wrapped_payloads(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"records": []}

    monkeypatch.setattr(data_loader.requests, "get", lambda *_args, **_kwargs: Response())
    with pytest.raises(data_loader.GISDataUnavailable, match="JSON list"):
        data_loader.fetch_saved_incidents()


def test_dataset_reload_invalidates_cache(monkeypatch):
    import app as gis_app_module

    loads = []

    def load():
        value = {"load": len(loads) + 1}
        loads.append(value)
        return value

    monkeypatch.setattr(gis_app_module, "load_gis_dataset", load)
    gis_app_module._cached_dataset = None
    assert gis_app_module.get_dataset() == {"load": 1}
    assert gis_app_module.get_dataset(force_reload=True) == {"load": 2}
    assert len(loads) == 2
    gis_app_module._cached_dataset = None
