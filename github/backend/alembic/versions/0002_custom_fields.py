"""Add custom_field_definitions table.

Revision ID: 0002_custom_fields
Revises: 0001_initial
Create Date: 2026-05-09 00:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_custom_fields"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("label_zh_tw", sa.Text()),
        sa.Column("label_en_us", sa.Text()),
        sa.Column("field_type", sa.String(16), nullable=False),
        sa.Column("options", postgresql.JSONB()),
        sa.Column("validation_regex", sa.Text()),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "object_type IN ('subnet','ip','device')",
            name="ck_custom_field_definitions_cf_object_type_valid",
        ),
        sa.CheckConstraint(
            "field_type IN ('text','int','float','bool','date','select','multi_select','regex')",
            name="ck_custom_field_definitions_cf_field_type_valid",
        ),
        sa.UniqueConstraint("object_type", "name", name="cf_object_name_uq"),
    )
    op.create_index(
        "ix_custom_field_definitions_object_type",
        "custom_field_definitions",
        ["object_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_custom_field_definitions_object_type",
                  table_name="custom_field_definitions")
    op.drop_table("custom_field_definitions")
