from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field

class LocationSchema(BaseModel):
    raw_text: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None

class CommonRecordCreate(BaseModel):
    record_id: str
    source_id: str
    source_type: str
    record_type: str
    title: str
    event_time: Optional[datetime] = None   # accepts ISO string
    location: LocationSchema
    data: dict[str, Any] = Field(default_factory=dict)
    severity: Optional[str] = None
    status: Optional[str] = None
    source_url: Optional[str] = None
    retrieved_at: datetime
    raw_reference: Optional[str] = None

class CommonRecordResponse(CommonRecordCreate):
    @classmethod
    def from_orm_record(cls, record: Any) -> "CommonRecordResponse":
        return cls(
            record_id=record.record_id,
            source_id=record.source_id,
            source_type=record.source_type,
            record_type=record.record_type,
            title=record.title,
            event_time=record.event_time,
            location=LocationSchema(
                raw_text=record.location_raw_text,
                district=record.district,
                state=record.state,
            ),
            data=record.data or {},
            severity=record.severity,
            status=record.status,
            source_url=record.source_url,
            retrieved_at=record.retrieved_at,
            raw_reference=record.raw_reference,
        )
