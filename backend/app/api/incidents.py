from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.intelligence_incident import IncidentEvidence, IncidentRecord
from intelligence.contract import Incident, SCHEMA_VERSION

router = APIRouter()


def _record_from_incident(incident: Incident) -> IncidentRecord:
    document = incident.to_storage_document()
    record = IncidentRecord(
        incident_id=incident.incident_id,
        schema_version=incident.schema_version,
        title=incident.title,
        description=incident.description,
        incident_type=incident.incident_type.value,
        category=incident.category.value,
        department=incident.department.value,
        severity=incident.severity.value,
        priority=incident.priority.value if incident.priority is not None else None,
        event_status=incident.event_status.value,
        event_time=incident.event_time,
        event_time_precision=incident.event_time_precision.value,
        document=document,
    )
    record.evidence_items = [
        IncidentEvidence(
            evidence_id=evidence.evidence_id,
            record_id=evidence.record_id,
            source_id=evidence.source_id,
            source_type=evidence.source_type,
            source_field=evidence.field,
            quote=evidence.quote,
            char_start=evidence.char_start,
            char_end=evidence.char_end,
            source_url=evidence.source_url,
            document=evidence.model_dump(mode="json"),
        )
        for evidence in incident.evidence
    ]
    return record


@router.post("/incidents", response_model=Incident, status_code=status.HTTP_201_CREATED)
def create_incident(incident: Incident, db: Session = Depends(get_db)) -> Incident:
    if incident.schema_version != SCHEMA_VERSION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported Incident schema_version {incident.schema_version!r}; expected {SCHEMA_VERSION!r}",
        )

    record = _record_from_incident(incident)
    try:
        db.add(record)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Incident {incident.incident_id!r} already exists",
        ) from error

    return incident


@router.get("/incidents", response_model=list[Incident])
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
) -> list[Incident]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    return [Incident.model_validate(record.document) for record in records]


@router.get("/incidents/{incident_id}", response_model=Incident)
def get_incident(incident_id: str, db: Session = Depends(get_db)) -> Incident:
    record = db.get(IncidentRecord, incident_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return Incident.model_validate(record.document)
