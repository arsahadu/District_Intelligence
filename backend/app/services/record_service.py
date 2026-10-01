from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.common_record import CommonRecord
from app.models.source import Source


def ensure_source_exists(db: Session, source_id: str, source_type: str, name: str | None = None) -> Source:
    source = db.execute(select(Source).where(Source.source_id == source_id)).scalar_one_or_none()
    if source is not None:
        return source

    new_source = Source(
        source_id=source_id,
        name=name or source_id,
        source_type=source_type,
        base_url=None,
        status="active",
    )
    db.add(new_source)
    db.flush()
    return new_source


def create_common_record(db: Session, payload: dict[str, Any]) -> CommonRecord:
    source = ensure_source_exists(
        db,
        source_id=payload["source_id"],
        source_type=payload["source_type"],
        name=payload.get("source_name"),
    )

    record = CommonRecord(
        record_id=payload["record_id"],
        source_id=source.source_id,
        source_type=payload["source_type"],
        record_type=payload["record_type"],
        title=payload["title"],
        event_time=payload.get("event_time"),
        location_raw_text=(payload.get("location") or {}).get("raw_text"),
        district=(payload.get("location") or {}).get("district"),
        state=(payload.get("location") or {}).get("state"),
        data=payload.get("data") or {},
        severity=payload.get("severity"),
        status=payload.get("status"),
        source_url=payload.get("source_url"),
        retrieved_at=payload["retrieved_at"],
        raw_reference=payload.get("raw_reference"),
    )

    db.add(record)
    try:
        db.commit()
        db.refresh(record)
        return record
    except IntegrityError:
        db.rollback()
        raise


def list_common_records(db: Session) -> list[CommonRecord]:
    return db.execute(select(CommonRecord).order_by(CommonRecord.created_at.desc())).scalars().all()


def get_common_record_by_id(db: Session, record_id: str) -> CommonRecord | None:
    return db.execute(select(CommonRecord).where(CommonRecord.record_id == record_id)).scalar_one_or_none()


def list_sources(db: Session) -> list[Source]:
    return db.execute(select(Source).order_by(Source.created_at.desc())).scalars().all()


def create_source(db: Session, payload: dict[str, Any]) -> Source:
    source = Source(
        source_id=payload["source_id"],
        name=payload["name"],
        source_type=payload["source_type"],
        base_url=payload.get("base_url"),
        status=payload.get("status", "active"),
    )
    db.add(source)
    try:
        db.commit()
        db.refresh(source)
        return source
    except IntegrityError:
        db.rollback()
        raise
