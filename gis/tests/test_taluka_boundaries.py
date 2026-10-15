"""Tests for Madurai taluka boundaries and saved-incident severity aggregation."""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app import app
from services.taluka_service import (
    TALUKA_BORDER_COLORS,
    TALUKA_SEVERITY_COLORS,
    aggregate_taluka_severity_from_records,
    get_taluka_geojson_with_severity,
    load_taluka_boundaries,
    normalize_taluk_name,
)


def test_taluka_boundaries_dataset_structure():
    """Validates that taluka boundaries contain all 11 official administrative talukas."""
    boundaries = load_taluka_boundaries()
    assert boundaries.get("type") == "FeatureCollection"
    features = boundaries.get("features", [])
    assert len(features) == 11, f"Expected 11 talukas for Madurai District, found {len(features)}"

    names = {f["properties"]["taluk_name"] for f in features}
    expected_taluks = {
        "Madurai North",
        "Madurai South",
        "Madurai East",
        "Madurai West",
        "Melur",
        "Thirumangalam",
        "Thiruparankundram",
        "Usilampatti",
        "Vadipatti",
        "Peraiyur",
        "Kallikudi",
    }
    assert names == expected_taluks, f"Taluk name mismatch: {names.symmetric_difference(expected_taluks)}"

    # Validate coordinate geometries (EPSG:4326 RFC 7946)
    for f in features:
        geom = f.get("geometry", {})
        assert geom.get("type") in ("Polygon", "MultiPolygon")
        coords = geom.get("coordinates")
        assert coords and len(coords) > 0

        # Verify bbox is within Madurai District bounds
        bbox = f["properties"]["bbox"]
        min_lng, min_lat, max_lng, max_lat = bbox
        assert 77.0 <= min_lng <= 79.0, f"Invalid longitude {min_lng} for {f['properties']['taluk_name']}"
        assert 9.0 <= min_lat <= 11.0, f"Invalid latitude {min_lat} for {f['properties']['taluk_name']}"


def test_severity_pastel_colour_palette():
    """Validates strict adherence to light pastel colours and distinct values."""
    assert TALUKA_SEVERITY_COLORS["high"].upper() == "#F4A6A6"
    assert TALUKA_SEVERITY_COLORS["medium"].upper() == "#FFD0A3"
    assert TALUKA_SEVERITY_COLORS["low"].upper() == "#B8E0B8"
    assert TALUKA_SEVERITY_COLORS["unknown"].upper() == "#D9DDE3"

    # Ensure all colors are unique
    assert len(set(TALUKA_SEVERITY_COLORS.values())) == 4


def test_unassessed_taluks_remain_unknown_without_saved_incidents():
    """No demonstration values are shown when there are no persisted incidents."""
    result = get_taluka_geojson_with_severity(records=[])
    assert result["metadata"]["display_mode"] == "aggregated"
    assert all(feature["properties"]["severity"] == "unknown" for feature in result["features"])


def test_incident_aggregation_precedence():
    """Validates highest-severity aggregation over saved incident records."""
    mock_records = [
        {"record_kind": "incident", "taluk": "Madurai North", "severity": "low"},
        {"record_kind": "incident", "taluk": "Madurai North", "severity": "high"},
        {"record_kind": "incident", "taluk": "Melur", "severity": "medium"},
        {"record_kind": "incident", "taluk": "Melur", "severity": "low"},
        {"record_kind": "incident", "taluk": "Vadipatti", "severity": "low"},
        {"record_kind": "incident", "taluk": "Vadipatti", "severity": "routine"},
        {"record_kind": "forecast", "taluk": "Melur", "severity": "critical"},
    ]

    agg = aggregate_taluka_severity_from_records(mock_records)
    assert agg["Madurai North"]["severity"] == "high"
    assert agg["Melur"]["severity"] == "medium"
    assert agg["Vadipatti"]["severity"] == "low"
    assert "Peraiyur" not in agg  # Untouched taluks remain unassessed (unknown)


def test_taluka_api_endpoint(monkeypatch):
    """Taluka boundaries are served by the API without synthetic severity data."""
    import app as gis_app_module

    monkeypatch.setattr(
        gis_app_module,
        "load_gis_dataset",
        lambda: {
            "incident_records": [],
            "stats": {"dataSourceType": "live", "lastRefreshed": None},
        },
    )
    gis_app_module._cached_dataset = None
    client = TestClient(app)
    resp = client.get("/api/talukas")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 11
    assert data["metadata"]["display_mode"] == "aggregated"
    assert all(feature["properties"]["severity"] == "unknown" for feature in data["features"])
    assert client.get("/api/talukas?mode=sample").status_code == 422
    gis_app_module._cached_dataset = None
