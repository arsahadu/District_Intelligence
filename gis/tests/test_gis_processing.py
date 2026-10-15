"""Tests for GIS processing, geographic resolution, dispersion, and GeoJSON output."""

from pathlib import Path
import sys
from fastapi.testclient import TestClient

# Add GIS root to path
GIS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GIS_ROOT))

from app import app
from services.location_resolver import resolve_record_location
from services.geojson_builder import build_geojson_feature_collection
from services.gis_processor import assign_dispersed_coordinates, normalize_record


client = TestClient(app)


def test_location_resolver_uzhavar_santhais():
    """Validates that all 7 Uzhavar Santhais resolve accurately with correct taluks."""
    sample_agri = [
        {"source_type": "agriculture", "data": {"market": "Anna Nagar"}},
        {"source_type": "agriculture", "data": {"market": "Chokkikulam"}},
        {"source_type": "agriculture", "data": {"market": "Palanganatham"}},
        {"source_type": "agriculture", "data": {"market": "Usilampatti"}},
        {"source_type": "agriculture", "data": {"market": "Thirumangalam"}},
        {"source_type": "agriculture", "data": {"market": "Melur"}},
        {"source_type": "agriculture", "data": {"market": "Anaiyur"}},
    ]

    for item in sample_agri:
        res = resolve_record_location(item)
        assert res["is_geocoded"] is True, f"Failed for {item['data']['market']}"
        assert res["lat"] > 9.0 and res["lng"] > 77.0
        assert res["taluk"] in ("Madurai East", "Madurai North", "Madurai South", "Usilampatti", "Thirumangalam", "Melur")


def test_location_resolver_weather_and_news():
    """Validates weather station and localized news points."""
    weather_res = resolve_record_location({
        "source_type": "weather",
        "data": {"station_id": "43360"},
    })
    assert weather_res["is_geocoded"] is True
    assert weather_res["resolved_name"] == "IMD Meteorological Observatory (Station 43360)"

    flood_res = resolve_record_location({
        "source_type": "news",
        "location": {"raw_text": "Nagamalai Pudukkottai, Madurai"},
    })
    assert flood_res["is_geocoded"] is True
    assert flood_res["taluk"] == "Madurai West"


def test_deterministic_co_location_dispersion():
    """Validates that co-located items receive distinct display coordinates without modifying base coordinates."""
    records = [
        normalize_record(_saved_incident(f"incident-{index}"))
        for index in range(3)
    ]
    assign_dispersed_coordinates(records)
    display_points = {(record["display_lat"], record["display_lng"]) for record in records}
    assert len(display_points) == len(records)
    assert all(record["base_lat"] == 9.9179 and record["base_lng"] == 78.1504 for record in records)


def test_geojson_validity():
    """GeoJSON contains only persisted incidents and uses RFC 7946 coordinates."""
    records = [normalize_record(_saved_incident("incident-1"))]
    geojson = build_geojson_feature_collection(records)
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1

    for feat in geojson["features"]:
        assert feat["type"] == "Feature"
        geom = feat["geometry"]
        assert geom["type"] == "Point"
        coords = geom["coordinates"]
        assert len(coords) == 2
        lng, lat = coords
        # GeoJSON is [longitude, latitude]
        assert 77.0 < lng < 79.0, f"Longitude {lng} out of range"
        assert 9.0 < lat < 11.0, f"Latitude {lat} out of range"
        props = feat["properties"]
        assert "id" in props
        assert "title" in props
        assert props["record_kind"] == "incident"


def test_fastapi_endpoints(monkeypatch):
    """The React client APIs expose persisted incidents, not the removed static UI."""
    import app as gis_app_module

    incident = normalize_record(_saved_incident("incident-1"))
    dataset = {
        "incident_records": [incident],
        "all_records": [incident],
        "mapped_records": [incident],
        "unmapped_records": [],
        "stats": {
            "totalRecords": 1,
            "incidentCount": 1,
            "mappedCount": 1,
            "unmappedCount": 0,
            "incidentMappedCount": 1,
            "incidentUnmappedCount": 0,
            "highSeverityCount": 1,
            "mediumSeverityCount": 0,
            "activeTaluksCount": 1,
            "dataSourceType": "live",
            "sourceEndpoints": {"incidents": {"status": "available", "error": None}},
            "lastRefreshed": "2026-10-15T00:00:00+00:00",
        },
        "data_source_type": "live",
        "source_endpoints": {"incidents": {"status": "available", "error": None}},
    }
    monkeypatch.setattr(
        gis_app_module,
        "load_gis_dataset",
        lambda: dataset,
    )
    gis_app_module._cached_dataset = None
    resp_health = client.get("/api/health")
    assert resp_health.status_code == 200
    assert resp_health.json()["status"] == "healthy"
    resp_stats = client.get("/api/stats")
    assert resp_stats.status_code == 200
    assert resp_stats.json()["incidentCount"] == 1
    resp_geo = client.get("/api/geojson")
    assert resp_geo.status_code == 200
    geo_data = resp_geo.json()
    assert geo_data["type"] == "FeatureCollection"
    assert len(geo_data["features"]) == 1
    resp_incidents = client.get("/api/incidents")
    assert resp_incidents.status_code == 200
    assert len(resp_incidents.json()) == 1
    assert resp_incidents.json()[0]["record_kind"] == "incident"
    assert client.get("/api/records").status_code == 404
    assert client.get("/").json()["documentation"] == "/docs"
    gis_app_module._cached_dataset = None


def _saved_incident(incident_id):
    return {
        "schema_version": "2.0",
        "incident_id": incident_id,
        "record_kind": "incident",
        "provenance": {
            "record_id": "source-1",
            "source_id": "district_report",
            "source_type": "news",
        },
        "title": "Reported road flooding",
        "description": "Water collected on the road.",
        "incident_type": "flood",
        "category": "disaster",
        "severity": "high",
        "event_status": "reported",
        "event_time": "2026-10-15T09:00:00+00:00",
        "locations": [{
            "text": "Anna Nagar",
            "latitude": 9.9179,
            "longitude": 78.1504,
        }],
        "evidence": [],
        "validation": {"state": "accepted"},
    }
