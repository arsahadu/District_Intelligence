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


def test_ambiguous_or_unregistered_places_remain_unmapped_without_centroid_guessing():
    locations = [
        {"raw_text": "Madurai", "district": "Madurai"},
        {"raw_text": "Peraiyur", "district": "Madurai"},
        {"raw_text": "Manamadurai", "district": "Sivaganga"},
        {"raw_text": "Tambaram", "district": "Chengalpattu"},
    ]
    results = [
        resolve_record_location({"source_type": "news", "location": location})
        for location in locations
    ]
    assert all(not result["is_geocoded"] for result in results)
    assert results[0]["location_precision"] == "area_only"
    assert results[1]["resolution_status"] == "UNRESOLVED_AREA_ONLY"
    assert results[2]["coordinate_origin"] is None
    assert results[3]["coordinate_origin"] is None


def test_invalid_incomplete_and_swapped_coordinates_are_not_corrected_silently():
    incomplete = resolve_record_location({
        "source_type": "news",
        "latitude": 9.91,
        "location": {"raw_text": "Madurai"},
    })
    swapped = resolve_record_location({
        "source_type": "news",
        "latitude": 78.12,
        "longitude": 9.91,
        "location": {"raw_text": "Madurai"},
    })
    assert not incomplete["is_geocoded"]
    assert not swapped["is_geocoded"]
    assert swapped["resolution_status"] == "INVALID_SOURCE_COORDINATES"
    assert "not changed" in swapped["reason"]


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
        "context_records": [],
        "all_records": [incident],
        "mapped_records": [incident],
        "unmapped_records": [],
        "incident_mapped_records": [incident],
        "incident_unmapped_records": [],
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
    resp_map_records = client.get("/api/map/records")
    assert resp_map_records.status_code == 200
    assert len(resp_map_records.json()) == 1
    resp_map_geo = client.get("/api/map/geojson")
    assert resp_map_geo.status_code == 200
    assert len(resp_map_geo.json()["features"]) == 1
    resp_incidents = client.get("/api/incidents")
    assert resp_incidents.status_code == 200
    assert len(resp_incidents.json()) == 1
    assert resp_incidents.json()[0]["record_kind"] == "incident"
    assert client.get("/api/records").status_code == 404
    assert client.get("/").json()["documentation"] == "/docs"
    gis_app_module._cached_dataset = None


def test_map_layer_endpoints_filter_context_without_changing_incident_contract(monkeypatch):
    import app as gis_app_module

    incident = normalize_record(_saved_incident("incident-layer"))
    market = normalize_record({
        "record_id": "market-layer",
        "source_id": "tn_agri_marketing",
        "source_type": "agriculture",
        "record_type": "market_price",
        "title": "Tomato price",
        "event_time": "2026-10-15T00:00:00+00:00",
        "location": {"raw_text": "Anaiyur Uzhavar Santhai, Madurai"},
        "data": {"market": "Anaiyur", "commodity": "Tomato", "min_price": 25, "max_price": 30, "unit": "kg"},
        "source_url": "https://example.test/market",
    })
    news = normalize_record({
        "record_id": "news-layer",
        "source_id": "dinamalar",
        "source_type": "news",
        "record_type": "article",
        "title": "\u0bae\u0ba4\u0bc1\u0bb0\u0bc8 \u0b9a\u0bc6\u0baf\u0bcd\u0ba4\u0bbf",
        "event_time": "2026-10-15T00:00:00+00:00",
        "location": {"raw_text": "Madurai", "district": "Madurai"},
        "data": {"content": "\u0ba4\u0bae\u0bbf\u0bb4\u0bcd \u0b89\u0bb3\u0bcd\u0bb3\u0b9f\u0b95\u0bcd\u0b95\u0bae\u0bcd"},
        "source_url": "https://example.test/news",
    })
    all_records = [incident, market, news]
    assign_dispersed_coordinates(all_records)
    mapped = [item for item in all_records if item["is_geocoded"]]
    unmapped = [item for item in all_records if not item["is_geocoded"]]
    dataset = {
        "all_records": all_records,
        "incident_records": [incident],
        "context_records": [market, news],
        "mapped_records": mapped,
        "unmapped_records": unmapped,
        "incident_mapped_records": [incident],
        "incident_unmapped_records": [],
        "stats": {
            "totalRecords": 1,
            "incidentCount": 1,
            "mappedCount": 1,
            "unmappedCount": 0,
            "commonRecordCount": 2,
            "mapRecordCount": 3,
            "mapMappedCount": 2,
            "mapUnmappedCount": 1,
            "dataSourceType": "live",
            "sourceEndpoints": {},
            "lastRefreshed": "2026-10-15T00:00:00+00:00",
        },
        "data_source_type": "live",
        "source_endpoints": {},
    }
    monkeypatch.setattr(gis_app_module, "load_gis_dataset", lambda: dataset)
    gis_app_module._cached_dataset = None

    map_records = client.get("/api/map/records")
    assert map_records.status_code == 200
    assert {record["record_kind"] for record in map_records.json()} == {"incident", "context"}
    agriculture = client.get("/api/map/records?source_type=agriculture").json()
    assert len(agriculture) == 1
    assert agriculture[0]["record_id"] == "market-layer"
    assert agriculture[0]["source_url"] == "https://example.test/market"
    assert agriculture[0]["details"]["commodity"] == "Tomato"
    assert client.get("/api/map/records?source_type=weather").json() == []
    assert client.get("/api/map/unmapped?source_type=news").json()[0]["record_id"] == "news-layer"
    geojson = client.get("/api/map/geojson?source_type=agriculture").json()
    assert len(geojson["features"]) == 1
    assert geojson["features"][0]["id"] == "context:market-layer"
    assert len(client.get("/api/incidents").json()) == 1
    assert client.get("/api/stats").json()["incidentCount"] == 1
    assert client.get("/api/stats").json()["commonRecordCount"] == 2
    assert "\u0ba4\u0bae\u0bbf\u0bb4\u0bcd" in client.get("/api/map/records").text
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
