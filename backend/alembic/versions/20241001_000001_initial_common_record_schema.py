"""Initial CommonRecord and Source schema

Revision ID: 20241001_000001
Revises: 
Create Date: 2026-10-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20241001_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name="uq_sources_source_id"),
    )
    op.create_index(op.f("ix_sources_id"), "sources", ["id"], unique=False)
    op.create_index(op.f("ix_sources_source_id"), "sources", ["source_id"], unique=True)

    op.create_table(
        "common_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.String(length=255), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("record_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("location_raw_text", sa.Text(), nullable=True),
        sa.Column("district", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=255), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("severity", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=100), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_reference", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.source_id"], name="fk_common_records_source_id_sources"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", name="uq_common_records_record_id"),
    )
    op.create_index(op.f("ix_common_records_id"), "common_records", ["id"], unique=False)
    op.create_index(op.f("ix_common_records_record_id"), "common_records", ["record_id"], unique=True)
    op.create_index(op.f("ix_common_records_source_id"), "common_records", ["source_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_common_records_source_id"), table_name="common_records")
    op.drop_index(op.f("ix_common_records_record_id"), table_name="common_records")
    op.drop_index(op.f("ix_common_records_id"), table_name="common_records")
    op.drop_table("common_records")
    op.drop_index(op.f("ix_sources_source_id"), table_name="sources")
    op.drop_index(op.f("ix_sources_id"), table_name="sources")
    op.drop_table("sources")
