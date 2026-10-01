from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.common_record import CommonRecord
from app.models.source import Source
from app.schemas.common_record import CommonRecordCreate, CommonRecordResponse

router = APIRouter()

@router.post("/records", response_model=CommonRecordResponse)
def create_record(record: CommonRecordCreate, db: Session = Depends(get_db)):
    try:
        existing = db.execute(select(CommonRecord).where(CommonRecord.record_id == record.record_id)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Record already exists")

        source = db.execute(select(Source).where(Source.source_id == record.source_id)).scalar_one_or_none()
        if source is None:
            source = Source(
                source_id=record.source_id,
                name=record.source_id,
                source_type=record.source_type,
                base_url=record.source_url,
                status="active",
            )
            db.add(source)
            db.flush()

        saved = CommonRecord(
            record_id=record.record_id,
            source_id=record.source_id,
            source_type=record.source_type,
            record_type=record.record_type,
            title=record.title,
            event_time=record.event_time,
            location_raw_text=record.location.raw_text,
            district=record.location.district,
            state=record.location.state,
            data=record.data,
            severity=record.severity,
            status=record.status,
            source_url=record.source_url,
            retrieved_at=record.retrieved_at,
            raw_reference=record.raw_reference,
        )
        db.add(saved)
        db.commit()
        db.refresh(saved)
        return CommonRecordResponse.from_orm_record(saved)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Record already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/records", response_model=list[CommonRecordResponse])
def list_records(db: Session = Depends(get_db)):
    records = db.execute(select(CommonRecord).order_by(CommonRecord.created_at.desc())).scalars().all()
    return [CommonRecordResponse.from_orm_record(r) for r in records]

@router.get("/records/{record_id}", response_model=CommonRecordResponse)
def get_record(record_id: str, db: Session = Depends(get_db)):
    record = db.execute(select(CommonRecord).where(CommonRecord.record_id == record_id)).scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    return CommonRecordResponse.from_orm_record(record)
