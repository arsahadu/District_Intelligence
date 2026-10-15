"""Load persisted incidents from the backend and prepare them for GIS."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from config import BACKEND_API_BASE_URL, BACKEND_TIMEOUT_SECONDS
from services.geojson_builder import build_geojson_feature_collection
from services.gis_processor import assign_dispersed_coordinates, normalize_record


class GISDataUnavailable(RuntimeError):
    """Raised when the backend's persisted incident API cannot be reached."""


def fetch_saved_incidents() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch persisted intelligence documents and retain classified incidents only."""
    url = f"{BACKEND_API_BASE_URL.rstrip('/')}/incidents"
    try:
        response = requests.get(url, timeout=BACKEND_TIMEOUT_SECONDS)
    except requests.Timeout as error:
        raise GISDataUnavailable("Incidents API request timed out.") from error
    except requests.RequestException as error:
        raise GISDataUnavailable("Incidents API request failed.") from error

    if response.status_code != 200:
        raise GISDataUnavailable(f"Incidents API returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as error:
        raise GISDataUnavailable("Incidents API returned invalid JSON.") from error
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise GISDataUnavailable("Incidents API response must be a JSON list of objects.")

    incidents = [
        document for document in payload
        if str(document.get("record_kind") or "").casefold() == "incident"
    ]
    endpoints = {
        "incidents": {"status": "available", "error": None},
    }
    return incidents, endpoints


def _calculate_stats(
    incidents: List[Dict[str, Any]],
    mapped_records: List[Dict[str, Any]],
    unmapped_records: List[Dict[str, Any]],
    source_endpoints: Dict[str, Any],
) -> Dict[str, Any]:
    taluks = {
        record.get("taluk")
        for record in mapped_records
        if record.get("taluk") not in (None, "", "Unassigned", "Unknown", "District-wide")
    }
    return {
        "totalRecords": len(incidents),
        "incidentCount": len(incidents),
        "mappedCount": len(mapped_records),
        "unmappedCount": len(unmapped_records),
        "incidentMappedCount": len(mapped_records),
        "incidentUnmappedCount": len(unmapped_records),
        "highSeverityCount": sum(1 for item in incidents if item.get("severity") == "high"),
        "mediumSeverityCount": sum(1 for item in incidents if item.get("severity") == "medium"),
        "activeTaluksCount": len(taluks),
        "dataSourceType": "live",
        "sourceEndpoints": source_endpoints,
        "lastRefreshed": datetime.now(timezone.utc).isoformat(),
    }


def load_gis_dataset() -> Dict[str, Any]:
    """Load only incidents already persisted by the intelligence pipeline."""
    saved_incidents, source_endpoints = fetch_saved_incidents()
    incidents = [
        normalize_record(document, index, record_kind="incident")
        for index, document in enumerate(saved_incidents)
    ]
    assign_dispersed_coordinates(incidents)
    mapped_records = [
        record for record in incidents
        if record.get("is_geocoded")
        and record.get("base_lat") is not None
        and record.get("base_lng") is not None
    ]
    unmapped_records = [
        record for record in incidents
        if not record.get("is_geocoded")
        or record.get("base_lat") is None
        or record.get("base_lng") is None
    ]
    stats = _calculate_stats(incidents, mapped_records, unmapped_records, source_endpoints)
    return {
        "all_records": incidents,
        "mapped_records": mapped_records,
        "unmapped_records": unmapped_records,
        "incident_records": incidents,
        "stats": stats,
        "geojson": build_geojson_feature_collection(mapped_records),
        "data_source_type": "live",
        "source_endpoints": source_endpoints,
    }
