"""Persist the Intelligence Incident v2.0 contract.

Revision ID: 20261007_000002
Revises: 20241001_000001
Create Date: 2026-10-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261007_000002"
down_revision = "20241001_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(length=255), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("incident_type", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("department", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=100), nullable=False),
        sa.Column("priority", sa.String(length=100), nullable=True),
        sa.Column("event_status", sa.String(length=100), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_time_precision", sa.String(length=100), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("incident_id"),
    )
    for column in ("incident_type", "category", "severity", "priority", "event_status", "event_time"):
        op.create_index(f"ix_incidents_{column}", "incidents", [column], unique=False)
    op.create_index(
        "ix_incidents_document_gin",
        "incidents",
        ["document"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"document": "jsonb_path_ops"},
    )

    op.create_table(
        "incident_evidence",
        sa.Column("incident_id", sa.String(length=255), nullable=False),
        sa.Column("evidence_id", sa.String(length=255), nullable=False),
        sa.Column("record_id", sa.String(length=255), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("source_field", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_incident_evidence_incident_id_incidents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("incident_id", "evidence_id"),
    )
    op.create_index("ix_incident_evidence_record_id", "incident_evidence", ["record_id"])
    op.create_index("ix_incident_evidence_source_id", "incident_evidence", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_incident_evidence_source_id", table_name="incident_evidence")
    op.drop_index("ix_incident_evidence_record_id", table_name="incident_evidence")
    op.drop_table("incident_evidence")
    op.drop_index("ix_incidents_document_gin", table_name="incidents")
    for column in ("event_time", "event_status", "priority", "severity", "category", "incident_type"):
        op.drop_index(f"ix_incidents_{column}", table_name="incidents")
    op.drop_table("incidents")
