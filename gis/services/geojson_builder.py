"""Madurai District GIS Intelligence Platform - GeoJSON Builder Service.

Generates RFC 7946 compliant GeoJSON FeatureCollection outputs for all mapped
incident entities in Madurai District.
"""

import math
from typing import Any, Dict, List


def build_incident_feature(record: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a mapped GIS record into an RFC 7946 GeoJSON point feature."""
    display_lat = record.get("display_lat")
    display_lng = record.get("display_lng")

    if display_lat is None or display_lng is None:
        return {}
    try:
        display_lat = float(display_lat)
        display_lng = float(display_lng)
    except (TypeError, ValueError):
        return {}
    if (
        not math.isfinite(display_lat)
        or not math.isfinite(display_lng)
        or not -90 <= display_lat <= 90
        or not -180 <= display_lng <= 180
    ):
        return {}

    return {
        "type": "Feature",
        "id": f"{record.get('record_kind') or 'record'}:{record.get('id')}",
        "geometry": {
            "type": "Point",
            "coordinates": [display_lng, display_lat],
        },
        "properties": {
            "id": record.get("id"),
            "record_id": record.get("record_id"),
            "incident_id": record.get("incident_id"),
            "record_kind": record.get("record_kind"),
            "source_type": record.get("source_type"),
            "source_id": record.get("source_id"),
            "record_type": record.get("record_type"),
            "source_name": record.get("source_name"),
            "title": record.get("title"),
            "summary": record.get("summary"),
            "event_time": record.get("event_time"),
            "display_date": record.get("display_date"),
            "date_only": record.get("date_only"),
            "category": record.get("category"),
            "category_label": record.get("category_label"),
            "incident_type": record.get("incident_type"),
            "severity": record.get("severity"),
            "status": record.get("status"),
            "priority": record.get("priority"),
            "review_required": record.get("review_required"),
            "review_state": record.get("review_state"),
            "taluk": record.get("taluk"),
            "location_name": record.get("location_name"),
            "administrative_level": record.get("administrative_level"),
            "location_precision": record.get("location_precision"),
            "raw_location_text": record.get("raw_location_text"),
            "base_lat": record.get("base_lat"),
            "base_lng": record.get("base_lng"),
            "display_lat": display_lat,
            "display_lng": display_lng,
            "co_location_count": record.get("co_location_count", 1),
            "co_location_index": record.get("co_location_index", 1),
            "source_url": record.get("source_url"),
            "resolution_status": record.get("resolution_status"),
            "resolution_reason": record.get("resolution_reason"),
            "coordinate_origin": record.get("coordinate_origin"),
            "source_coordinates": record.get("source_coordinates"),
            "source_record_ids": record.get("source_record_ids", []),
        },
    }


def build_geojson_feature_collection(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a GeoJSON FeatureCollection from mapped incident and context records."""
    features = []
    for r in records:
        if r.get("is_geocoded") and r.get("display_lat") is not None and r.get("display_lng") is not None:
            feat = build_incident_feature(r)
            if feat:
                features.append(feat)

    return {
        "type": "FeatureCollection",
        "metadata": {
            "district": "Madurai",
            "state": "Tamil Nadu",
            "feature_count": len(features),
        },
        "features": features,
    }
