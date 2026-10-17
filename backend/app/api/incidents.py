from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.intelligence_incident import IncidentEvidence, IncidentRecord
from intelligence.contract import Incident, SCHEMA_VERSION

router = APIRouter()

_ALLOWED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "incident_id",
    "provenance",
    "title",
    "description",
    "incident_type",
    "category",
    "department",
    "severity",
    "priority",
    "event_status",
    "event_time",
    "event_time_precision",
    "locations",
    "entities",
    "relationships",
    "evidence",
    "claims",
    "validation",
    "generation",
    "record_kind",
    "context_type",
    "context_facts",
    "source_geography",
}

_ALLOWED_PROVENANCE_FIELDS = {
    "record_id",
    "source_id",
    "source_type",
    "record_type",
    "source_url",
    "raw_reference",
    "retrieved_at",
    "modality",
    "language_hint",
    "district_hint",
    "state_hint",
    "declared_severity",
    "declared_status",
    "declared_event_time",
    "declared_event_time_precision",
    "fields_without_value",
    "input_hash",
    "source_record_id",
    "source_record_url",
    "record_url",
    "source_record_ids",
}


def _as_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


def _normalize_incident_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Incident payload must be an object",
        )

    unknown_fields = sorted(set(document) - _ALLOWED_TOP_LEVEL_FIELDS)
    if unknown_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported Incident field(s): {', '.join(unknown_fields)}",
        )

    schema_version = document.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported Incident schema_version {schema_version!r}; expected {SCHEMA_VERSION!r}",
        )

    incident_id = document.get("incident_id")
    if not isinstance(incident_id, str) or not incident_id.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Incident `incident_id` is required",
        )

    provenance = document.get("provenance")
    if provenance is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Incident `provenance` is required",
        )
    if not isinstance(provenance, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Incident `provenance` must be an object",
        )
    if not provenance.get("record_id") or not provenance.get("source_id") or not provenance.get("source_type"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Incident provenance requires `record_id`, `source_id` and `source_type`",
        )
    if provenance is not None:
        if not isinstance(provenance, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Incident `provenance` must be an object",
            )
        unknown_provenance = sorted(set(provenance) - _ALLOWED_PROVENANCE_FIELDS)
        if unknown_provenance:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported provenance field(s): {', '.join(unknown_provenance)}",
            )

    return document


def _record_from_incident(incident: Incident | dict[str, Any]) -> IncidentRecord:
    document = incident.to_storage_document() if isinstance(incident, Incident) else _normalize_incident_document(incident)
    incident_id = document.get("incident_id")
    if not isinstance(incident_id, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Incident `incident_id` is required",
        )

    record = IncidentRecord(
        incident_id=incident_id,
        schema_version=document.get("schema_version", SCHEMA_VERSION),
        title=document.get("title"),
        description=document.get("description"),
        incident_type=_as_string(document.get("incident_type")) or "unresolved",
        category=_as_string(document.get("category")) or "unresolved",
        department=_as_string(document.get("department")) or "unresolved",
        severity=_as_string(document.get("severity")) or "unresolved",
        priority=_as_string(document.get("priority")),
        event_status=_as_string(document.get("event_status")) or "unknown",
        event_time=_parse_datetime(document.get("event_time")),
        event_time_precision=_as_string(document.get("event_time_precision")) or "unknown",
        document=document,
    )
    evidence_items = document.get("evidence") or []
    record.evidence_items = [
        IncidentEvidence(
            evidence_id=item["evidence_id"],
            record_id=item.get("record_id"),
            source_id=item.get("source_id"),
            source_type=item.get("source_type"),
            source_field=item.get("field"),
            quote=item.get("quote"),
            char_start=item.get("char_start"),
            char_end=item.get("char_end"),
            source_url=item.get("source_url"),
            document=item,
        )
        for item in evidence_items
        if isinstance(item, dict)
    ]
    return record


@router.post("/incidents", status_code=status.HTTP_201_CREATED)
def create_incident(incident: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    document = _normalize_incident_document(incident)
    record = _record_from_incident(document)
    try:
        db.add(record)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Incident {record.incident_id!r} already exists",
        ) from error

    return document


@router.get("/incidents")
def list_incidents(
    incident_type: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    priority: str | None = None,
    event_status: str | None = None,
    district: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be on or before end_date",
        )

    query = select(IncidentRecord)
    for field, value in (
        (IncidentRecord.incident_type, incident_type),
        (IncidentRecord.category, category),
        (IncidentRecord.severity, severity),
        (IncidentRecord.priority, priority),
        (IncidentRecord.event_status, event_status),
    ):
        if value is not None:
            query = query.where(field == value)

    if district is not None:
        query = query.where(IncidentRecord.document.contains({"locations": [{"district": district}]}))
    if start_date is not None:
        start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        query = query.where(IncidentRecord.event_time >= start)
    if end_date is not None:
        end_exclusive = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        query = query.where(IncidentRecord.event_time < end_exclusive)

    records = db.execute(query.order_by(IncidentRecord.created_at.desc())).scalars().all()
    return [record.document for record in records]


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    record = db.get(IncidentRecord, incident_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return record.document
