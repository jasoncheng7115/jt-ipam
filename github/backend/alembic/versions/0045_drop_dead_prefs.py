"""移除死 code 欄位：user_preferences.default_section_id / dashboard_layout（無 UI、無消費者）。

Revision ID: 0045_drop_dead_prefs
Revises: 0044_floor_plan
Create Date: 2026-05-31 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_drop_dead_prefs"
down_revision: str | None = "0044_floor_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("user_preferences", "dashboard_layout")
    op.drop_column("user_preferences", "default_section_id")


def downgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("default_section_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "user_preferences_default_section_id_fkey",
        "user_preferences", "sections",
        ["default_section_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column(
        "user_preferences",
        sa.Column("dashboard_layout", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
