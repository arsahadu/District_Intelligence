from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class IncidentRecord(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index(
            "ix_incidents_document_gin",
            "document",
            postgresql_using="gin",
            postgresql_ops={"document": "jsonb_path_ops"},
        ),
    )

    incident_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    incident_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    priority: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    event_status: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    event_time_precision: Mapped[str] = mapped_column(String(100), nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    evidence_items: Mapped[list["IncidentEvidence"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class IncidentEvidence(Base):
    __tablename__ = "incident_evidence"

    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.incident_id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_field: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)

    incident: Mapped[IncidentRecord] = relationship(back_populates="evidence_items")
