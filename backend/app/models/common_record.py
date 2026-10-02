from sqlalchemy import Column, String, DateTime, JSON
from sqlalchemy.orm import declarative_base
import datetime

Base = declarative_base()

class CommonRecord(Base):
    __tablename__ = "common_records"

    record_id = Column(String, primary_key=True, unique=True, index=True)
    source_id = Column(String, nullable=False)
    source_type = Column(String, nullable=False)
    record_type = Column(String, nullable=False)
    title = Column(String, nullable=False)

    event_time = Column(DateTime, nullable=True)
    location_raw_text = Column(String, nullable=True)
    district = Column(String, nullable=True)
    state = Column(String, nullable=True)

    data = Column(JSON, nullable=True)
    severity = Column(String, nullable=True)
    status = Column(String, nullable=True)
    source_url = Column(String, nullable=True)

    retrieved_at = Column(DateTime, default=datetime.datetime.utcnow)
    raw_reference = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
