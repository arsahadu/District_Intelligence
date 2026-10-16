"""Load saved incidents and source records from the backend for GIS."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from config import BACKEND_API_BASE_URL, BACKEND_TIMEOUT_SECONDS
from services.geojson_builder import build_geojson_feature_collection
from services.gis_processor import assign_dispersed_coordinates, normalize_record


class GISDataUnavailable(RuntimeError):
    """Raised when the backend's persisted incident API cannot be reached."""


def fetch_saved_documents() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch intelligence documents while preserving their separate classifications."""
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

    endpoints = {
        "incidents": {"status": "available", "error": None},
    }
    return payload, endpoints


def fetch_saved_incidents() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Backward-compatible helper returning accepted incident-kind records only."""
    documents, endpoints = fetch_saved_documents()
    incidents = [
        document for document in documents
        if str(document.get("record_kind") or "").casefold() == "incident"
    ]
    return incidents, endpoints


def fetch_common_records() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch source records without promoting contextual records to incidents."""
    url = f"{BACKEND_API_BASE_URL.rstrip('/')}/records"
    try:
        response = requests.get(url, timeout=BACKEND_TIMEOUT_SECONDS)
    except requests.Timeout:
        return [], {"records": {"status": "unavailable", "error": "The records request timed out."}}
    except requests.RequestException:
        return [], {"records": {"status": "unavailable", "error": "The records request failed."}}

    if response.status_code != 200:
        return [], {
            "records": {
                "status": "unavailable",
                "error": f"The records service returned HTTP {response.status_code}.",
            },
        }
    try:
        payload = response.json()
    except ValueError:
        return [], {"records": {"status": "unavailable", "error": "The records response was invalid."}}
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        return [], {"records": {"status": "unavailable", "error": "The records response was invalid."}}

    eligible = [
        item for item in payload
        if str(item.get("source_type") or "").casefold() in {"news", "weather", "agriculture"}
        or str(item.get("record_type") or "").casefold() in {"article", "forecast", "market_price"}
    ]
    return eligible, {"records": {"status": "available", "error": None}}


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
        "commonRecordCount": 0,
        "mapRecordCount": len(incidents),
        "mapMappedCount": len(mapped_records),
        "mapUnmappedCount": len(unmapped_records),
        "highSeverityCount": sum(1 for item in incidents if item.get("severity") == "high"),
        "mediumSeverityCount": sum(1 for item in incidents if item.get("severity") == "medium"),
        "activeTaluksCount": len(taluks),
        "dataSourceType": "live",
        "sourceEndpoints": source_endpoints,
        "lastRefreshed": datetime.now(timezone.utc).isoformat(),
    }


def load_gis_dataset() -> Dict[str, Any]:
    """Load incidents and contextual source records as distinct map records."""
    saved_documents, source_endpoints = fetch_saved_documents()
    saved_incidents = [
        document for document in saved_documents
        if str(document.get("record_kind") or "").casefold() == "incident"
    ]
    source_records, common_record_status = fetch_common_records()
    source_endpoints.update(common_record_status)
    source_records_by_id = {
        str(record.get("record_id")): record
        for record in source_records
        if record.get("record_id")
    }
    incidents = []
    for index, document in enumerate(saved_incidents):
        provenance = document.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        linked_record = source_records_by_id.get(str(provenance.get("record_id")))
        incidents.append(normalize_record(
            document,
            index,
            record_kind="incident",
            linked_record=linked_record,
        ))
    contexts = [
        normalize_record(record, index, record_kind="context")
        for index, record in enumerate(source_records)
    ]
    outcomes_by_source_id: Dict[str, List[Dict[str, Any]]] = {}
    for document in saved_documents:
        provenance = document.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        source_record_id = provenance.get("record_id")
        if not source_record_id:
            continue
        kind = str(document.get("record_kind") or "").casefold()
        validation = document.get("validation")
        validation = validation if isinstance(validation, dict) else {}
        validation_state = str(validation.get("state") or "").casefold()
        if kind == "incident":
            outcome = "accepted" if validation_state == "accepted" else "needs_review"
        elif kind == "unresolved" and validation_state and validation_state != "accepted":
            outcome = "needs_review"
        elif kind == "context":
            outcome = "information_only"
        else:
            continue
        outcomes_by_source_id.setdefault(str(source_record_id), []).append({
            "status": outcome,
            "incident_id": document.get("incident_id"),
            "title": document.get("title"),
            "record_kind": kind,
        })
    for record in contexts:
        record["intelligence_outcomes"] = outcomes_by_source_id.get(record["id"], [])
    all_records = incidents + contexts
    assign_dispersed_coordinates(all_records)
    mapped_records = [
        record for record in all_records
        if record.get("is_geocoded")
        and record.get("base_lat") is not None
        and record.get("base_lng") is not None
    ]
    unmapped_records = [
        record for record in all_records
        if not record.get("is_geocoded")
        or record.get("base_lat") is None
        or record.get("base_lng") is None
    ]
    incident_mapped_records = [record for record in mapped_records if record["record_kind"] == "incident"]
    incident_unmapped_records = [record for record in unmapped_records if record["record_kind"] == "incident"]
    stats = _calculate_stats(incidents, incident_mapped_records, incident_unmapped_records, source_endpoints)
    stats.update({
        "commonRecordCount": len(contexts),
        "mapRecordCount": len(all_records),
        "mapMappedCount": len(mapped_records),
        "mapUnmappedCount": len(unmapped_records),
    })
    return {
        "all_records": all_records,
        "mapped_records": mapped_records,
        "unmapped_records": unmapped_records,
        "incident_mapped_records": incident_mapped_records,
        "incident_unmapped_records": incident_unmapped_records,
        "incident_records": incidents,
        "context_records": contexts,
        "stats": stats,
        "geojson": build_geojson_feature_collection(mapped_records),
        "data_source_type": "live",
        "source_endpoints": source_endpoints,
    }
