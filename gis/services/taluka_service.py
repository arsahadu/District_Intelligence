"""Madurai District GIS API - Taluka boundary and severity service.

Manages official Madurai District administrative taluka polygons and applies
severity-based pastel colouring for situational awareness.

Supports:
- Severity aggregation from persisted incident records.
"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DATA_DIR

# ---------------------------------------------------------------------------
# 1. Official Pastel Severity Colour Palette
# Light, soft pastel fills that preserve base map visibility underneath
# ---------------------------------------------------------------------------
TALUKA_SEVERITY_COLORS = {
    "high": "#F4A6A6",      # Light pastel red / soft pink
    "medium": "#FFD0A3",    # Light pastel orange / peach
    "low": "#B8E0B8",       # Light pastel green
    "unknown": "#D9DDE3",   # Light neutral grey
}

TALUKA_BORDER_COLORS = {
    "high": "#E57373",      # Soft darker red
    "medium": "#FFB74D",    # Soft darker orange
    "low": "#81C784",       # Soft darker green
    "unknown": "#94A3B8",   # Soft darker grey
}

TALUKA_SEVERITY_LABELS = {
    "high": "High Severity",
    "medium": "Medium Severity",
    "low": "Low Severity",
    "unknown": "Unknown / Unassessed",
}

DEFAULT_TALUKA_MODE = "aggregated"

# ---------------------------------------------------------------------------
# 2. Polygon Boundary Loader
# Loads official geoBoundaries administrative boundaries for Madurai
# ---------------------------------------------------------------------------
_cached_boundaries: Optional[Dict[str, Any]] = None


def load_taluka_boundaries(force_reload: bool = False) -> Dict[str, Any]:
    """Loads and caches the official Madurai District taluka boundaries GeoJSON."""
    global _cached_boundaries
    if _cached_boundaries is not None and not force_reload:
        return _cached_boundaries

    boundary_file = DATA_DIR / "madurai_talukas.geojson"
    if not boundary_file.exists():
        return {"type": "FeatureCollection", "features": []}

    try:
        with open(boundary_file, "r", encoding="utf-8") as f:
            boundaries = json.load(f)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read taluka boundary GeoJSON: {error}") from error

    if not isinstance(boundaries, dict) or boundaries.get("type") != "FeatureCollection":
        raise ValueError("Taluka boundary file must be a GeoJSON FeatureCollection.")
    if boundaries.get("crs") is not None:
        raise ValueError("Taluka boundaries must use RFC 7946 WGS84 coordinates without a legacy CRS field.")
    features = boundaries.get("features")
    if not isinstance(features, list):
        raise ValueError("Taluka boundary FeatureCollection must contain a features list.")

    def validate_position(position: Any) -> None:
        if not isinstance(position, list) or len(position) < 2:
            raise ValueError("Taluka boundary contains an invalid coordinate position.")
        try:
            longitude, latitude = float(position[0]), float(position[1])
        except (TypeError, ValueError) as error:
            raise ValueError("Taluka boundary coordinates must be numeric [longitude, latitude].") from error
        if (
            not math.isfinite(longitude)
            or not math.isfinite(latitude)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            raise ValueError("Taluka boundary coordinates are outside valid GeoJSON ranges.")
        if not 77 <= longitude <= 79 or not 9 <= latitude <= 11:
            raise ValueError("Taluka boundary coordinate is outside the Madurai District extent.")

    def validate_ring(ring: Any) -> None:
        if not isinstance(ring, list) or len(ring) < 4:
            raise ValueError("Taluka polygon rings must contain at least four positions.")
        for position in ring:
            validate_position(position)
        if ring[0][:2] != ring[-1][:2]:
            raise ValueError("Taluka polygon rings must be closed.")

    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError("Taluka boundary entries must be GeoJSON Features.")
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not properties.get("taluk_name"):
            raise ValueError("Every taluka boundary must have a taluk_name property.")
        if not isinstance(geometry, dict):
            raise ValueError("Taluka boundary Feature has no geometry.")
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "Polygon":
            polygons = [coordinates]
        elif geometry_type == "MultiPolygon":
            polygons = coordinates
        else:
            raise ValueError(f"Unsupported taluka geometry type: {geometry_type!r}.")
        if not isinstance(polygons, list) or not polygons:
            raise ValueError("Taluka boundary geometry must contain polygon rings.")
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError("Taluka polygons must contain at least one ring.")
            for ring in polygon:
                validate_ring(ring)

    _cached_boundaries = boundaries
    return _cached_boundaries


# ---------------------------------------------------------------------------
# 4. Aggregation Logic (Highest Confirmed Severity Rule)
# ---------------------------------------------------------------------------
def aggregate_taluka_severity_from_records(
    records: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Calculates authoritative taluka severity from confirmed mapped records.

    Priority rule: High > Medium > Low / Routine > Unknown.
    Does not modify any original record data.
    """
    severity_order = {"high": 3, "medium": 2, "low": 1, "routine": 1, "unknown": 0}

    taluk_aggregation: Dict[str, Dict[str, Any]] = {}

    for r in records:
        if "record_kind" in r and r.get("record_kind") != "incident":
            continue
        taluk = r.get("taluk")
        if not taluk or taluk in ("Unassigned", "Unknown", "District-wide", "Unresolved"):
            continue

        std_taluk = normalize_taluk_name(taluk)
        if std_taluk not in taluk_aggregation:
            taluk_aggregation[std_taluk] = {
                "highest_rank": -1,
                "severity": "unknown",
                "incident_count": 0,
                "high_count": 0,
                "medium_count": 0,
                "low_count": 0,
            }

        rec_sev = str(r.get("severity") or "unknown").lower()
        rank = severity_order.get(rec_sev, 0)
        entry = taluk_aggregation[std_taluk]
        entry["incident_count"] += 1

        if rec_sev == "high":
            entry["high_count"] += 1
        elif rec_sev == "medium":
            entry["medium_count"] += 1
        else:
            entry["low_count"] += 1

        if rank > entry["highest_rank"]:
            entry["highest_rank"] = rank
            if rank == 3:
                entry["severity"] = "high"
            elif rank == 2:
                entry["severity"] = "medium"
            elif rank == 1:
                entry["severity"] = "low"
            else:
                entry["severity"] = "unknown"

    return taluk_aggregation


def normalize_taluk_name(name: str) -> str:
    """Normalizes variations in taluk spelling for reliable matching."""
    n = name.strip()
    lower = n.lower().replace("  ", " ")
    mapping = {
        "madurai north": "Madurai North",
        "madurai south": "Madurai South",
        "madurai east": "Madurai East",
        "madurai west": "Madurai West",
        "melur": "Melur",
        "thirumangalam": "Thirumangalam",
        "tirumangalam": "Thirumangalam",
        "thiruparankundram": "Thiruparankundram",
        "thirupparankundram": "Thiruparankundram",
        "usilampatti": "Usilampatti",
        "vadipatti": "Vadipatti",
        "vadippatti": "Vadipatti",
        "peraiyur": "Peraiyur",
        "kallikudi": "Kallikudi",
        "kalligudi": "Kallikudi",
    }
    return mapping.get(lower, n)


# ---------------------------------------------------------------------------
# 5. Core Interface: Get Taluka GeoJSON with Severity & Styling
# ---------------------------------------------------------------------------
def get_taluka_geojson_with_severity(
    mode: Optional[str] = None,
    records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Generates an RFC 7946 GeoJSON FeatureCollection of Madurai talukas

    with pastel severity styling and comprehensive provenance metadata.

    Args:
        mode: 'aggregated' (from persisted incident records)
        records: Optional list of incident records for 'aggregated' mode.

    Returns:
        GeoJSON FeatureCollection dict with styling and inspection metadata.
    """
    mode = (mode or DEFAULT_TALUKA_MODE).lower()
    if mode != "aggregated":
        raise ValueError("Taluka severity supports only aggregation from persisted incidents.")
    base_boundaries = load_taluka_boundaries()

    features_with_severity: List[Dict[str, Any]] = []

    aggregated_map: Dict[str, Dict[str, Any]] = {}
    if records:
        aggregated_map = aggregate_taluka_severity_from_records(records)

    for feat in base_boundaries.get("features", []):
        raw_name = feat.get("properties", {}).get("taluk_name", "")
        std_name = normalize_taluk_name(raw_name)

        severity = "unknown"
        severity_source = "Aggregated Incident Highest-Confirmed-Severity Rule"
        audit_note = "No mapped incident records registered in this taluka."
        incident_stats = None

        if mode == "aggregated":
            if std_name in aggregated_map:
                agg = aggregated_map[std_name]
                severity = agg["severity"]
                incident_stats = {
                    "total": agg["incident_count"],
                    "high": agg["high_count"],
                    "medium": agg["medium_count"],
                    "low": agg["low_count"],
                }
                audit_note = (
                    f"Aggregated from {agg['incident_count']} saved incidents "
                    f"(High: {agg['high_count']}, Medium: {agg['medium_count']}, "
                    f"Low: {agg['low_count']})"
                )

        # Validate severity key
        if severity not in TALUKA_SEVERITY_COLORS:
            severity = "unknown"

        fill_color = TALUKA_SEVERITY_COLORS[severity]
        border_color = TALUKA_BORDER_COLORS[severity]
        label = TALUKA_SEVERITY_LABELS[severity]

        # Clone and enrich feature properties
        enriched_props = dict(feat.get("properties", {}))
        enriched_props.update({
            "taluk_name": std_name,
            "severity": severity,
            "severity_label": label,
            "fill_color": fill_color,
            "border_color": border_color,
            "fill_opacity": 0.38,  # Soft transparency ensuring basemap readability
            "border_weight": 1.5,
            "severity_source": severity_source,
            "audit_note": audit_note,
            "incident_stats": incident_stats,
        })

        features_with_severity.append({
            "type": "Feature",
            "id": feat.get("id"),
            "properties": enriched_props,
            "geometry": feat.get("geometry"),
        })

    return {
        "type": "FeatureCollection",
        "metadata": {
            "title": "Madurai District Taluka Administrative Boundaries",
            "total_taluks": len(features_with_severity),
            "display_mode": mode,
            "palette": TALUKA_SEVERITY_COLORS,
            "description": "Administrative polygons with severity styling derived from saved incidents.",
        },
        "features": features_with_severity,
    }
