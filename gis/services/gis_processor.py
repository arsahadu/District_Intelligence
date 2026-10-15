"""Normalize live CommonRecords and saved intelligence documents for GIS display."""

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.location_resolver import resolve_record_location

KNOWN_CONTEXT_TYPES = {"agriculture", "weather", "news"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stable_identifier(record: Dict[str, Any], prefix: str) -> tuple[str, bool]:
    value = record.get("record_id") or record.get("incident_id")
    if value:
        return str(value), False
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}", True


def _date_fields(value: Any) -> tuple[str, str]:
    if value is None:
        return "", ""
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.strftime("%d %b %Y"), parsed.date().isoformat()
    except ValueError:
        return raw[:10], raw[:10]


def _known_severity(value: Any) -> Optional[str]:
    if value is None:
        return None
    severity = str(value).strip().casefold()
    return severity if severity in VALID_SEVERITIES else None


def _source_coordinates(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    location = _dict(record.get("location"))
    latitude = record.get("latitude", record.get("lat", location.get("latitude")))
    longitude = record.get("longitude", record.get("lng", location.get("longitude")))
    if latitude is None and longitude is None:
        return None

    def preserve(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            return repr(value)
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    return {
        "latitude": preserve(latitude),
        "longitude": preserve(longitude),
    }


def _location_input(record: Dict[str, Any], linked_record: Dict[str, Any]) -> Dict[str, Any]:
    locations = record.get("locations")
    selected = next((item for item in locations or [] if isinstance(item, dict)), None)
    raw_record = dict(record)
    raw_record["data"] = _dict(linked_record.get("data"))
    raw_record["source_id"] = (
        _dict(record.get("provenance")).get("source_id")
        or linked_record.get("source_id")
    )
    raw_record["source_type"] = (
        _dict(record.get("provenance")).get("source_type")
        or linked_record.get("source_type")
    )

    if selected:
        linked_location = _dict(linked_record.get("location"))
        selected_lat = selected.get("latitude")
        selected_lng = selected.get("longitude")
        if selected_lat is None and selected_lng is None:
            selected_lat = linked_record.get("latitude", linked_record.get("lat", linked_location.get("latitude")))
            selected_lng = linked_record.get("longitude", linked_record.get("lng", linked_location.get("longitude")))
        raw_record["location"] = {
            "raw_text": selected.get("text"),
            "latitude": selected_lat,
            "longitude": selected_lng,
            "canonical_location_id": selected.get("canonical_location_id"),
        }
    elif linked_record:
        raw_record["location"] = linked_record.get("location")
        raw_record["latitude"] = linked_record.get("latitude")
        raw_record["longitude"] = linked_record.get("longitude")
    else:
        raw_record["location"] = None
    return raw_record


def normalize_record(
    raw_record: Dict[str, Any],
    index: int = 0,
    *,
    record_kind: Optional[str] = None,
    linked_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convert a backend source record or saved intelligence document to GIS schema.

    Context records retain source-provided values and never inherit an incident
    classification. Incident-only properties come solely from saved intelligence.
    """
    linked_record = _dict(linked_record)
    provenance = _dict(raw_record.get("provenance"))
    is_saved_document = bool(raw_record.get("incident_id"))
    identifier, generated_id = _stable_identifier(raw_record, "gis-record")

    if is_saved_document:
        kind = record_kind or str(raw_record.get("record_kind") or "unresolved").casefold()
        source_type = str(
            provenance.get("source_type")
            or linked_record.get("source_type")
            or "unknown"
        ).strip().casefold()
        source_record_id = provenance.get("record_id") or linked_record.get("record_id")
        source_id = provenance.get("source_id") or linked_record.get("source_id")
        source_url = (
            provenance.get("source_url")
            or provenance.get("source_record_url")
            or linked_record.get("source_url")
        )
        raw_location = _location_input(raw_record, linked_record)
        source_coordinates = _source_coordinates(raw_location)
        title = raw_record.get("title") or "Untitled intelligence record"
        summary = raw_record.get("description") or ""
        event_time = raw_record.get("event_time") or provenance.get("declared_event_time")
        retrieved_at = provenance.get("retrieved_at") or linked_record.get("retrieved_at")
        incident_id = str(raw_record.get("incident_id"))
        record_id = str(source_record_id) if source_record_id else None
        evidence = [
            item for item in raw_record.get("evidence", [])
            if isinstance(item, dict)
        ]
        source_ids = []
        for item in evidence:
            cited_record_id = item.get("record_id")
            if cited_record_id and cited_record_id not in source_ids:
                source_ids.append(cited_record_id)
        if record_id and record_id not in source_ids:
            source_ids.insert(0, record_id)
        validation = _dict(raw_record.get("validation"))
        review_required = validation.get("state") != "accepted" if kind == "incident" else None
        incident_type = raw_record.get("incident_type")
        category = raw_record.get("category")
        severity = _known_severity(raw_record.get("severity")) if kind == "incident" else None
        status = raw_record.get("event_status") if kind == "incident" else None
        priority = raw_record.get("priority") if kind == "incident" else None
        context_type = raw_record.get("context_type")
        context_facts = raw_record.get("context_facts") or []
        record_type = provenance.get("record_type") or linked_record.get("record_type")
        department = raw_record.get("department")
        event_time_precision = raw_record.get("event_time_precision")
        schema_version = raw_record.get("schema_version")
        claims = raw_record.get("claims") or []
        generation = raw_record.get("generation")
        validation_details = validation
    else:
        kind = "context"
        source_type = str(raw_record.get("source_type") or "unknown").strip().casefold()
        source_id = raw_record.get("source_id")
        record_id = str(raw_record.get("record_id")) if raw_record.get("record_id") else None
        incident_id = None
        source_url = raw_record.get("source_url") or raw_record.get("url")
        title = raw_record.get("title") or "Untitled source record"
        data = _dict(raw_record.get("data"))
        summary = data.get("summary") or data.get("content") or ""
        event_time = raw_record.get("event_time")
        retrieved_at = raw_record.get("retrieved_at")
        raw_location = raw_record
        source_coordinates = _source_coordinates(raw_record)
        evidence = []
        source_ids = [record_id] if record_id else []
        review_required = None
        incident_type = None
        category = source_type if source_type in KNOWN_CONTEXT_TYPES else "other"
        severity = _known_severity(raw_record.get("severity"))
        status = raw_record.get("status")
        priority = None
        context_type = raw_record.get("record_type")
        context_facts = []
        record_type = raw_record.get("record_type")
        department = None
        event_time_precision = None
        schema_version = None
        claims = []
        generation = None
        validation_details = {}
        provenance = {
            "record_id": record_id,
            "source_id": source_id,
            "source_type": source_type,
            "record_type": raw_record.get("record_type"),
            "source_url": source_url,
            "raw_reference": raw_record.get("raw_reference"),
            "retrieved_at": raw_record.get("retrieved_at"),
        }

    resolution = resolve_record_location(raw_location)
    data = _dict(linked_record.get("data")) if is_saved_document else _dict(raw_record.get("data"))
    event_time_text = str(event_time) if event_time is not None else None
    display_date, date_only = _date_fields(event_time)

    raw_location_object = _dict(_dict(raw_location).get("location"))
    selected_locations = raw_record.get("locations") if is_saved_document else None
    if selected_locations:
        first_location = next(
            (item for item in selected_locations if isinstance(item, dict)),
            {},
        )
        raw_location_text = first_location.get("text")
    else:
        raw_location_text = raw_location_object.get("raw_text") or raw_location_object.get("text")
    if raw_location_text is None and not is_saved_document:
        raw_location_text = raw_record.get("raw_location_text")
    if raw_location_text is None:
        raw_location_text = ""

    details = dict(data)
    details.update({
        "context_type": context_type,
        "context_facts": context_facts,
    })
    search_parts = [
        identifier, record_id, incident_id, source_id, source_type, title,
        summary, category, incident_type, status, priority,
        raw_location_text, resolution.get("resolved_name"),
        resolution.get("taluk"), json.dumps(details, ensure_ascii=False, default=str),
    ]
    search_string = " ".join(str(item) for item in search_parts if item).casefold()

    return {
        "id": identifier,
        "record_id": record_id,
        "incident_id": incident_id,
        "record_kind": kind,
        "source_type": source_type,
        "record_type": record_type,
        "source_id": source_id,
        "source_name": source_id or source_type or "Unknown source",
        "title": str(title),
        "summary": str(summary),
        "event_time": event_time_text,
        "retrieved_at": str(retrieved_at) if retrieved_at is not None else None,
        "display_date": display_date,
        "date_only": date_only,
        "category": str(category or "other"),
        "category_label": str(category or "Other").replace("_", " ").title(),
        "incident_type": incident_type,
        "department": department,
        "schema_version": schema_version,
        "event_time_precision": event_time_precision,
        "severity": severity,
        "status": status,
        "priority": priority,
        "review_required": review_required,
        "review_state": validation.get("state") if is_saved_document else None,
        "validation": validation_details,
        "claims": claims,
        "generation": generation,
        "context_type": context_type,
        "context_facts": context_facts,
        "is_incident": kind == "incident",
        "is_geocoded": resolution["is_geocoded"],
        "base_lat": resolution.get("lat"),
        "base_lng": resolution.get("lng"),
        "display_lat": resolution.get("lat"),
        "display_lng": resolution.get("lng"),
        "taluk": resolution.get("taluk") or "Unassigned",
        "location_name": resolution.get("resolved_name"),
        "administrative_level": resolution.get("administrative_level"),
        "raw_location_text": str(raw_location_text),
        "resolution_status": resolution.get("resolution_status"),
        "resolution_reason": resolution.get("reason"),
        "coordinate_origin": resolution.get("coordinate_origin"),
        "source_coordinates": source_coordinates,
        "source_url": source_url,
        "raw_reference": provenance.get("raw_reference") or linked_record.get("raw_reference"),
        "provenance": provenance,
        "location_details": _dict(raw_record.get("location")) if not is_saved_document else {},
        "source_record_ids": source_ids,
        "source_records": [],
        "evidence": evidence,
        "locations": raw_record.get("locations", []) if is_saved_document else [],
        "details": details,
        "search_string": search_string,
        "identifier_is_generated": generated_id,
        "raw": raw_record,
    }


def assign_dispersed_coordinates(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply stable visual offsets to colocated pins without changing base coordinates.

    Dispersed positions are rendering offsets only; base_lat/base_lng remain the
    resolved source or registry coordinates shown in record details.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        lat, lng = record.get("base_lat"), record.get("base_lng")
        if not record.get("is_geocoded") or lat is None or lng is None:
            continue
        key = f"{float(lat):.7f},{float(lng):.7f}"
        groups.setdefault(key, []).append(record)

    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda item: f"{item.get('record_kind')}:{item.get('id')}",
        )
        total = len(ordered)
        for index, record in enumerate(ordered):
            record["co_location_count"] = total
            record["co_location_index"] = index + 1
            if total == 1:
                record["display_lat"] = record["base_lat"]
                record["display_lng"] = record["base_lng"]
                continue
            angle = index * math.pi * (3.0 - math.sqrt(5.0))
            radius_m = 7.0 + 2.0 * math.sqrt(index)
            lat_offset = (radius_m * math.cos(angle)) / 111_320.0
            lng_scale = 111_320.0 * math.cos(math.radians(float(record["base_lat"])))
            lng_offset = (radius_m * math.sin(angle)) / max(lng_scale, 1.0)
            record["display_lat"] = float(record["base_lat"]) + lat_offset
            record["display_lng"] = float(record["base_lng"]) + lng_offset
    return records


def group_agriculture_by_market(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Summarize agriculture source records without inventing missing measurements."""
    market_map: Dict[str, Dict[str, Any]] = {}
    for item in records:
        details = item.get("details") or {}
        market = details.get("market") or item.get("location_name") or "Unknown Market"
        group = market_map.setdefault(market, {
            "market_name": market,
            "taluk": item.get("taluk"),
            "coordinates": [item["base_lat"], item["base_lng"]] if item.get("is_geocoded") else None,
            "is_geocoded": item.get("is_geocoded"),
            "items_count": 0,
            "commodities": [],
            "sample_item": item,
        })
        group["items_count"] += 1
        group["commodities"].append({
            "commodity": details.get("commodity"),
            "min_price": details.get("min_price"),
            "max_price": details.get("max_price"),
            "modal_price": details.get("modal_price"),
            "quantity": details.get("quantity"),
            "unit": details.get("unit"),
            "record_id": item.get("record_id"),
        })
    return list(market_map.values())
