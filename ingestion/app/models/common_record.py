from pydantic import BaseModel, Field
from typing import Any, Optional


class Location(BaseModel):
    raw_text: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None


class CommonRecord(BaseModel):
    record_id: str

    source_id: str
    source_type: str
    record_type: str

    title: str

    event_time: Optional[str] = None

    location: Location

    data: dict[str, Any] = Field(default_factory=dict)

    severity: Optional[str] = None
    status: Optional[str] = None

    source_url: Optional[str] = None

    retrieved_at: str

    raw_reference: Optional[str] = None